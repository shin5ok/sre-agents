"""エージェント配線のテスト(LLM の出力内容には依存しない)。"""

from app.agent import app, root_agent
from app.config import MODEL_ID


def _tool_names(agent) -> set[str]:
    return {t.__name__ if callable(t) else t.name for t in agent.tools}


def test_app_name_matches_agent_directory():
    # App の name が app/ ディレクトリ名と一致しないと eval が "Session not found" で失敗する
    assert app.name == "app"


def test_root_is_reception_with_investigation_sub_agent():
    assert root_agent.name == "reception_agent"
    assert [a.name for a in root_agent.sub_agents] == ["investigation_agent"]


def test_reception_tools():
    assert _tool_names(root_agent) == {"list_managed_systems", "create_support_case"}


def test_investigation_tools_cover_spec_flow():
    investigation = root_agent.sub_agents[0]
    assert _tool_names(investigation) == {
        "get_support_case",
        "update_case_status",
        "get_system_status",
        "get_server_logs",
        "search_past_incidents",
        "search_runbooks",
        "restart_server",
        "escalate_to_engineer",
        "record_resolution_log",
    }


def test_agents_use_pinned_model():
    assert root_agent.model.model == MODEL_ID
    assert root_agent.sub_agents[0].model.model == MODEL_ID
