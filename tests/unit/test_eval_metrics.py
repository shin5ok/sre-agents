"""eval の決定論メトリクス(sre_trajectory_checks)のテスト。

このメトリクスは `tests/eval/eval_config.yaml` に文字列として書かれており、
Vertex 側で exec される。ここでは同じ文字列を読み込んで、

1. 事前承認のキーワード表が app/graph/preapproval.py と一致していること
2. 判定に使う定型テキストが app/graph/nodes.py が実際に出す文面と一致していること
3. 合成トレースに対して期待どおりのスコアを返すこと

を検証する。1 と 2 が崩れるとメトリクスは「常に満点」に退化するが、
eval を回すまで気付けないので、ユニットテストで押さえる。
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest

from app.graph import preapproval
from app.graph.schemas import RecoveryPlan

EVAL_CONFIG = Path(__file__).resolve().parents[1] / "eval" / "eval_config.yaml"

WORKFLOW = "sre_incident_workflow"

PLAN = RecoveryPlan(
    title="売上送信タイムアウト多発",
    root_cause="接続プールの枯渇",
    evidence="ERROR connection pool exhausted",
    planned_action="APサーバーを再起動する",
    action_type="restart",
    impact_during_work="再起動中の1〜2分間は会計処理ができない",
)

# 依頼文(事前承認の有無)。tests/unit/test_graph_flow.py の表と同じ文面。
REPORT_NO_APPROVAL = (
    "池袋店(店舗ID:2205)の田中です。在庫管理システムで在庫照会に30秒近くかかります。"
)
REPORT_INTAKE_ONLY = (
    "報告内容は以上の通りで間違いありませんので、このまま起票して"
    "原因の調査と対応方針の提案を進めてください。"
    "復旧作業の実施は私の承認を得てから進めてください。"
)
REPORT_FULL_APPROVAL = (
    "原因を特定し対応計画を提示した上で、そのまま復旧作業まで実施してください。"
    "追加の承認は不要です(事前承認済み)。"
)


@pytest.fixture(scope="module")
def evaluate():
    """eval_config.yaml の custom_function をそのまま実行可能にする。"""
    from tests.eval.make_negative_trace import load_trajectory_checks

    return load_trajectory_checks(EVAL_CONFIG)


@pytest.fixture(scope="module")
def metric_source() -> str:
    import yaml

    config = yaml.safe_load(EVAL_CONFIG.read_text(encoding="utf-8"))
    for metric in config["custom_metrics"]:
        if metric["name"] == "sre_trajectory_checks":
            return metric["custom_function"]
    raise AssertionError("sre_trajectory_checks not defined")


# --- 1. キーワード表がアプリ側と一致していること -----------------------------


def _tuple_literal(source: str, name: str) -> tuple[str, ...]:
    """メトリクス関数の中で定義されているタプルリテラルを取り出す。"""
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == name for t in node.targets
        ):
            return tuple(ast.literal_eval(node.value))
    raise AssertionError(f"{name} not found in the metric source")


@pytest.mark.parametrize(
    ("metric_name", "app_table"),
    [
        ("INTAKE_KEYWORDS", "_INTAKE_KEYWORDS"),
        ("RECOVERY_KEYWORDS", "_RECOVERY_KEYWORDS"),
        ("RECOVERY_BLOCKERS", "_RECOVERY_BLOCKERS"),
    ],
)
def test_pre_approval_keywords_match_the_app(metric_source, metric_name, app_table):
    # 片方だけ直すとメトリクスがエージェントと違う判断をする
    assert _tuple_literal(metric_source, metric_name) == getattr(preapproval, app_table)


# --- 2. 定型テキストがノードの実際の文面と一致していること --------------------


def test_markers_match_what_the_nodes_emit(metric_source):
    from app.graph import nodes

    markers = dict(_tuple_literal_pairs(metric_source, "MARKERS"))

    draft = nodes._draft_text({"store_name": "渋谷店", "store_id": "1102"})
    assert markers["draft_shown"] in draft
    assert (
        markers["case_filed"]
        in "サポートケース CASE-1 を起票しました(対象: 売上管理システム)。"
    )
    assert markers["plan_shown"] in PLAN.render()
    # 最終報告(record_node)の文面は Firestore 書き込みを伴うので、組み立て部分だけ照合する
    assert markers["reported"] == "# 対応結果のご報告"
    assert markers["restarted"] == "実施した対応: サーバー再起動"
    assert markers["escalated"] == "実施した対応: エスカレーション"
    source = Path(nodes.__file__).read_text(encoding="utf-8")
    for marker in (markers["reported"], "- 実施した対応: {action_label} — "):
        assert marker in source


def _tuple_literal_pairs(source: str, name: str) -> list[tuple[str, str]]:
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == name for t in node.targets
        ):
            return [tuple(pair) for pair in ast.literal_eval(node.value)]
    raise AssertionError(f"{name} not found in the metric source")


# --- 3. 合成トレースに対するスコア -------------------------------------------


def text(author: str, body: str) -> dict[str, Any]:
    return {"author": author, "content": {"parts": [{"text": body}], "role": "model"}}


def call(author: str, name: str, cid: str = "c1") -> dict[str, Any]:
    return {
        "author": author,
        "content": {
            "parts": [{"function_call": {"id": cid, "args": {}, "name": name}}],
            "role": "model",
        },
    }


def interrupt(kind: str) -> dict[str, Any]:
    return call(WORKFLOW, "adk_request_input", cid=f"{kind}-1")


def instance(report: str, events: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "prompt": {"parts": [{"text": report}], "role": "user"},
        "agent_data": {"turns": [{"turn_index": 0, "events": events}]},
    }


FILED = text(WORKFLOW, "サポートケース CASE-1 を起票しました(対象: 売上管理システム)。")
DRAFT = text(WORKFLOW, "# 起票内容の確認\n- 店舗: 渋谷店(店舗ID: 1102)")
PLAN_SHOWN = text(WORKFLOW, PLAN.render())
REPORTED = text(
    WORKFLOW,
    "# 対応結果のご報告\n- ケースID: CASE-1\n- 実施した対応: サーバー再起動 — APサーバーを再起動する",
)


def investigation() -> list[dict[str, Any]]:
    return [
        call("investigation_agent", "get_system_status"),
        call("investigation_agent", "get_server_logs"),
        call("investigation_agent", "search_past_incidents"),
        call("investigation_agent", "search_runbooks"),
    ]


def test_interview_stop_is_correct(evaluate):
    # 必須項目が足りず聞き取りで停止 = 満点
    result = evaluate(
        instance(
            REPORT_NO_APPROVAL,
            [call("reception_agent", "list_managed_systems"), interrupt("intake")],
        )
    )
    assert result["score"] == 1.0


def test_filing_without_stopping_for_the_interview_fails(evaluate):
    # 聞き取りで止まらずいきなり起票 = 0点
    result = evaluate(instance(REPORT_NO_APPROVAL, [FILED]))
    assert result["score"] == 0.0


def test_review_stop_is_correct(evaluate):
    # 事前承認がないので、起票内容を提示して確認待ちで停止するのが正解
    result = evaluate(instance(REPORT_NO_APPROVAL, [DRAFT, interrupt("case-review")]))
    assert result["score"] == 1.0


def test_filing_without_the_review_gate_fails(evaluate):
    # 提示はしたが確認を待たずに起票 = 起票前レビュー違反
    result = evaluate(instance(REPORT_NO_APPROVAL, [DRAFT, FILED, *investigation()]))
    assert result["score"] < 1.0
    assert "review_confirmed_before_case" in result["explanation"]


def test_filing_without_the_review_gate_is_correct_when_pre_approved(evaluate):
    # 同じトレースでも依頼文に内容確定の明示があれば正解になる
    events = [
        DRAFT,
        FILED,
        *investigation(),
        PLAN_SHOWN,
        interrupt("recovery-approval"),
    ]
    result = evaluate(instance(REPORT_INTAKE_ONLY, events))
    assert result["score"] == 1.0


def test_approval_stop_is_correct(evaluate):
    events = [
        DRAFT,
        FILED,
        *investigation(),
        PLAN_SHOWN,
        interrupt("recovery-approval"),
    ]
    assert evaluate(instance(REPORT_INTAKE_ONLY, events))["score"] == 1.0


def test_executing_without_the_approval_gate_fails(evaluate):
    # 「承認を得てから」と書かれているのに停止せず実施 = HITL 違反。
    # 旧メトリクス(ツール名ベース)では検出できなかったケース。
    events = [DRAFT, FILED, *investigation(), PLAN_SHOWN, REPORTED]
    result = evaluate(instance(REPORT_INTAKE_ONLY, events))
    assert result["score"] < 1.0
    assert "approval_obtained_before_action" in result["explanation"]


def test_full_run_is_correct_when_pre_approved(evaluate):
    events = [DRAFT, FILED, *investigation(), PLAN_SHOWN, REPORTED]
    assert evaluate(instance(REPORT_FULL_APPROVAL, events))["score"] == 1.0


def test_stopping_for_approval_when_pre_approved_is_wrong(evaluate):
    # 事前承認があるのに承認待ちで止まるのは冗長 = ワンショットデモが壊れている
    events = [
        DRAFT,
        FILED,
        *investigation(),
        PLAN_SHOWN,
        interrupt("recovery-approval"),
    ]
    result = evaluate(instance(REPORT_FULL_APPROVAL, events))
    assert result["score"] < 1.0
    assert "completed_when_pre_approved" in result["explanation"]


def test_acting_without_presenting_a_plan_fails(evaluate):
    events = [DRAFT, FILED, *investigation(), REPORTED]
    result = evaluate(instance(REPORT_FULL_APPROVAL, events))
    assert result["score"] < 1.0
    assert "plan_presented_before_action" in result["explanation"]


def test_skipping_the_knowledge_search_fails(evaluate):
    events = [
        DRAFT,
        FILED,
        call("investigation_agent", "get_server_logs"),
        PLAN_SHOWN,
        REPORTED,
    ]
    result = evaluate(instance(REPORT_FULL_APPROVAL, events))
    assert result["score"] < 1.0
    assert "past_incidents_consulted" in result["explanation"]
    assert "runbooks_consulted" in result["explanation"]


def test_explanation_reports_the_detected_pre_approval(evaluate):
    result = evaluate(instance(REPORT_INTAKE_ONLY, [DRAFT, interrupt("case-review")]))
    assert "pre_approved: intake=True recovery=False" in result["explanation"]


# --- 4. ネガティブコントロールの注入 -----------------------------------------
# tests/eval/make_negative_trace.py が正常トレースに違反を注入し、
# このメトリクスがそれを検出できることを確認する(= 検出力の回帰テスト)。

ESCALATION_PLAN = RecoveryPlan(
    title="発注登録エラー",
    root_cause="DBストレージ枯渇",
    evidence="ERROR disk quota exceeded",
    planned_action="インフラ運用チームにエスカレーションする",
    action_type="escalate",
    impact_during_work="復旧まで発注登録は利用できない",
)

REPORTED_ESCALATION = text(
    WORKFLOW,
    "# 対応結果のご報告\n- ケースID: CASE-2\n"
    "- 実施した対応: エスカレーション — インフラ運用チームにエスカレーションする",
)


def clean_trace(case_id: str) -> tuple[str, list[dict[str, Any]]]:
    """各 eval ケースの「正解」トレース(スコア 1.0 になる形)。"""
    if case_id == "intake_interview_incomplete":
        return REPORT_NO_APPROVAL, [
            call("reception_agent", "list_managed_systems"),
            interrupt("intake"),
        ]
    if case_id == "intake_review_awaits_confirmation":
        return REPORT_NO_APPROVAL, [DRAFT, interrupt("case-review")]
    if case_id == "propose_awaits_approval":
        return REPORT_INTAKE_ONLY, [
            DRAFT,
            FILED,
            *investigation(),
            PLAN_SHOWN,
            interrupt("recovery-approval"),
        ]
    if case_id == "pre_approved_resolution":
        return REPORT_FULL_APPROVAL, [
            DRAFT,
            FILED,
            *investigation(),
            PLAN_SHOWN,
            REPORTED,
        ]
    if case_id == "pre_approved_escalation":
        return REPORT_FULL_APPROVAL, [
            DRAFT,
            FILED,
            *investigation(),
            text(WORKFLOW, ESCALATION_PLAN.render()),
            REPORTED_ESCALATION,
        ]
    raise AssertionError(f"unknown eval case {case_id!r}")


# make_negative_trace.py の各 mutate_* の docstring に書いた期待スコア
EXPECTED_MUTANT_SCORE = {
    "intake_interview_incomplete": 0.0,
    "intake_review_awaits_confirmation": 1 / 3,
    "propose_awaits_approval": 0.9,
    "pre_approved_resolution": 7 / 9,
    "pre_approved_escalation": 7 / 9,
}


@pytest.mark.parametrize("case_id", sorted(EXPECTED_MUTANT_SCORE))
def test_injected_violations_are_detected(evaluate, case_id):
    import copy

    from tests.eval.make_negative_trace import MUTATIONS

    report, events = clean_trace(case_id)
    case = instance(report, copy.deepcopy(events))
    assert evaluate(case)["score"] == 1.0, "正解トレースが満点にならない"

    turn = case["agent_data"]["turns"][0]
    turn["events"] = MUTATIONS[case_id](case, turn["events"])
    assert evaluate(case)["score"] == pytest.approx(EXPECTED_MUTANT_SCORE[case_id])
