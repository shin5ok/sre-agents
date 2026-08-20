"""ワークフローグラフの関数ノード。

決定的に決めたい処理(起票・承認ゲート・復旧操作の実行・対応ログ記録)は
すべてここに置く。LLM に残すのは「聞き取り」と「原因調査」の判断だけで、
どのツールをいつ呼ぶかはグラフのエッジが決める。

app/tools/ のツールは一切変更せずにそのまま呼び出す。ADK 2.x の Context は
ツールが使う ``state`` インターフェース(``__getitem__`` / ``__setitem__`` /
``get``)をそのまま備えているため、``tool_context=ctx`` で渡せる。
これにより create_support_case の起票前レビュー確認や
restart_server / escalate_to_engineer の承認確認といったツール層の防御が
グラフの承認ゲートとは独立に生き続ける(defense-in-depth)。
"""

from collections.abc import Generator
from typing import Any

from google.adk import Context, Event
from google.adk.events import RequestInput
from google.genai import types

from app.graph.preapproval import detect_pre_approval, is_affirmative
from app.graph.schemas import (
    ActionResult,
    CaseFiled,
    Exchange,
    IntakeResult,
    RecoveryPlan,
    Transcript,
)
from app.mock_world import SYSTEMS
from app.tools.case_tools import (
    create_support_case,
    propose_case_draft,
    update_case_status,
)
from app.tools.knowledge_tools import record_resolution_log
from app.tools.remediation_tools import (
    escalate_to_engineer,
    propose_recovery_plan,
    restart_server,
)

# 障害報告がまだ無いときに返す固定のウェルカムメッセージ。
# LLM に生成させると文面が揺れるため、定型文はグラフ側の定数にする。
WELCOME_MESSAGE = """\
# SRE エージェントへようこそ

私は障害受付エージェントです。本部の各システムで発生した障害の
一次受付を担当しています。ご報告いただいた内容をもとに、調査エージェントが
原因調査・復旧対応(必要に応じてエスカレーション)まで進めます。

## 障害報告に必要な項目
1. 店舗ID と 店舗名(例: 店舗ID 1102 / 渋谷店)
2. 対象システム(例: 売上管理システム / 店舗アプリ 等)
3. 具体的な症状(エラーメッセージ、画面の状態、できなくなった操作)
4. 発生日時(いつから起きているか)
5. 影響範囲(何台の端末か、他店舗でも起きているか、どの業務に支障か)
6. 報告者名

上記が揃っていなくても、まずはご状況をお知らせください。
不足があればこちらから順にお伺いします。"""

_DEFAULT_QUESTION = "障害の状況をお知らせください。"


# --- 補助関数 ---------------------------------------------------------------


def _as_text(value: Any) -> str:
    """ノード入力(ユーザーの回答)を素直な文字列に変換する。

    RequestInput の応答はクライアント実装によって str / dict / Content の
    いずれでも渡ってくるため、ここで吸収する。
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, types.Content):
        return "".join(p.text for p in (value.parts or []) if p.text).strip()
    if isinstance(value, dict):
        for key in ("result", "answer", "text", "message", "response"):
            if key in value:
                return _as_text(value[key])
        return str(value).strip()
    return str(value).strip()


def _system_choices() -> str:
    """管理対象システムの一覧を選択肢テキストにする。"""
    return "\n".join(
        f"- {entry['display_name']}(system_id: {sid})" for sid, entry in SYSTEMS.items()
    )


def _transcript(ctx: Context) -> Transcript:
    """state に積み上げた聞き取り記録を Transcript に組み直す。"""
    exchanges = [
        Exchange(
            question=str(e.get("question") or ""), answer=str(e.get("answer") or "")
        )
        for e in (ctx.state.get("interview_log") or [])
    ]
    return Transcript(
        initial_report=str(ctx.state.get("initial_report") or ""),
        exchanges=exchanges,
    )


def _refresh_pre_approval(ctx: Context) -> None:
    """初回報告とこれまでの回答すべてから事前承認フラグを再判定する。

    後の発言で「復旧作業は承認を得てから」と言われた場合に取り消せるよう、
    差分ではなく全文に対して毎回判定し直す。
    """
    texts = [str(ctx.state.get("initial_report") or "")]
    texts.extend(
        str(e.get("answer") or "") for e in (ctx.state.get("interview_log") or [])
    )
    approval = detect_pre_approval("\n".join(texts))
    ctx.state["intake_pre_approved"] = approval.intake
    ctx.state["recovery_pre_approved"] = approval.recovery


def _intake(ctx: Context) -> IntakeResult:
    """state に退避した受付エージェントの出力を取り出す。"""
    return IntakeResult.model_validate(
        ctx.state.get("intake") or {"status": "need_more_info"}
    )


def _case(ctx: Context) -> CaseFiled:
    """state に退避した起票済みケースを取り出す。"""
    return CaseFiled.model_validate(ctx.state.get("case") or {"case_id": ""})


def _plan(ctx: Context) -> RecoveryPlan:
    """state に退避した復旧計画を取り出す。"""
    return RecoveryPlan.model_validate(ctx.state.get("plan") or {})


def _mark(ctx: Context, step: str) -> None:
    """通過したノードの回数を state に記録する。

    HITL ゲートの interrupt_id("intake-2" 等)に使う周回数がこれ。後退エッジで
    同じノードを何度も通るため、停止ごとに一意な id を振るのに必要。

    eval のトラジェクトリ判定には使えない: agents-cli はトレースに author と
    content しか載せず、state_delta は落とすため(判定は
    tests/eval/eval_config.yaml のコメント参照)。デバッグ時の通過確認には使える。
    """
    ctx.state[f"step_{step}"] = int(ctx.state.get(f"step_{step}") or 0) + 1


def _ask(ctx: Context, question: str) -> None:
    """次に ask_reporter が提示する質問文を state に置く。"""
    ctx.state["pending_question"] = question


def _say(text: str) -> types.Content:
    """関数ノードの発話をモデル発話(role="model")として組み立てる。

    ``Event(message="...")`` に素の文字列を渡すと role="user" の Content に
    なり、UI でも eval の応答抽出でもエージェントの発話として扱われない。
    関数ノードが報告者・担当者に見せる本文は必ずこの関数を通す。
    """
    return types.Content(role="model", parts=[types.Part(text=text)])


# --- 受付フェーズ -----------------------------------------------------------


def detect_intent(ctx: Context, node_input: str) -> str:
    """初回の障害報告を記録し、事前承認フラグを決定的に判定する。

    承認ゲートを迂回してよいかどうかの判断に LLM を介在させない。
    """
    report = (node_input or "").strip()
    ctx.state["initial_report"] = report
    ctx.state["interview_log"] = []
    ctx.state["pending_question"] = ""
    _refresh_pre_approval(ctx)
    _mark(ctx, "detect_intent")
    return _transcript(ctx).render()


def intake_router(ctx: Context, node_input: IntakeResult) -> Event:
    """受付エージェントの結果を見て、聞き取り継続か起票へ進むかを決める。

    LLM が status="ready" と言っても、必須項目とシステムIDは必ず自前で検証する。
    """
    intake = node_input
    ctx.state["intake"] = intake.model_dump()
    _mark(ctx, "intake_router")

    missing = intake.missing_fields()
    unknown_system = bool(intake.system_id) and intake.system_id not in SYSTEMS

    if intake.status == "ready" and not missing and not unknown_system:
        return Event(route="READY")

    _ask(ctx, _build_question(ctx, intake, missing, unknown_system))
    return Event(route="ASK")


def _build_question(
    ctx: Context,
    intake: IntakeResult,
    missing: list[str],
    unknown_system: bool,
) -> str:
    """報告者に返す質問文(または初回のウェルカム文)を組み立てる。"""
    has_report = bool(str(ctx.state.get("initial_report") or "").strip())
    if intake.status == "welcome" and not has_report:
        return WELCOME_MESSAGE

    parts: list[str] = []
    message = intake.message.strip()
    if message:
        parts.append(message)
    elif missing:
        parts.append("恐れ入りますが、以下の項目をお知らせください。")
        parts.append("\n".join(f"{i}. {label}" for i, label in enumerate(missing, 1)))

    if unknown_system:
        parts.append("対象システムを次の中からお選びください。\n" + _system_choices())
    return "\n\n".join(parts) or _DEFAULT_QUESTION


def ask_reporter(ctx: Context) -> Generator[Event | RequestInput, None, None]:
    """報告者に質問を提示し、ワークフローを停止して回答を待つ。"""
    question = str(ctx.state.get("pending_question") or _DEFAULT_QUESTION)
    _mark(ctx, "ask_reporter")
    round_no = ctx.state.get("step_ask_reporter")
    # RequestInput を先に出すのは意図的。停止時の最後のイベントが
    # adk_request_input の function_call だけになると、テキストを持つイベントが
    # 末尾に無くなり、eval 側の応答抽出(最後のイベントの parts[0].text)が落ちる。
    yield RequestInput(message=question, interrupt_id=f"intake-{round_no}")
    yield Event(message=_say(question))


def collect_answer(ctx: Context, node_input: Any) -> str:
    """報告者の回答を聞き取り記録に追記し、受付エージェントへ渡す入力を作る。"""
    answer = _as_text(node_input)
    question = str(ctx.state.get("pending_question") or "")

    if not str(ctx.state.get("initial_report") or "").strip():
        # ウェルカム応答の直後。この回答が実質の初回報告になる。
        ctx.state["initial_report"] = answer
    else:
        log = list(ctx.state.get("interview_log") or [])
        log.append({"question": question, "answer": answer})
        ctx.state["interview_log"] = log

    ctx.state["pending_question"] = ""
    _refresh_pre_approval(ctx)
    _mark(ctx, "collect_answer")
    return _transcript(ctx).render()


# --- 起票前レビュー(HITL ゲート 1)-----------------------------------------


def review_router(ctx: Context) -> Event:
    """起票内容をドラフト登録し、レビュー待ちにするか起票へ直行するかを決める。

    propose_case_draft はどちらの経路でも必ず通す。ここを通らないと
    create_support_case がツール層で起票を拒否する。
    """
    intake = _intake(ctx)
    _mark(ctx, "propose_case_draft")
    result = propose_case_draft(
        store_id=intake.store_id,
        store_name=intake.store_name,
        system_id=intake.system_id,
        symptom=intake.symptom,
        started_at=intake.started_at,
        impact_scope=intake.impact_scope,
        reporter_name=intake.reporter_name,
        tool_context=ctx,
    )
    if result.get("status") != "awaiting_confirmation":
        _ask(
            ctx,
            f"{result.get('message', '起票内容の登録に失敗しました。')}\n\n"
            "対象システムを次の中からお選びください。\n" + _system_choices(),
        )
        return Event(route="ASK")

    if ctx.state.get("intake_pre_approved"):
        return Event(
            message=_say(
                _draft_text(result["draft"])
                + "\n\n報告内容の確定をいただいているため、この内容で起票して調査を開始します。"
            ),
            route="PRE_APPROVED",
        )
    return Event(route="NEEDS_REVIEW")


def _draft_text(draft: dict) -> str:
    """起票内容(ドラフト)を報告者に見せる日本語テキストにする。"""
    return "\n".join(
        [
            "# 起票内容の確認",
            f"- 店舗: {draft.get('store_name', '')}(店舗ID: {draft.get('store_id', '')})",
            f"- 対象システム: {draft.get('system_name', '')}"
            f"(system_id: {draft.get('system_id', '')})",
            f"- 症状: {draft.get('symptom', '')}",
            f"- 発生日時: {draft.get('started_at', '')}",
            f"- 影響範囲: {draft.get('impact_scope', '')}",
            f"- 報告者: {draft.get('reporter_name', '')}",
        ]
    )


def review_gate(ctx: Context) -> Generator[Event | RequestInput, None, None]:
    """起票内容を報告者に提示し、確認が取れるまでワークフローを停止する。"""
    draft = ctx.state.get("case_draft") or {}
    message = (
        _draft_text(draft)
        + "\n\nこの内容でサポートケースを作成し、調査を開始してよろしいでしょうか?"
    )
    _mark(ctx, "review_gate")
    # 順序の理由は ask_reporter のコメントを参照
    yield RequestInput(
        message=message, interrupt_id=f"case-review-{ctx.state.get('step_review_gate')}"
    )
    yield Event(message=_say(message))


def review_decision(ctx: Context, node_input: Any) -> Event:
    """報告者の確認結果を判定する。否定なら訂正内容として聞き取りに戻す。"""
    answer = _as_text(node_input)
    _mark(ctx, "review_decision")
    if is_affirmative(answer):
        return Event(route="APPROVE")

    log = list(ctx.state.get("interview_log") or [])
    log.append({"question": "起票内容の確認", "answer": answer})
    ctx.state["interview_log"] = log
    _refresh_pre_approval(ctx)
    return Event(
        message=_say("承知しました。ご指摘の内容で起票内容を修正します。"),
        output=_transcript(ctx).render(),
        route="REVISE",
    )


def file_case(ctx: Context) -> Event:
    """サポートケースを起票し、ステータスを investigating にして調査へ渡す。"""
    intake = _intake(ctx)
    _mark(ctx, "create_support_case")
    result = create_support_case(
        store_id=intake.store_id,
        store_name=intake.store_name,
        system_id=intake.system_id,
        symptom=intake.symptom,
        started_at=intake.started_at,
        impact_scope=intake.impact_scope,
        reporter_name=intake.reporter_name,
        tool_context=ctx,
    )
    if result.get("status") != "success":
        ctx.state["halt_reason"] = "サポートケースの起票に失敗しました: " + str(
            result.get("message", "")
        )
        return Event(route="FAILED")

    case = result["case"]
    filed = CaseFiled(
        case_id=case["case_id"],
        store_id=case.get("store_id", ""),
        store_name=case.get("store_name", ""),
        system_id=case.get("system_id", ""),
        system_name=case.get("system_name", ""),
        symptom=case.get("symptom", ""),
        started_at=case.get("started_at", ""),
        impact_scope=case.get("impact_scope", ""),
        reporter_name=case.get("reporter_name", ""),
    )
    ctx.state["case"] = filed.model_dump()
    update_case_status(
        case_id=filed.case_id,
        status="investigating",
        note="調査エージェントによる原因調査を開始",
    )
    _mark(ctx, "update_case_status")
    return Event(
        message=_say(
            f"サポートケース {filed.case_id} を起票しました"
            f"(対象: {filed.system_name})。これより原因調査を開始します。"
        ),
        output=filed.render(),
        route="FILED",
    )


# --- 復旧承認(HITL ゲート 2)-----------------------------------------------


def plan_router(ctx: Context, node_input: RecoveryPlan) -> Event:
    """復旧計画を登録し、承認待ちにするか事前承認で実施へ進むかを決める。

    propose_recovery_plan はどちらの経路でも必ず通す。ここを通らないと
    restart_server / escalate_to_engineer がツール層で実行を拒否する。
    """
    plan = node_input
    case = _case(ctx)
    ctx.state["plan"] = plan.model_dump()
    _mark(ctx, "propose_recovery_plan")
    propose_recovery_plan(
        case_id=case.case_id,
        root_cause=plan.root_cause,
        planned_action=plan.planned_action,
        action_type=plan.action_type,
        impact_during_work=plan.impact_during_work,
        tool_context=ctx,
    )
    if ctx.state.get("recovery_pre_approved"):
        return Event(
            message=_say(
                plan.render()
                + "\n\n事前にご承認をいただいているため、このまま対応を実施します。"
            ),
            route="PRE_APPROVED",
        )
    return Event(route="NEEDS_APPROVAL")


def plan_gate(ctx: Context) -> Generator[Event | RequestInput, None, None]:
    """復旧計画を担当者に提示し、承認が得られるまでワークフローを停止する。"""
    message = (
        _plan(ctx).render() + "\n\n上記の内容で対応を進めてよろしいでしょうか?"
        "ご承認いただけましたら実施いたします。"
    )
    _mark(ctx, "plan_gate")
    # 順序の理由は ask_reporter のコメントを参照
    yield RequestInput(
        message=message,
        interrupt_id=f"recovery-approval-{ctx.state.get('step_plan_gate')}",
    )
    yield Event(message=_say(message))


def approval_decision(ctx: Context, node_input: Any) -> Event:
    """担当者の承認結果を判定する。否認ならツール層のゲートも閉じ直す。"""
    answer = _as_text(node_input)
    _mark(ctx, "approval_decision")
    if is_affirmative(answer):
        return Event(route="APPROVED")

    # 承認されなかったので、実施系ツールを再び拒否させる
    ctx.state["recovery_plan_proposed"] = None
    ctx.state["halt_reason"] = (
        "担当者の承認が得られなかったため、復旧作業は実施していません。"
        f"いただいたご指示: {answer or '(承認なし)'}"
    )
    return Event(route="DENIED")


def exec_router(ctx: Context) -> Event:
    """承認済みの対応種別に応じて、再起動とエスカレーションを振り分ける。"""
    _mark(ctx, "exec_router")
    if _plan(ctx).action_type == "restart":
        return Event(route="RESTART")
    return Event(route="ESCALATE")


# --- 復旧の実施と記録 -------------------------------------------------------


def restart_node(ctx: Context) -> Event:
    """承認済みのサーバー再起動を実行する。"""
    case = _case(ctx)
    _mark(ctx, "restart_server")
    result = restart_server(system_id=case.system_id, tool_context=ctx)
    ok = result.get("status") == "success"
    detail = str(result.get("message", ""))
    ctx.state["action_result"] = ActionResult(
        ok=ok, action_type="restart", detail=detail
    ).model_dump()
    if not ok:
        ctx.state["halt_reason"] = f"再起動に失敗したため、対応を中断しました: {detail}"
        return Event(route="FAILED")
    return Event(message=_say(detail), route="OK")


def escalate_node(ctx: Context) -> Event:
    """承認済みのエスカレーションを実行する。"""
    case = _case(ctx)
    plan = _plan(ctx)
    _mark(ctx, "escalate_to_engineer")
    result = escalate_to_engineer(
        case_id=case.case_id,
        reason=plan.root_cause,
        summary=f"{plan.evidence}\n実施予定の対応: {plan.planned_action}".strip(),
        tool_context=ctx,
    )
    ok = result.get("status") == "success"
    detail = str(result.get("message", ""))
    ctx.state["action_result"] = ActionResult(
        ok=ok, action_type="escalate", detail=detail
    ).model_dump()
    if not ok:
        ctx.state["halt_reason"] = (
            f"エスカレーションに失敗したため、対応を中断しました: {detail}"
        )
        return Event(route="FAILED")
    ctx.state["escalation_id"] = result.get("escalation_id", "")
    ctx.state["notified_team"] = result.get("notified_team", "")
    return Event(message=_say(detail), route="OK")


def record_node(ctx: Context) -> Event:
    """対応ログを記録し、ケースをクローズして最終報告を返す(終端ノード)。"""
    case = _case(ctx)
    plan = _plan(ctx)
    action = ActionResult.model_validate(
        ctx.state.get("action_result") or {"ok": True, "action_type": plan.action_type}
    )
    outcome = "resolved" if plan.action_type == "restart" else "escalated"

    _mark(ctx, "record_resolution_log")
    record_resolution_log(
        case_id=case.case_id,
        system_id=case.system_id,
        title=plan.title,
        root_cause=plan.root_cause,
        action_taken=plan.planned_action,
        outcome=outcome,
    )
    update_case_status(
        case_id=case.case_id,
        status=outcome,
        note=plan.planned_action,
    )
    _mark(ctx, "update_case_status")

    action_label = (
        "サーバー再起動" if plan.action_type == "restart" else "エスカレーション"
    )
    lines = [
        "# 対応結果のご報告",
        f"- ケースID: {case.case_id}",
        f"- 対象システム: {case.system_name}(system_id: {case.system_id})",
        f"- 特定した原因: {plan.root_cause}",
    ]
    if plan.evidence.strip():
        lines.append(f"- 根拠: {plan.evidence}")
    lines.append(f"- 実施した対応: {action_label} — {plan.planned_action}")
    if outcome == "escalated":
        lines.append(
            f"- 通知先: {ctx.state.get('notified_team') or 'インフラ運用チーム'}"
            f"(エスカレーションID: {ctx.state.get('escalation_id') or '-'})"
        )
    if action.detail:
        lines.append(f"- 実行結果: {action.detail}")
    lines.append(
        "- 現在のケース状態: "
        + (
            "復旧済み(resolved)"
            if outcome == "resolved"
            else "エスカレーション済み(escalated)"
        )
    )
    report = "\n".join(lines)
    return Event(message=_say(report), output=report)


def halt(ctx: Context) -> Event:
    """対応を打ち切った理由を報告して終了する(出力は持たない終端ノード)。"""
    _mark(ctx, "halt")
    return Event(
        message=_say(str(ctx.state.get("halt_reason") or "処理を中断しました。"))
    )
