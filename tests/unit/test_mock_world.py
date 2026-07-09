"""mock_world レジストリの整合性テスト(デモシナリオが破綻していないことを保証)。"""

from app.mock_world import SYSTEMS

REQUIRED_KEYS = {
    "display_name",
    "description",
    "status",
    "status_detail",
    "restartable",
    "healthy_detail",
    "log_excerpt",
}


def test_all_systems_have_required_keys():
    for system_id, entry in SYSTEMS.items():
        missing = REQUIRED_KEYS - entry.keys()
        assert not missing, f"{system_id} に不足キー: {missing}"


def test_statuses_are_valid():
    assert {e["status"] for e in SYSTEMS.values()} <= {"healthy", "degraded", "down"}


def test_non_restartable_systems_have_block_reason():
    for system_id, entry in SYSTEMS.items():
        if not entry["restartable"]:
            assert entry.get("restart_block_reason"), (
                f"{system_id}: 再起動不可の理由が未定義"
            )


def test_log_excerpts_contain_expected_anomalies():
    anomalies = {
        "sales-mgmt": "HikariPool-1 - Connection is not available",
        "inventory-mgmt": "java.lang.OutOfMemoryError",
        "order-mgmt": "No space left on device",
    }
    for system_id, marker in anomalies.items():
        joined = "\n".join(SYSTEMS[system_id]["log_excerpt"])
        assert marker in joined, f"{system_id} のログに診断根拠となる異常行がない"
        assert len(SYSTEMS[system_id]["log_excerpt"]) >= 8


def test_demo_covers_both_restart_and_escalation_paths():
    restartable = [s for s, e in SYSTEMS.items() if e["restartable"]]
    blocked = [s for s, e in SYSTEMS.items() if not e["restartable"]]
    assert len(restartable) >= 2
    assert len(blocked) >= 1
