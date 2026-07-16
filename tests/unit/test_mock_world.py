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
        "store-app": "returned 5xx",
        "store-web-fleet": "memory usage 95%",
    }
    for system_id, marker in anomalies.items():
        joined = "\n".join(SYSTEMS[system_id]["log_excerpt"])
        assert marker in joined, f"{system_id} のログに診断根拠となる異常行がない"
        assert len(SYSTEMS[system_id]["log_excerpt"]) >= 8


def test_gce_restart_entries_are_consistent():
    """gce_restart_instance を持つシステム(restart_server が実リセットする対象)の整合性。"""
    gce_systems = {s: e for s, e in SYSTEMS.items() if e.get("gce_restart_instance")}
    assert set(gce_systems) == {"store-web-fleet", "inventory-mgmt"}
    for system_id, entry in gce_systems.items():
        assert entry.get("gce_zone"), f"{system_id}: gce_zone が未定義"
        assert entry["restartable"] is True, (
            f"{system_id}: 実リセット対象なのに restartable=False"
        )


def test_demo_covers_both_restart_and_escalation_paths():
    restartable = [s for s, e in SYSTEMS.items() if e["restartable"]]
    blocked = [s for s, e in SYSTEMS.items() if not e["restartable"]]
    assert len(restartable) >= 2
    assert len(blocked) >= 1
