"""HITL(復旧実施前の承認)ゲートのユニットテスト。

propose_recovery_plan を通していない restart_server / escalate_to_engineer は
実際の操作を行わずエラーを返す(実施系ツール側の防御深化)ことを確認する。
"""

from types import SimpleNamespace

from app.tools.remediation_tools import (
    escalate_to_engineer,
    propose_recovery_plan,
    restart_server,
)


def _ctx(state: dict | None = None) -> SimpleNamespace:
    return SimpleNamespace(state=state if state is not None else {})


def test_restart_server_rejected_without_plan():
    result = restart_server("sales-mgmt", _ctx())
    assert result["status"] == "error"
    assert "propose_recovery_plan" in result["message"]


def test_escalate_rejected_without_plan():
    result = escalate_to_engineer("CASE-X", "reason", "summary", _ctx())
    assert result["status"] == "error"
    assert "propose_recovery_plan" in result["message"]


def test_restart_rejected_when_plan_is_for_escalate():
    ctx = _ctx({"recovery_plan_proposed": "escalate"})
    result = restart_server("sales-mgmt", ctx)
    assert result["status"] == "error"


def test_escalate_rejected_when_plan_is_for_restart():
    ctx = _ctx({"recovery_plan_proposed": "restart"})
    result = escalate_to_engineer("CASE-X", "reason", "summary", ctx)
    assert result["status"] == "error"


def test_propose_then_restart_succeeds_for_restartable_system():
    ctx = _ctx()
    proposal = propose_recovery_plan(
        case_id="CASE-X",
        root_cause="接続プール枯渇",
        planned_action="APサーバー再起動",
        action_type="restart",
        impact_during_work="再起動中1〜2分は会計処理不可",
        tool_context=ctx,
    )
    assert proposal["status"] == "awaiting_approval"
    assert ctx.state["recovery_plan_proposed"] == "restart"

    result = restart_server("sales-mgmt", ctx)
    assert result["status"] == "success"
    assert "sales-mgmt" in ctx.state["remediated_systems"]


def test_propose_then_escalate_succeeds():
    ctx = _ctx()
    propose_recovery_plan(
        case_id="CASE-Y",
        root_cause="DBストレージ枯渇",
        planned_action="インフラチームへエスカレーション",
        action_type="escalate",
        impact_during_work="ストレージ拡張作業に約30分",
        tool_context=ctx,
    )
    result = escalate_to_engineer("CASE-Y", "reason", "summary", ctx)
    assert result["status"] == "success"
    assert result["notified_team"] == "インフラ運用チーム"


def test_propose_rejects_unknown_action_type():
    result = propose_recovery_plan(
        case_id="CASE-Z",
        root_cause="x",
        planned_action="y",
        action_type="reboot_universe",
        impact_during_work="z",
        tool_context=_ctx(),
    )
    assert result["status"] == "error"


def test_restart_still_blocked_for_non_restartable_system_after_approval():
    """マネージドDB構成システムは、承認があっても再起動できないこと(実世界の制約)。"""
    ctx = _ctx({"recovery_plan_proposed": "restart"})
    result = restart_server("order-mgmt", ctx)
    assert result["status"] == "error"
    assert "マネージド" in result["message"] or "再起動" in result["message"]
