"""GCE障害シナリオ(store-web-fleet / inventory-mgmt)連携のユニットテスト(ネットワーク非依存)。

- restart_server: SANDBOX_PROJECT_ID 設定時のみ実リセット(_reset_gce_instance)を
  呼ぶこと、失敗時に success を偽らないこと(gce_restart_instance を持つ全システム)
- query_incident_bigquery_logs: 未設定時・空キーワード時はネットワークに出ずエラーを返すこと
"""

from types import SimpleNamespace

import pytest

from app.tools import gcp_logs, remediation_tools

SANDBOX = "sandbox-test-project"

# restart_server が実リセットする (system_id, 対象インスタンス) の組
GCE_RESTART_SYSTEMS = [
    ("store-web-fleet", "store-web-02"),
    ("inventory-mgmt", "inventory-mgmt"),
]


def _approved_ctx() -> SimpleNamespace:
    return SimpleNamespace(state={"recovery_plan_proposed": "restart"})


@pytest.mark.parametrize("system_id,instance", GCE_RESTART_SYSTEMS)
def test_restart_uses_mock_path_without_sandbox_project(
    monkeypatch, system_id, instance
):
    monkeypatch.delenv("SANDBOX_PROJECT_ID", raising=False)

    def _fail(**_kwargs):
        raise AssertionError("SANDBOX_PROJECT_ID 未設定で実リセットが呼ばれた")

    monkeypatch.setattr(remediation_tools, "_reset_gce_instance", _fail)
    result = remediation_tools.restart_server(system_id, _approved_ctx())
    assert result["status"] == "success"


@pytest.mark.parametrize("system_id,instance", GCE_RESTART_SYSTEMS)
def test_restart_resets_real_instance_with_sandbox_project(
    monkeypatch, system_id, instance
):
    monkeypatch.setenv("SANDBOX_PROJECT_ID", SANDBOX)
    monkeypatch.delenv("SANDBOX_TOOLS_DISABLED", raising=False)
    calls = []
    monkeypatch.setattr(
        remediation_tools,
        "_reset_gce_instance",
        lambda **kwargs: calls.append(kwargs),
    )
    ctx = _approved_ctx()
    result = remediation_tools.restart_server(system_id, ctx)
    assert result["status"] == "success"
    assert calls == [
        {
            "project_id": SANDBOX,
            "zone": "asia-northeast1-a",
            "instance": instance,
        }
    ]
    assert system_id in ctx.state["remediated_systems"]
    assert instance in result["message"]


@pytest.mark.parametrize("system_id,instance", GCE_RESTART_SYSTEMS)
def test_restart_reports_error_when_reset_fails(monkeypatch, system_id, instance):
    monkeypatch.setenv("SANDBOX_PROJECT_ID", SANDBOX)
    monkeypatch.delenv("SANDBOX_TOOLS_DISABLED", raising=False)

    def _boom(**_kwargs):
        raise RuntimeError("reset failed")

    monkeypatch.setattr(remediation_tools, "_reset_gce_instance", _boom)
    ctx = _approved_ctx()
    result = remediation_tools.restart_server(system_id, ctx)
    assert result["status"] == "error"
    assert instance in result["message"]
    # 失敗時は復旧済み扱いにしない(get_system_status が healthy に化けない)
    assert system_id not in (ctx.state.get("remediated_systems") or [])


def test_restart_of_other_systems_never_touches_gce(monkeypatch):
    monkeypatch.setenv("SANDBOX_PROJECT_ID", SANDBOX)

    def _fail(**_kwargs):
        raise AssertionError("GCE対象外のシステムで実リセットが呼ばれた")

    monkeypatch.setattr(remediation_tools, "_reset_gce_instance", _fail)
    result = remediation_tools.restart_server("sales-mgmt", _approved_ctx())
    assert result["status"] == "success"


def test_bigquery_logs_tool_requires_sandbox_project(monkeypatch):
    monkeypatch.delenv("SANDBOX_PROJECT_ID", raising=False)
    result = gcp_logs.query_incident_bigquery_logs("Out of memory")
    assert result["status"] == "error"


def test_bigquery_logs_tool_rejects_empty_keyword(monkeypatch):
    monkeypatch.setenv("SANDBOX_PROJECT_ID", SANDBOX)
    result = gcp_logs.query_incident_bigquery_logs("   ")
    assert result["status"] == "error"
