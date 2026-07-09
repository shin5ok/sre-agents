"""システム状態・サーバーログの確認ツール(モックワールド参照)。"""

from google.adk.tools import ToolContext

from app.mock_world import SYSTEMS


def list_managed_systems() -> dict:
    """管理対象システムの一覧(正式なシステムIDと表示名)を取得する。

    報告された障害がどのシステムのものか特定するために使う。

    Returns:
        システムID・表示名・説明のリスト。
    """
    return {
        "status": "success",
        "systems": [
            {
                "system_id": system_id,
                "display_name": entry["display_name"],
                "description": entry["description"],
            }
            for system_id, entry in SYSTEMS.items()
        ],
    }


def get_system_status(system_id: str, tool_context: ToolContext) -> dict:
    """指定システムの現在の稼働状態を確認する。

    Args:
        system_id: 対象システムの正式ID(例: "sales-mgmt")

    Returns:
        稼働状態(healthy / degraded / down)と詳細。
    """
    entry = SYSTEMS.get(system_id)
    if entry is None:
        return {"status": "error", "message": f"未知のシステムIDです: {system_id}"}
    remediated = tool_context.state.get("remediated_systems") or []
    if system_id in remediated:
        return {
            "status": "success",
            "system_id": system_id,
            "display_name": entry["display_name"],
            "operational_status": "healthy",
            "detail": entry["healthy_detail"],
        }
    return {
        "status": "success",
        "system_id": system_id,
        "display_name": entry["display_name"],
        "operational_status": entry["status"],
        "detail": entry["status_detail"],
    }


def get_server_logs(system_id: str) -> dict:
    """指定システムのサーバーログ(直近の抜粋)を取得する。

    ERROR / FATAL / WARN の行に注目して異常を特定すること。

    Args:
        system_id: 対象システムの正式ID(例: "sales-mgmt")

    Returns:
        ログ行のリスト。
    """
    entry = SYSTEMS.get(system_id)
    if entry is None:
        return {"status": "error", "message": f"未知のシステムIDです: {system_id}"}
    return {
        "status": "success",
        "system_id": system_id,
        "display_name": entry["display_name"],
        "log_excerpt": entry["log_excerpt"],
    }
