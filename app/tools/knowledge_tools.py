"""過去の対応ログ検索・運用手順書検索・対応ログ記録ツール。

検索は全文検索エンジンを使わず、少量コーパス前提の
「全件取得 + キーワードスコアリング」で割り切る(純関数はユニットテスト対象)。
"""

import re

from app import mock_world
from app.config import RUNBOOK_DIR
from app.firestore_client import CASES_COLLECTION, INCIDENTS_COLLECTION, get_client
from app.tools.case_tools import _now_jst

_MAX_RESULTS = 3
_MAX_FETCH = 100

# 過去対応ログのうち検索対象にするテキストフィールド
_INCIDENT_TEXT_FIELDS = ("title", "symptom", "root_cause", "action_taken", "system_id")


def _tokenize(query: str) -> list[str]:
    """クエリを空白・読点・記号で分割してトークン列にする。"""
    return [t for t in re.split(r"[\s、。,，/・()()]+", query) if t]  # noqa: RUF001


def _score_text(text: str, tokens: list[str]) -> int:
    """テキストに含まれるトークン数(ユニーク)をスコアとして返す。"""
    return sum(1 for t in tokens if t in text)


def _incident_searchable_text(doc: dict) -> str:
    parts = [str(doc.get(f, "")) for f in _INCIDENT_TEXT_FIELDS]
    parts.extend(doc.get("tags") or [])
    system_id = doc.get("system_id", "")
    if system_id in mock_world.SYSTEMS:
        parts.append(mock_world.SYSTEMS[system_id]["display_name"])
    return " ".join(parts)


def _split_runbook_sections(text: str) -> tuple[str, list[tuple[str, str]]]:
    """Markdown を文書タイトル(# 見出し)と「## 」見出し単位のセクションに分割する。"""
    title = ""
    sections: list[tuple[str, str]] = []
    heading = "(冒頭)"
    lines: list[str] = []
    for line in text.splitlines():
        if line.startswith("# ") and not title:
            title = line.removeprefix("# ").strip()
        elif line.startswith("## "):
            if lines and "".join(lines).strip():
                sections.append((heading, "\n".join(lines).strip()))
            heading = line.removeprefix("## ").strip()
            lines = []
        else:
            lines.append(line)
    if lines and "".join(lines).strip():
        sections.append((heading, "\n".join(lines).strip()))
    return title, sections


def search_past_incidents(keywords: str) -> dict:
    """過去の障害対応ログをキーワードで検索し、類似事例の原因と対応を取得する。

    対応方針を決める前に必ず呼び出すこと。keywords にはシステム名・症状・
    ログの異常内容などを空白区切りで指定する(例: "売上管理 タイムアウト 接続プール")。

    Args:
        keywords: 検索キーワード(空白区切り)

    Returns:
        関連度の高い過去対応ログ(最大3件)。原因・実施した対応・結果を含む。
    """
    tokens = _tokenize(keywords)
    if not tokens:
        return {"status": "error", "message": "検索キーワードを指定してください。"}
    docs = [
        d.to_dict()
        for d in get_client()
        .collection(INCIDENTS_COLLECTION)
        .limit(_MAX_FETCH)
        .stream()
    ]
    scored = [
        (score, doc)
        for doc in docs
        if (score := _score_text(_incident_searchable_text(doc), tokens)) > 0
    ]
    scored.sort(key=lambda pair: (-pair[0], str(pair[1].get("incident_id", ""))))
    incidents = [
        {
            "incident_id": doc.get("incident_id"),
            "system_id": doc.get("system_id"),
            "title": doc.get("title"),
            "symptom": doc.get("symptom"),
            "root_cause": doc.get("root_cause"),
            "action_taken": doc.get("action_taken"),
            "outcome": doc.get("outcome"),
            "escalated": doc.get("escalated"),
            "occurred_at": doc.get("occurred_at"),
        }
        for _, doc in scored[:_MAX_RESULTS]
    ]
    return {"status": "success", "hit_count": len(scored), "incidents": incidents}


def search_runbooks(query: str) -> dict:
    """運用手順書(ドキュメント)をキーワードで検索し、該当する手順を取得する。

    復旧操作を実施する前に必ず呼び出し、その操作が手順書で認められているかを
    確認すること(例: "再起動 手順"、"発注管理 ディスク")。

    Args:
        query: 検索キーワード(空白区切り)

    Returns:
        関連度の高い手順書セクション(最大3件)。
    """
    tokens = _tokenize(query)
    if not tokens:
        return {"status": "error", "message": "検索キーワードを指定してください。"}
    if not RUNBOOK_DIR.is_dir():
        return {
            "status": "error",
            "message": f"手順書ディレクトリがありません: {RUNBOOK_DIR}",
        }
    scored: list[tuple[int, str, str, str]] = []
    for path in sorted(RUNBOOK_DIR.glob("*.md")):
        title, sections = _split_runbook_sections(path.read_text(encoding="utf-8"))
        for heading, content in sections:
            # 文書タイトル(システム名)もスコア対象に含め、システム別手順書が
            # 「発注管理 ...」のようなクエリで確実にヒットするようにする
            score = _score_text(f"{path.name} {title} {heading} {content}", tokens)
            if score > 0:
                scored.append((score, path.name, heading, content))
    scored.sort(key=lambda item: (-item[0], item[1], item[2]))
    sections = [
        {"file": file, "heading": heading, "content": content}
        for _, file, heading, content in scored[:_MAX_RESULTS]
    ]
    return {"status": "success", "hit_count": len(scored), "sections": sections}


def record_resolution_log(
    case_id: str,
    system_id: str,
    title: str,
    root_cause: str,
    action_taken: str,
    outcome: str,
) -> dict:
    """今回の対応内容を対応ログとして記録する(将来の類似障害の検索に使われる)。

    対応完了時(復旧またはエスカレーション)に必ず呼び出すこと。

    Args:
        case_id: 対象ケースID
        system_id: 対象システムの正式ID
        title: 障害の短い表題(例: "売上送信タイムアウト多発")
        root_cause: 特定した原因(ログ上の根拠を含めて具体的に)
        action_taken: 実施した対応(エスカレーションの場合は通知先と理由)
        outcome: 結果。"resolved"(復旧)または "escalated"(エスカレーション)

    Returns:
        記録結果。incident_id を含む。
    """
    if outcome not in ("resolved", "escalated"):
        return {
            "status": "error",
            "message": f"不正な outcome です: {outcome}。有効値: resolved / escalated",
        }
    if system_id not in mock_world.SYSTEMS:
        return {"status": "error", "message": f"未知のシステムIDです: {system_id}"}

    client = get_client()
    case_doc = client.collection(CASES_COLLECTION).document(case_id).get()
    case = case_doc.to_dict() if case_doc.exists else {}

    display_name = mock_world.SYSTEMS[system_id]["display_name"]
    tags = [display_name]
    tags.extend(t for t in _tokenize(f"{title} {root_cause}") if len(t) >= 2)
    tags = list(dict.fromkeys(tags))[:8]

    incident_id = f"INC-{case_id}"
    incident = {
        "incident_id": incident_id,
        "case_id": case_id,
        "system_id": system_id,
        "title": title,
        "symptom": case.get("symptom", ""),
        "root_cause": root_cause,
        "action_taken": action_taken,
        "outcome": outcome,
        "escalated": outcome == "escalated",
        "tags": tags,
        "occurred_at": case.get("started_at", ""),
        "recorded_at": _now_jst(),
        "source": "agent",
    }
    client.collection(INCIDENTS_COLLECTION).document(incident_id).set(incident)
    return {"status": "success", "incident_id": incident_id, "incident": incident}
