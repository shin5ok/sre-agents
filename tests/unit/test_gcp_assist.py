"""Gemini Cloud Assist(MCP)連携の配線テスト(ネットワーク非依存)。

McpToolset は生成時にはMCPサーバーへ接続しない(遅延接続)ため、
ここでは「SANDBOX_PROJECT_ID の有無で構成が正しく切り替わること」だけを検証する。
"""

import asyncio

from google.adk.tools.mcp_tool import McpToolset

from app.sub_agents.investigation import create_investigation_agent
from app.tools import gcp_assist

SANDBOX = "sandbox-test-project"


def _toolsets(agent) -> list[McpToolset]:
    return [t for t in agent.tools if isinstance(t, McpToolset)]


def _instruction_text(agent) -> str:
    assert isinstance(agent.instruction, str)
    return agent.instruction


def test_sandbox_tools_disabled_flag_wins(monkeypatch):
    # .env が SANDBOX_PROJECT_ID を注入しても、eval 用の明示スイッチで無効化できる
    monkeypatch.setenv("SANDBOX_PROJECT_ID", SANDBOX)
    monkeypatch.setenv("SANDBOX_TOOLS_DISABLED", "1")
    assert gcp_assist.build_cloud_assist_toolset() is None
    agent = create_investigation_agent()
    assert _toolsets(agent) == []
    assert "Cloud Assist" not in _instruction_text(agent)


def test_toolset_absent_without_sandbox_project(monkeypatch):
    monkeypatch.delenv("SANDBOX_PROJECT_ID", raising=False)
    assert gcp_assist.build_cloud_assist_toolset() is None
    agent = create_investigation_agent()
    assert _toolsets(agent) == []
    assert "Cloud Assist" not in _instruction_text(agent)
    assert "query_incident_bigquery_logs" not in {
        t.__name__ for t in agent.tools if callable(t)
    }


def test_toolset_present_with_sandbox_project(monkeypatch):
    monkeypatch.setenv("SANDBOX_PROJECT_ID", SANDBOX)
    toolset = gcp_assist.build_cloud_assist_toolset()
    assert isinstance(toolset, McpToolset)

    agent = create_investigation_agent()
    assert len(_toolsets(agent)) == 1
    # instruction に project 引数の値("projects/<id>")が展開されている
    assert f"projects/{SANDBOX}" in _instruction_text(agent)
    # GCE層の調査ツール(BigQueryログ検索)も同時に組み込まれる
    assert "query_incident_bigquery_logs" in {
        t.__name__ for t in agent.tools if callable(t)
    }
    assert "Compute Engine" in _instruction_text(agent)
    # 調査(読み取り)系のみ公開する(mutative な invoke_operation 等は含めない)
    assert set(gcp_assist.CLOUD_ASSIST_TOOLS) == {
        "investigate_issue",
        "ask_cloud_assist",
    }


def test_auth_header_provider_adds_quota_project_when_missing(monkeypatch):
    class FakeCreds:
        token = "test-token"
        quota_project_id = None

    monkeypatch.setenv("SANDBOX_PROJECT_ID", SANDBOX)
    monkeypatch.setattr(gcp_assist, "_get_fresh_credentials", lambda: FakeCreds())
    headers = asyncio.run(gcp_assist.auth_header_provider(None))
    assert headers["Authorization"] == "Bearer test-token"
    assert headers["x-goog-user-project"] == SANDBOX


def test_auth_header_provider_keeps_existing_quota_project(monkeypatch):
    class FakeCreds:
        token = "test-token"
        quota_project_id = "creds-own-quota-project"

    monkeypatch.setenv("SANDBOX_PROJECT_ID", SANDBOX)
    monkeypatch.setattr(gcp_assist, "_get_fresh_credentials", lambda: FakeCreds())
    headers = asyncio.run(gcp_assist.auth_header_provider(None))
    assert "x-goog-user-project" not in headers
