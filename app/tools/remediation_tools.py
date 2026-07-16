"""復旧アクション・エスカレーション・HITL(承認)のツール。

基本はモック実装であり、実環境への差し替えポイント。
実装を差し替える場合も、シグネチャと返却契約({"status": ...} を含む dict)は維持すること。
(例: restart_server → Compute Engine API のインスタンス再起動、
 escalate_to_engineer → チケットシステム起票 + オンコール通知)

差し替えの実例: mock_world のエントリに gce_restart_instance がある
システム(store-web-fleet)は、SANDBOX_PROJECT_ID 設定時に restart_server が
Compute Engine API で対象インスタンスを実際にリセットする(それ以外は従来のモック)。

HITL(Human-in-the-Loop): 破壊的な復旧操作を実施する前に、必ず
propose_recovery_plan で計画を提示して担当者の承認を得るフローになっている。
restart_server / escalate_to_engineer は state["recovery_plan_proposed"] が
一致していない場合はエラーを返して実行を拒否する。
"""

from google.adk.tools import ToolContext

from app.config import get_sandbox_project_id
from app.mock_world import SYSTEMS

VALID_ACTION_TYPES = ("restart", "escalate")


def _reset_gce_instance(project_id: str, zone: str, instance: str) -> None:
    """Compute Engine インスタンスをリセットし、API操作の完了まで待つ。

    リセット操作自体は数秒で完了する(OS起動はその後1分程度続く)。
    テストではこの関数を monkeypatch して実呼び出しを差し替える。
    """
    from google.cloud import compute_v1

    client = compute_v1.InstancesClient()
    operation = client.reset(project=project_id, zone=zone, instance=instance)
    operation.result(timeout=90)


def propose_recovery_plan(
    case_id: str,
    root_cause: str,
    planned_action: str,
    action_type: str,
    impact_during_work: str,
    tool_context: ToolContext,
) -> dict:
    """復旧作業を実施する前に、対応計画を担当者へ提示して承認を求める(HITL)。

    restart_server または escalate_to_engineer を呼び出す前に必ず本ツールを呼ぶこと。
    本ツール自体は実際の復旧操作を行わない(計画の登録・提示のみ)。
    呼び出し後は原則として応答を一旦終了し、次のユーザー入力で承認を待つこと。

    Args:
        case_id: 対象ケースID
        root_cause: 特定した原因(ログの根拠を含めて具体的に)
        planned_action: 実施予定の対応の具体的内容
            (例:「APサーバーの再起動(rolling restart)」「インフラ運用チームへエスカレーションしDBストレージ拡張を依頼」)
        action_type: 対応の種別。"restart"(サーバー再起動)または "escalate"(エスカレーション)
        impact_during_work: 作業中に生じる業務影響
            (何が何分ほど停止するか・他店舗への波及・エスカレーションの場合は担当チームでの想定所要時間)

    Returns:
        承認待ちであることを示すレスポンス。
    """
    if action_type not in VALID_ACTION_TYPES:
        return {
            "status": "error",
            "message": (
                f"不正な action_type です: {action_type}。"
                f"有効値: {' / '.join(VALID_ACTION_TYPES)}"
            ),
        }
    plan = {
        "case_id": case_id,
        "root_cause": root_cause,
        "planned_action": planned_action,
        "action_type": action_type,
        "impact_during_work": impact_during_work,
    }
    tool_context.state["recovery_plan_proposed"] = action_type
    tool_context.state["recovery_plan"] = plan
    return {
        "status": "awaiting_approval",
        "case_id": case_id,
        "plan": plan,
        "message": (
            "上記の対応計画を担当者に提示しました。"
            "担当者からの明示的な承認(「承認します」「進めてください」等)を確認してから、"
            "action_type に対応する実施ツール(restart_server / escalate_to_engineer)を呼び出してください。"
            "承認がない状態では実施ツールを呼び出さないでください。"
        ),
    }


def restart_server(system_id: str, tool_context: ToolContext) -> dict:
    """対象システムのアプリケーションサーバーを再起動する(復旧オペレーション)。

    実施前に必ず propose_recovery_plan で action_type="restart" の計画を提示し、
    担当者の承認を得ていること。実行後は get_system_status で回復を確認すること。

    Args:
        system_id: 対象システムの正式ID(例: "sales-mgmt")

    Returns:
        再起動の実行結果。承認未取得または再起動権限がないシステムでは error。
    """
    if tool_context.state.get("recovery_plan_proposed") != "restart":
        return {
            "status": "error",
            "message": (
                "HITL 未完了のため実行を拒否しました。"
                "先に propose_recovery_plan(action_type='restart') で対応計画を提示し、"
                "担当者の承認を得てから再度呼び出してください。"
            ),
        }
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

    # 実環境差し替え済みのシステム(GCEホスト)は対象インスタンスを実際にリセットする
    gce_instance = entry.get("gce_restart_instance")
    sandbox_project = get_sandbox_project_id()
    if gce_instance and sandbox_project:
        try:
            _reset_gce_instance(
                project_id=sandbox_project,
                zone=entry["gce_zone"],
                instance=gce_instance,
            )
        except Exception as e:
            return {
                "status": "error",
                "system_id": system_id,
                "message": (
                    f"インスタンス {gce_instance} の再起動に失敗しました: {e}。"
                    "escalate_to_engineer でのエスカレーションを検討してください。"
                ),
            }
        message = (
            f"{entry['display_name']} の対象インスタンス {gce_instance}"
            f"(project={sandbox_project})を Compute Engine API でリセットしました。"
            "OSの起動完了まで1分程度かかります。get_system_status で稼働状態を確認してください。"
        )
    else:
        message = (
            f"{entry['display_name']} のアプリケーションサーバーを再起動しました。"
            "get_system_status で稼働状態を確認してください。"
        )

    remediated = list(tool_context.state.get("remediated_systems") or [])
    if system_id not in remediated:
        remediated.append(system_id)
    tool_context.state["remediated_systems"] = remediated
    return {
        "status": "success",
        "system_id": system_id,
        "message": message,
    }


def escalate_to_engineer(
    case_id: str, reason: str, summary: str, tool_context: ToolContext
) -> dict:
    """自動対応できない障害を担当エンジニアチームへエスカレーションする。

    実施前に必ず propose_recovery_plan で action_type="escalate" の計画を提示し、
    担当者の承認を得ていること。手順書がエスカレーションを指示している場合、
    定型対応が失敗した場合、または対応方法に確信が持てない場合に使用する。

    Args:
        case_id: 対象ケースID
        reason: エスカレーションが必要な理由
        summary: これまでの調査内容の要約(原因の見立て・確認したログ・試した対応)

    Returns:
        エスカレーション結果。通知先チームを含む。
    """
    if tool_context.state.get("recovery_plan_proposed") != "escalate":
        return {
            "status": "error",
            "message": (
                "HITL 未完了のため実行を拒否しました。"
                "先に propose_recovery_plan(action_type='escalate') で対応計画を提示し、"
                "担当者の承認を得てから再度呼び出してください。"
            ),
        }
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
