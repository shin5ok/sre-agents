"""起票前レビュー(受付HITL)ゲートのユニットテスト。

propose_case_draft を通していない create_support_case は Firestore に書き込まず
エラーを返す(起票ツール側の防御深化)ことを確認する。復旧側の HITL ゲート
(test_hitl_gating.py)と対になるテスト。
"""

from types import SimpleNamespace

from app.tools import case_tools

DRAFT_ARGS = {
    "store_id": "1102",
    "store_name": "渋谷店",
    "system_id": "sales-mgmt",
    "symptom": "レジ会計時に売上送信がタイムアウト",
    "started_at": "今朝9時ごろ",
    "impact_scope": "レジ2台、他業務は正常",
    "reporter_name": "佐藤",
}


def _ctx(state: dict | None = None) -> SimpleNamespace:
    return SimpleNamespace(state=state if state is not None else {})


class _FakeFirestore:
    """collection().document().set() の呼び出しだけ記録する疑似クライアント。"""

    def __init__(self):
        self.saved: dict[str, dict] = {}
        self._doc_id: str | None = None

    def collection(self, _name):
        return self

    def document(self, doc_id):
        self._doc_id = doc_id
        return self

    def set(self, data):
        self.saved[self._doc_id] = data


def _patch_firestore(monkeypatch) -> _FakeFirestore:
    fake = _FakeFirestore()
    monkeypatch.setattr(case_tools, "get_client", lambda: fake)
    return fake


def test_create_rejected_without_draft(monkeypatch):
    fake = _patch_firestore(monkeypatch)
    result = case_tools.create_support_case(**DRAFT_ARGS, tool_context=_ctx())
    assert result["status"] == "error"
    assert "propose_case_draft" in result["message"]
    assert fake.saved == {}  # DBには書き込まない


def test_propose_then_create_succeeds(monkeypatch):
    fake = _patch_firestore(monkeypatch)
    ctx = _ctx()
    proposal = case_tools.propose_case_draft(**DRAFT_ARGS, tool_context=ctx)
    assert proposal["status"] == "awaiting_confirmation"
    assert ctx.state["case_draft"]["system_id"] == "sales-mgmt"
    assert proposal["draft"]["system_name"] == "売上管理システム"

    result = case_tools.create_support_case(**DRAFT_ARGS, tool_context=ctx)
    assert result["status"] == "success"
    assert result["case_id"].startswith("CASE-")
    assert list(fake.saved) == [result["case_id"]]
    assert ctx.state["case_id"] == result["case_id"]
    # 起票後はドラフトがクリアされ、次のケースには改めてレビューが必要
    assert not ctx.state["case_draft"]


def test_create_rejected_when_system_differs_from_draft(monkeypatch):
    fake = _patch_firestore(monkeypatch)
    ctx = _ctx()
    case_tools.propose_case_draft(**DRAFT_ARGS, tool_context=ctx)
    result = case_tools.create_support_case(
        **{**DRAFT_ARGS, "system_id": "order-mgmt"}, tool_context=ctx
    )
    assert result["status"] == "error"
    assert "propose_case_draft" in result["message"]
    assert fake.saved == {}


def test_second_create_requires_new_review(monkeypatch):
    fake = _patch_firestore(monkeypatch)
    ctx = _ctx()
    case_tools.propose_case_draft(**DRAFT_ARGS, tool_context=ctx)
    first = case_tools.create_support_case(**DRAFT_ARGS, tool_context=ctx)
    assert first["status"] == "success"

    second = case_tools.create_support_case(**DRAFT_ARGS, tool_context=ctx)
    assert second["status"] == "error"
    assert len(fake.saved) == 1


def test_propose_rejects_unknown_system():
    result = case_tools.propose_case_draft(
        **{**DRAFT_ARGS, "system_id": "unknown-sys"}, tool_context=_ctx()
    )
    assert result["status"] == "error"
    assert "unknown-sys" in result["message"]
