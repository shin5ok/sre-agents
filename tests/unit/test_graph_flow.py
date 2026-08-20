"""グラフの関数ノードのテスト(LLM も Firestore も呼ばない範囲)。

事前承認の判定と各ルーターの分岐が決定的であることを確認する。
起票(file_case)と対応ログ記録(record_node)は Firestore に書き込むため、
ここでは扱わない。
"""

from types import SimpleNamespace

import pytest

from app.graph import nodes
from app.graph.preapproval import PreApproval, detect_pre_approval, is_affirmative
from app.graph.schemas import CaseFiled, IntakeResult, RecoveryPlan

# eval データセット(tests/eval/datasets/basic-dataset.json)と README のデモ文面。
# 事前承認ルーターがこの表どおりに判定できないと、ワンショットのデモと eval が壊れる。
EVAL_PROMPTS: dict[str, str] = {
    "intake_interview_incomplete": (
        "渋谷店ですが、売上管理システムの調子が悪くて会計に時間がかかっています。"
        "なんとかしてもらえますか。"
    ),
    "intake_review_awaits_confirmation": (
        "池袋店(店舗ID:2205)の田中です。在庫管理システムで今朝7時半ごろから"
        "在庫照会に30秒近くかかり、断続的にエラーも出ています。"
        "店内の発注端末2台で発生していて、棚卸作業が止まっています。"
    ),
    "propose_awaits_approval": (
        "報告内容は以上の通りで間違いありませんので、このまま起票して"
        "原因の調査と対応方針の提案を進めてください。"
        "復旧作業の実施は私の承認を得てから進めてください。"
    ),
    "pre_approved_resolution": (
        "原因を特定し対応計画を提示した上で、そのまま復旧作業まで実施してください。"
        "追加の承認は不要です(事前承認済み)。"
    ),
    "pre_approved_escalation": (
        "横浜元町店(店舗ID:2201)の店長の田中です。発注管理システムで"
        "必要な対応(エスカレーションを含む)をそのまま実施してください。"
        "追加の承認は不要です(事前承認済み)。"
    ),
    "readme_gcp_demo": (
        "報告内容は以上の通りで間違いありませんので、このまま起票して"
        "調査を進めてください。復旧作業は私の承認を得てから進めてください。"
    ),
    "makefile_default": (
        "渋谷店(店舗ID:1102)です。売上管理システムで今朝9時頃からレジ2台の"
        "会計時に売上送信がタイムアウトします。報告者は佐藤です。"
        "以上の内容で対応をお願いします。"
    ),
    "welcome": "こんにちは",
}

EXPECTED_PRE_APPROVAL: dict[str, PreApproval] = {
    "intake_interview_incomplete": PreApproval(intake=False, recovery=False),
    "intake_review_awaits_confirmation": PreApproval(intake=False, recovery=False),
    # 起票は事前承認だが、復旧は「承認を得てから」なので承認待ちにする
    "propose_awaits_approval": PreApproval(intake=True, recovery=False),
    "pre_approved_resolution": PreApproval(intake=True, recovery=True),
    "pre_approved_escalation": PreApproval(intake=True, recovery=True),
    "readme_gcp_demo": PreApproval(intake=True, recovery=False),
    "makefile_default": PreApproval(intake=True, recovery=False),
    "welcome": PreApproval(intake=False, recovery=False),
}

COMPLETE_INTAKE = IntakeResult(
    status="ready",
    store_id="1102",
    store_name="渋谷店",
    system_id="sales-mgmt",
    symptom="売上送信がタイムアウトする",
    started_at="今朝9時頃",
    impact_scope="レジ2台",
    reporter_name="佐藤",
)

PLAN = RecoveryPlan(
    title="売上送信タイムアウト多発",
    root_cause="接続プールの枯渇",
    evidence="ERROR connection pool exhausted",
    planned_action="APサーバーを再起動する",
    action_type="restart",
    impact_during_work="再起動中の1〜2分間は会計処理ができない",
)


def make_ctx(**state) -> SimpleNamespace:
    """関数ノードに渡す最小の Context スタブ。

    ノードが使うのは ctx.state だけで、既存ツールに tool_context として
    そのまま渡す(既存の HITL テストと同じ渡し方)。
    """
    return SimpleNamespace(state=dict(state))


# --- 事前承認の判定 ---------------------------------------------------------


@pytest.mark.parametrize("case_id", sorted(EVAL_PROMPTS))
def test_pre_approval_matches_expected_table(case_id):
    assert detect_pre_approval(EVAL_PROMPTS[case_id]) == EXPECTED_PRE_APPROVAL[case_id]


def test_detect_intent_records_report_and_flags():
    ctx = make_ctx()
    rendered = nodes.detect_intent(ctx, EVAL_PROMPTS["pre_approved_resolution"])
    assert ctx.state["initial_report"] == EVAL_PROMPTS["pre_approved_resolution"]
    assert ctx.state["intake_pre_approved"] is True
    assert ctx.state["recovery_pre_approved"] is True
    assert "報告者からの初回報告" in rendered


def test_pre_approval_is_revoked_by_a_later_answer():
    # 後の発言で「承認を得てから」と言われたら、復旧の事前承認は取り消される
    ctx = make_ctx()
    nodes.detect_intent(ctx, "そのまま実施してください。")
    assert ctx.state["recovery_pre_approved"] is True

    ctx.state["pending_question"] = "報告者名を教えてください"
    nodes.collect_answer(ctx, "佐藤です。復旧作業は私の承認を得てから進めてください。")
    assert ctx.state["recovery_pre_approved"] is False


@pytest.mark.parametrize(
    "answer,expected",
    [
        ("はい、お願いします", True),
        ("この内容で問題ありません", True),
        ("承認します", True),
        ("いいえ、症状が違います", False),
        ("店舗IDが間違っているので修正してください", False),
        ("", False),
    ],
)
def test_is_affirmative(answer, expected):
    assert is_affirmative(answer) is expected


# --- 聞き取りループ ---------------------------------------------------------


def test_intake_router_asks_when_fields_are_missing():
    ctx = make_ctx(initial_report="売上管理システムが遅いです")
    intake = IntakeResult(status="ready", store_name="渋谷店")  # LLM が ready と誤答
    event = nodes.intake_router(ctx, intake)
    # LLM の自己申告ではなく必須項目の実体で判定する
    assert event.actions.route == "ASK"
    assert ctx.state["pending_question"]


def test_intake_router_asks_when_system_id_is_unknown():
    ctx = make_ctx(initial_report="売上管理システムが遅いです")
    intake = COMPLETE_INTAKE.model_copy(update={"system_id": "sales-management"})
    event = nodes.intake_router(ctx, intake)
    assert event.actions.route == "ASK"
    assert "sales-mgmt" in ctx.state["pending_question"]


def test_intake_router_proceeds_when_complete():
    ctx = make_ctx(initial_report="…")
    event = nodes.intake_router(ctx, COMPLETE_INTAKE)
    assert event.actions.route == "READY"
    assert ctx.state["intake"]["system_id"] == "sales-mgmt"


def test_welcome_message_is_returned_verbatim_without_a_report():
    ctx = make_ctx(initial_report="")
    event = nodes.intake_router(ctx, IntakeResult(status="welcome"))
    assert event.actions.route == "ASK"
    assert ctx.state["pending_question"] == nodes.WELCOME_MESSAGE


@pytest.mark.parametrize(
    ("gate", "state", "interrupt_prefix"),
    [
        ("ask_reporter", {"pending_question": "店舗IDを教えてください"}, "intake-"),
        ("review_gate", {"case_draft": {"store_name": "渋谷店"}}, "case-review-"),
        ("plan_gate", {"plan": PLAN.model_dump()}, "recovery-approval-"),
    ],
)
def test_gates_yield_the_interrupt_before_the_text(gate, state, interrupt_prefix):
    """停止イベントを先に、本文の Event を後に出すこと。

    この順序は eval の応答抽出のために必要。agents-cli は「最後に残った
    イベントの parts[0].text」を応答として取り出すので、末尾が
    adk_request_input の function_call だけになると抽出が KeyError で落ちる。
    """
    emitted = list(getattr(nodes, gate)(make_ctx(**state)))
    assert [type(e).__name__ for e in emitted] == ["RequestInput", "Event"]
    request_input, event = emitted
    assert request_input.interrupt_id.startswith(interrupt_prefix)
    # 同じ本文を人間向けの発話としても出す(UI に質問文が出るのはこちら)
    assert event.message.parts[0].text == request_input.message
    assert event.message.role == "model"


def test_ask_reporter_asks_the_pending_question():
    ctx = make_ctx(pending_question="店舗IDを教えてください")
    emitted = list(nodes.ask_reporter(ctx))
    assert emitted[0].message == "店舗IDを教えてください"


def test_collect_answer_treats_the_first_answer_as_the_report():
    ctx = make_ctx(initial_report="", pending_question=nodes.WELCOME_MESSAGE)
    nodes.collect_answer(ctx, "渋谷店です。売上管理システムが遅いです。")
    assert ctx.state["initial_report"].startswith("渋谷店です")
    # ウェルカムへの応答は質疑ではなく初回報告そのものなので、聞き取り記録には積まない
    assert not ctx.state.get("interview_log")


def test_collect_answer_appends_to_the_interview_log():
    ctx = make_ctx(
        initial_report="売上管理システムが遅いです", pending_question="報告者名は?"
    )
    rendered = nodes.collect_answer(ctx, "佐藤です")
    assert ctx.state["interview_log"] == [
        {"question": "報告者名は?", "answer": "佐藤です"}
    ]
    # 会話履歴を持たない single_turn ノードのために、聞き取り全体を毎回渡す
    assert "売上管理システムが遅いです" in rendered
    assert "佐藤です" in rendered


# --- 起票前レビュー(HITL ゲート1)-----------------------------------------


def test_review_router_registers_the_draft_and_waits():
    ctx = make_ctx(intake=COMPLETE_INTAKE.model_dump(), intake_pre_approved=False)
    event = nodes.review_router(ctx)
    assert event.actions.route == "NEEDS_REVIEW"
    # create_support_case のツール層ゲートを開ける下ごしらえ
    assert ctx.state["case_draft"]["system_id"] == "sales-mgmt"


def test_review_router_skips_the_gate_when_pre_approved():
    ctx = make_ctx(intake=COMPLETE_INTAKE.model_dump(), intake_pre_approved=True)
    event = nodes.review_router(ctx)
    assert event.actions.route == "PRE_APPROVED"
    assert ctx.state["case_draft"]["system_id"] == "sales-mgmt"


def test_review_decision_sends_corrections_back_to_the_interview():
    ctx = make_ctx(initial_report="売上管理システムが遅いです", interview_log=[])
    event = nodes.review_decision(ctx, "いいえ、店舗IDは1103です")
    assert event.actions.route == "REVISE"
    assert ctx.state["interview_log"][-1]["answer"] == "いいえ、店舗IDは1103です"
    # 訂正内容を載せた聞き取り記録を受付エージェントへ差し戻す
    assert "1103" in str(event.output)


def test_review_decision_approves():
    ctx = make_ctx(initial_report="…", interview_log=[])
    assert nodes.review_decision(ctx, "はい、お願いします").actions.route == "APPROVE"


# --- 復旧承認(HITL ゲート2)-----------------------------------------------


def test_plan_router_registers_the_plan_and_waits():
    ctx = make_ctx(
        case=CaseFiled(case_id="CASE-1", system_id="sales-mgmt").model_dump(),
        recovery_pre_approved=False,
    )
    event = nodes.plan_router(ctx, PLAN)
    assert event.actions.route == "NEEDS_APPROVAL"
    # restart_server のツール層ゲートを開ける下ごしらえ
    assert ctx.state["recovery_plan_proposed"] == "restart"


def test_plan_router_skips_the_gate_when_pre_approved():
    ctx = make_ctx(
        case=CaseFiled(case_id="CASE-1", system_id="sales-mgmt").model_dump(),
        recovery_pre_approved=True,
    )
    event = nodes.plan_router(ctx, PLAN)
    assert event.actions.route == "PRE_APPROVED"


def test_denied_approval_recloses_the_tool_layer_gate():
    ctx = make_ctx(plan=PLAN.model_dump(), recovery_plan_proposed="restart")
    event = nodes.approval_decision(ctx, "いいえ、まだ待ってください")
    assert event.actions.route == "DENIED"
    # ツール層のゲートも閉じ直す(グラフ側だけでなく二重に止める)
    assert ctx.state["recovery_plan_proposed"] is None
    assert ctx.state["halt_reason"]


def test_approved_plan_routes_by_action_type():
    ctx = make_ctx(plan=PLAN.model_dump(), recovery_plan_proposed="restart")
    assert nodes.approval_decision(ctx, "承認します").actions.route == "APPROVED"
    assert nodes.exec_router(ctx).actions.route == "RESTART"

    ctx.state["plan"] = PLAN.model_copy(update={"action_type": "escalate"}).model_dump()
    assert nodes.exec_router(ctx).actions.route == "ESCALATE"


# --- 実施ノード -------------------------------------------------------------


def test_restart_node_runs_only_after_the_plan_was_registered():
    case = CaseFiled(case_id="CASE-1", system_id="sales-mgmt").model_dump()
    ctx = make_ctx(case=case, plan=PLAN.model_dump())
    # propose_recovery_plan を通っていないので、ツール層が実行を拒否する
    failed = nodes.restart_node(ctx)
    assert failed.actions.route == "FAILED"
    assert ctx.state["action_result"]["ok"] is False

    ctx.state["recovery_plan_proposed"] = "restart"
    assert nodes.restart_node(ctx).actions.route == "OK"
    assert ctx.state["remediated_systems"] == ["sales-mgmt"]


def test_escalate_node_runs_only_after_the_plan_was_registered():
    plan = PLAN.model_copy(update={"action_type": "escalate"})
    case = CaseFiled(case_id="CASE-1", system_id="order-mgmt").model_dump()
    ctx = make_ctx(case=case, plan=plan.model_dump())
    assert nodes.escalate_node(ctx).actions.route == "FAILED"

    ctx.state["recovery_plan_proposed"] = "escalate"
    assert nodes.escalate_node(ctx).actions.route == "OK"
    assert ctx.state["escalation_id"] == "ESC-CASE-1"


def test_halt_reports_the_reason_without_producing_output():
    ctx = make_ctx(halt_reason="担当者の承認が得られませんでした。")
    event = nodes.halt(ctx)
    # 出力を持つ終端ノードは record_node の1つだけでなければならない
    assert event.output is None
    assert event.content.parts[0].text == "担当者の承認が得られませんでした。"
