"""ワークフローグラフの配線のテスト(LLM の出力内容には依存しない)。"""

import os

from google.adk.tools.mcp_tool import McpToolset

from app.agent import app, root_agent
from app.config import MODEL_ID
from app.graph.schemas import IntakeResult, RecoveryPlan

# 調査エージェントが常に持つ関数ツール(調査系のみ。
# 復旧の実施・記録はグラフの関数ノードへ移したのでツールには含めない)
INVESTIGATION_FUNCTION_TOOLS = {
    "get_support_case",
    "get_system_status",
    "get_server_logs",
    "search_past_incidents",
    "search_runbooks",
}

# グラフを構成するノード(START を除く)
EXPECTED_NODES = {
    "detect_intent",
    "reception_agent",
    "intake_router",
    "ask_reporter",
    "collect_answer",
    "review_router",
    "review_gate",
    "review_decision",
    "file_case",
    "investigation_agent",
    "plan_router",
    "plan_gate",
    "approval_decision",
    "exec_router",
    "restart_node",
    "escalate_node",
    "record_node",
    "halt",
}

NODES = {n.name: n for n in root_agent.graph.nodes}
EDGES = {(e.from_node.name, e.to_node.name): e.route for e in root_agent.graph.edges}


def _function_tool_names(agent) -> set[str]:
    return {t.__name__ for t in agent.tools if callable(t)}


def test_app_name_matches_agent_directory():
    # App の name が app/ ディレクトリ名と一致しないと eval が "Session not found" で失敗する
    assert app.name == "app"


def test_root_is_workflow_with_expected_nodes():
    assert root_agent.name == "sre_incident_workflow"
    assert set(NODES) - {"__START__"} == EXPECTED_NODES


def test_reception_tools():
    # 起票系(propose_case_draft / create_support_case)はグラフの関数ノードが持つ
    assert _function_tool_names(NODES["reception_agent"]) == {"list_managed_systems"}


def test_investigation_tools_cover_spec_flow():
    investigation = NODES["investigation_agent"]
    expected = set(INVESTIGATION_FUNCTION_TOOLS)
    # GCP調査ツール(BigQueryログ検索 + Cloud Assist MCP)は
    # SANDBOX_PROJECT_ID 設定時のみ付く
    toolsets = [t for t in investigation.tools if isinstance(t, McpToolset)]
    if os.environ.get("SANDBOX_PROJECT_ID"):
        expected |= {"query_incident_bigquery_logs"}
        assert len(toolsets) == 1
    else:
        assert toolsets == []
    assert _function_tool_names(investigation) == expected


def test_llm_nodes_are_single_turn_with_output_schema():
    # single_turn 以外(既定の chat)だと後退エッジのあるノードはグラフ検証で弾かれる。
    # output_schema がないと後続の router が決定的に分岐できない。
    reception = NODES["reception_agent"]
    investigation = NODES["investigation_agent"]
    assert reception.mode == "single_turn"
    assert investigation.mode == "single_turn"
    assert reception.output_schema is IntakeResult
    assert investigation.output_schema is RecoveryPlan


def test_agents_use_pinned_model():
    assert NODES["reception_agent"].model.model == MODEL_ID
    assert NODES["investigation_agent"].model.model == MODEL_ID


def test_recovery_is_only_reachable_through_an_approval_gate():
    # HITL の肝。実施系ノードへの入口は exec_router だけで、exec_router への入口は
    # 承認済み(approval_decision:APPROVED)か事前承認(plan_router:PRE_APPROVED)しかない。
    into_exec = {
        frm: route for (frm, to), route in EDGES.items() if to == "exec_router"
    }
    assert into_exec == {"approval_decision": "APPROVED", "plan_router": "PRE_APPROVED"}

    for node in ("restart_node", "escalate_node"):
        assert {frm for (frm, to) in EDGES if to == node} == {"exec_router"}


def test_case_filing_is_only_reachable_through_a_review_gate():
    into_file = {frm: route for (frm, to), route in EDGES.items() if to == "file_case"}
    assert into_file == {"review_decision": "APPROVE", "review_router": "PRE_APPROVED"}


def test_interview_loop_has_back_edges():
    # 聞き取りの繰り返しと起票内容の修正は、後退エッジでグラフの構造として表現する
    assert ("intake_router", "ask_reporter") in EDGES
    assert ("ask_reporter", "collect_answer") in EDGES
    assert ("collect_answer", "reception_agent") in EDGES
    assert EDGES[("review_decision", "reception_agent")] == "REVISE"


def test_record_node_is_the_only_terminal_with_output():
    terminals = EXPECTED_NODES - {frm for (frm, _to) in EDGES}
    assert terminals == {"record_node", "halt"}
