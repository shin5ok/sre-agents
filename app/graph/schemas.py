"""グラフのノード間で受け渡すデータ構造。

LLM ノード(受付・調査)の出力はここで定義したスキーマに固定し、
後続の関数ノードが決定的に分岐できるようにする。

逆方向(関数ノード → LLM ノード)の受け渡しには render() の生成する
日本語テキストを使う。node_input が BaseModel / dict だと ADK は JSON 化して
モデルに渡すため(日本語が \\uXXXX にエスケープされる)、可読なプレーンテキストに
してから渡す。
"""

from typing import Literal

from pydantic import BaseModel, Field

# 起票に必要な項目とその日本語ラベル(不足項目の質問文に使う)
REQUIRED_INTAKE_FIELDS: dict[str, str] = {
    "store_id": "店舗ID",
    "store_name": "店舗名",
    "system_id": "対象システム",
    "symptom": "具体的な症状",
    "started_at": "発生日時",
    "impact_scope": "影響範囲",
    "reporter_name": "報告者名",
}


class Exchange(BaseModel):
    """聞き取りの一往復(受付エージェントの質問と報告者の回答)。"""

    question: str = ""
    answer: str = ""


class Transcript(BaseModel):
    """受付エージェントに毎回渡す聞き取り記録。

    受付エージェントはグラフノード(single_turn)なので会話履歴を持たない。
    後退エッジで再入するたびに、初回報告とこれまでの質疑をまとめて渡すことで、
    聞き取りの継続を会話履歴の組み立てに依存させずに成立させる。
    """

    initial_report: str = ""
    exchanges: list[Exchange] = Field(default_factory=list)

    def render(self) -> str:
        """受付エージェントへの入力にする日本語テキストを組み立てる。"""
        lines = [
            "# 報告者からの初回報告",
            self.initial_report or "(まだ具体的な障害報告はありません)",
        ]
        if self.exchanges:
            lines.append("")
            lines.append("# これまでの聞き取り(あなたの質問と報告者の回答)")
            for i, ex in enumerate(self.exchanges, 1):
                lines.append(f"{i}. 質問: {ex.question}")
                lines.append(f"   回答: {ex.answer}")
        return "\n".join(lines)


class IntakeResult(BaseModel):
    """受付エージェントの構造化出力。"""

    status: Literal["welcome", "need_more_info", "ready"] = Field(
        description=(
            "welcome=挨拶等で障害報告がまだない / "
            "need_more_info=必須項目が不足している / "
            "ready=必須項目がすべて揃った"
        )
    )
    message: str = Field(
        default="",
        description="報告者への応答文。need_more_info では不足項目の質問を入れる。",
    )
    store_id: str = ""
    store_name: str = ""
    system_id: str = Field(
        default="",
        description="list_managed_systems が返す正式なシステムID(例: sales-mgmt)",
    )
    symptom: str = ""
    started_at: str = ""
    impact_scope: str = ""
    reporter_name: str = ""

    def missing_fields(self) -> list[str]:
        """未入力の必須項目の日本語ラベルを返す。"""
        return [
            label
            for field, label in REQUIRED_INTAKE_FIELDS.items()
            if not str(getattr(self, field, "") or "").strip()
        ]


class CaseFiled(BaseModel):
    """起票済みケースの要約(調査エージェントへの入力)。"""

    case_id: str
    store_id: str = ""
    store_name: str = ""
    system_id: str = ""
    system_name: str = ""
    symptom: str = ""
    started_at: str = ""
    impact_scope: str = ""
    reporter_name: str = ""

    def render(self) -> str:
        """調査エージェントへの入力にする日本語テキストを組み立てる。"""
        return "\n".join(
            [
                "# 調査対象のサポートケース",
                f"- ケースID: {self.case_id}",
                f"- 対象システム: {self.system_name}(system_id: {self.system_id})",
                f"- 店舗: {self.store_name}(店舗ID: {self.store_id})",
                f"- 症状: {self.symptom}",
                f"- 発生日時: {self.started_at}",
                f"- 影響範囲: {self.impact_scope}",
                f"- 報告者: {self.reporter_name}",
            ]
        )


class RecoveryPlan(BaseModel):
    """調査エージェントの構造化出力(復旧計画)。

    case_id / system_id は state 側(起票結果)が正なので、ここには持たせない。
    """

    title: str = Field(description="対応ログ用の短い表題(例: 売上送信タイムアウト多発)")
    root_cause: str = Field(description="特定した原因")
    evidence: str = Field(
        default="",
        description="原因の根拠。確認したログ行・手順書・過去事例を引用する。",
    )
    planned_action: str = Field(description="実施予定の対応の具体的内容")
    action_type: Literal["restart", "escalate"] = Field(
        description="restart=サーバー再起動 / escalate=エスカレーション"
    )
    impact_during_work: str = Field(description="作業中に生じる業務影響")

    def render(self) -> str:
        """担当者に提示する対応計画の日本語テキストを組み立てる。"""
        action_label = (
            "サーバー再起動" if self.action_type == "restart" else "エスカレーション"
        )
        lines = [
            "# 対応計画(ご承認をお願いします)",
            f"- 特定した原因: {self.root_cause}",
        ]
        if self.evidence.strip():
            lines.append(f"- 根拠: {self.evidence}")
        lines.extend(
            [
                f"- 実施予定の対応: {self.planned_action}",
                f"- 対応種別: {action_label}",
                f"- 作業中の業務影響: {self.impact_during_work}",
            ]
        )
        return "\n".join(lines)


class ActionResult(BaseModel):
    """復旧操作(再起動 / エスカレーション)の実行結果。"""

    ok: bool
    action_type: Literal["restart", "escalate"] = "restart"
    detail: str = ""
