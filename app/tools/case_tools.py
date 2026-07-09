"""サポートケース(support_cases コレクション)の起票・参照・更新ツール。"""

import datetime
from zoneinfo import ZoneInfo

from google.adk.tools import ToolContext
from google.cloud import firestore

from app import mock_world
from app.firestore_client import CASES_COLLECTION, get_client

VALID_STATUSES = ("open", "investigating", "resolved", "escalated")


def _now_jst() -> str:
    return datetime.datetime.now(ZoneInfo("Asia/Tokyo")).isoformat(timespec="seconds")


def create_support_case(
    store_id: str,
    store_name: str,
    system_id: str,
    symptom: str,
    started_at: str,
    impact_scope: str,
    reporter_name: str,
    tool_context: ToolContext,
) -> dict:
    """聞き取りが完了した障害報告をサポートケースとしてデータベースに起票する。

    必須項目(店舗ID・店舗名・対象システムID・症状・発生日時・影響範囲・報告者名)が
    すべて揃ってから呼び出すこと。

    Args:
        store_id: 店舗ID(例: "1102")
        store_name: 店舗名(例: "渋谷店")
        system_id: 対象システムの正式ID。list_managed_systems が返すIDを指定する(例: "sales-mgmt")
        symptom: 具体的な症状(エラーメッセージや画面の状態を含む)
        started_at: 発生日時(報告者の申告どおりの表現でよい)
        impact_scope: 影響範囲(何台・何店舗・どの業務に支障が出ているか)
        reporter_name: 報告者名

    Returns:
        起票結果。成功時は case_id を含む。
    """
    if system_id not in mock_world.SYSTEMS:
        return {
            "status": "error",
            "message": f"未知のシステムIDです: {system_id}。"
            "list_managed_systems で正式なIDを確認してください。",
        }
    now = datetime.datetime.now(ZoneInfo("Asia/Tokyo"))
    case_id = f"CASE-{now:%Y%m%d-%H%M%S}"
    case = {
        "case_id": case_id,
        "store_id": store_id,
        "store_name": store_name,
        "system_id": system_id,
        "system_name": mock_world.SYSTEMS[system_id]["display_name"],
        "symptom": symptom,
        "started_at": started_at,
        "impact_scope": impact_scope,
        "reporter_name": reporter_name,
        "status": "open",
        "history": [
            {
                "ts": now.isoformat(timespec="seconds"),
                "status": "open",
                "note": "受付エージェントが起票",
            }
        ],
        "created_at": now.isoformat(timespec="seconds"),
        "updated_at": now.isoformat(timespec="seconds"),
    }
    get_client().collection(CASES_COLLECTION).document(case_id).set(case)
    tool_context.state["case_id"] = case_id
    tool_context.state["system_id"] = system_id
    return {"status": "success", "case_id": case_id, "case": case}


def get_support_case(case_id: str) -> dict:
    """起票済みサポートケースの内容をデータベースから取得する。

    Args:
        case_id: ケースID(例: "CASE-20260709-101530")

    Returns:
        ケースの全項目。存在しない場合は error。
    """
    doc = get_client().collection(CASES_COLLECTION).document(case_id).get()
    if not doc.exists:
        return {"status": "error", "message": f"ケースが見つかりません: {case_id}"}
    return {"status": "success", "case": doc.to_dict()}


def update_case_status(case_id: str, status: str, note: str) -> dict:
    """サポートケースのステータスを更新し、履歴に記録する。

    Args:
        case_id: ケースID
        status: 新しいステータス。"open" / "investigating" / "resolved" / "escalated" のいずれか
        note: 更新理由や状況のメモ(日本語)

    Returns:
        更新結果。
    """
    if status not in VALID_STATUSES:
        return {
            "status": "error",
            "message": f"不正なステータスです: {status}。有効値: {', '.join(VALID_STATUSES)}",
        }
    ref = get_client().collection(CASES_COLLECTION).document(case_id)
    if not ref.get().exists:
        return {"status": "error", "message": f"ケースが見つかりません: {case_id}"}
    now = _now_jst()
    ref.update(
        {
            "status": status,
            "history": firestore.ArrayUnion(
                [{"ts": now, "status": status, "note": note}]
            ),
            "updated_at": now,
        }
    )
    return {"status": "success", "case_id": case_id, "new_status": status}
