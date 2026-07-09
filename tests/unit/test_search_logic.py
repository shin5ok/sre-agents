"""ナレッジ検索の純関数ロジックのテスト(Firestore・LLM 非依存)。"""

from app.config import RUNBOOK_DIR
from app.tools.knowledge_tools import (
    _incident_searchable_text,
    _score_text,
    _split_runbook_sections,
    _tokenize,
    search_runbooks,
)


def test_tokenize_splits_japanese_punctuation_and_whitespace():
    assert _tokenize("売上管理 タイムアウト、接続プール") == [
        "売上管理",
        "タイムアウト",
        "接続プール",
    ]
    assert _tokenize("   ") == []


def test_score_text_counts_unique_token_hits():
    text = "DB接続プールの枯渇によるタイムアウト"
    assert _score_text(text, ["タイムアウト", "接続プール", "OOM"]) == 2


def test_incident_searchable_text_includes_tags_and_display_name():
    doc = {
        "title": "障害",
        "symptom": "症状",
        "root_cause": "原因",
        "action_taken": "対応",
        "system_id": "sales-mgmt",
        "tags": ["接続プール"],
    }
    text = _incident_searchable_text(doc)
    assert "接続プール" in text
    assert "売上管理システム" in text


def test_split_runbook_sections_extracts_title_and_headings():
    md = "# タイトル\n\n前文\n\n## 手順A\n内容A\n\n## 手順B\n内容B\n"
    title, sections = _split_runbook_sections(md)
    assert title == "タイトル"
    assert [h for h, _ in sections] == ["(冒頭)", "手順A", "手順B"]
    assert sections[1] == ("手順A", "内容A")


def test_runbook_search_hits_the_right_procedure():
    assert RUNBOOK_DIR.is_dir()
    result = search_runbooks("発注管理 ストレージ")
    assert result["status"] == "success"
    files = {s["file"] for s in result["sections"]}
    assert "order-mgmt.md" in files


def test_runbook_search_rejects_empty_query():
    assert search_runbooks("   ")["status"] == "error"
