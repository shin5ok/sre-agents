"""復旧アクションとエスカレーションのツール。

このモジュールはモック実装であり、実環境への差し替えポイント。
実装を差し替える場合も、シグネチャと返却契約({"status": ...} を含む dict)は維持すること。
(例: restart_server → Compute Engine API のインスタンス再起動、
 escalate_to_engineer → チケットシステム起票 + オンコール通知)
"""

from google.adk.tools import ToolContext

from app.mock_world import SYSTEMS


def restart_server(system_id: str, tool_context: ToolContext) -> dict:
    """対象システムのアプリケーションサーバーを再起動する(復旧オペレーション)。

    運用手順書または過去の対応ログで再起動が有効と確認できた場合のみ実行すること。
    実行後は get_system_status で回復したことを必ず確認すること。

    Args:
        system_id: 対象システムの正式ID(例: "sales-mgmt")

    Returns:
        再起動の実行結果。再起動権限がないシステムでは error。
    """
    entry = SYSTEMS.get(system_id)
    if entry is None:
        return {"status": "error", "message": f"未知のシステムIDです: {system_id}"}
    if not entry["restartable"]:
        return {
            "status": "error",
            "system_id": system_id,
            "message": entry.get(
                "restart_block_reason", "このシステムは再起動できません。"
            )
            + " 手順書に従い escalate_to_engineer でエスカレーションしてください。",
        }
    remediated = list(tool_context.state.get("remediated_systems") or [])
    if system_id not in remediated:
        remediated.append(system_id)
    tool_context.state["remediated_systems"] = remediated
    return {
        "status": "success",
        "system_id": system_id,
        "message": f"{entry['display_name']} のアプリケーションサーバーを再起動しました。"
        "get_system_status で稼働状態を確認してください。",
    }


def escalate_to_engineer(case_id: str, reason: str, summary: str) -> dict:
    """自動対応できない障害を担当エンジニアチームへエスカレーションする。

    手順書がエスカレーションを指示している場合、定型対応が失敗した場合、
    または対応方法に確信が持てない場合に使用する。

    Args:
        case_id: 対象ケースID
        reason: エスカレーションが必要な理由
        summary: これまでの調査内容の要約(原因の見立て・確認したログ・試した対応)

    Returns:
        エスカレーション結果。通知先チームを含む。
    """
    escalation_id = f"ESC-{case_id}"
    return {
        "status": "success",
        "escalation_id": escalation_id,
        "case_id": case_id,
        "notified_team": "インフラ運用チーム",
        "message": "インフラ運用チームへエスカレーションを送信しました。"
        "record_resolution_log で対応ログを記録し、"
        "update_case_status でステータスを escalated に更新してください。",
    }
