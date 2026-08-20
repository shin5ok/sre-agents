"""報告者の依頼文から HITL 2ゲートの事前承認を判定する。

判定は LLM ではなく決定的なキーワード照合で行う。承認ゲートの通過可否に
モデルの解釈を介在させないための意図的な設計であり、同時に
「そのまま実施してください」で完走するワンショットデモ・eval を成立させる。

2つのゲートは独立に判定する:
- intake: 起票前レビュー(調査開始前に報告内容を確認するステップ)を省略してよいか
- recovery: 復旧計画の承認待ちを省略して実施まで進んでよいか

「このまま起票して調査を進めてください。復旧作業は私の承認を得てから
進めてください」のように、起票だけ事前承認して復旧は承認待ちにする依頼が
あるため、recovery 側には否定表現のガードを置く。
"""

from typing import NamedTuple

# 起票前レビューの省略を許す表現
_INTAKE_KEYWORDS = (
    "このまま起票",
    "そのまま起票",
    "この内容で起票",
    "以上の内容で",
    "報告内容は以上の通り",
    "この内容でお願い",
    "追加の承認・確認は不要",
    "追加の承認は不要",
    "事前承認済み",
    "そのまま実施",
    "そのまま復旧",
)

# 復旧作業の承認待ちの省略を許す表現
_RECOVERY_KEYWORDS = (
    "追加の承認・確認は不要",
    "追加の承認は不要",
    "事前承認済み",
    "承認済み",
    "そのまま実施",
    "そのまま復旧",
    "そのまま進めて",
    "そのまま対応",
)

# 復旧は承認待ちにする意思を示す表現(_RECOVERY_KEYWORDS より優先する)
_RECOVERY_BLOCKERS = (
    "承認を得てから",
    "承認をいただいてから",
    "承認を得た上で",
    "承認後に",
    "承認してから",
)


class PreApproval(NamedTuple):
    """2ゲートそれぞれの事前承認の有無。"""

    intake: bool
    recovery: bool


def detect_pre_approval(text: str) -> PreApproval:
    """依頼文から起票前レビュー・復旧承認の事前承認を判定する。

    Args:
        text: 報告者の依頼文(初回の障害報告)

    Returns:
        intake / recovery それぞれの事前承認の有無。
    """
    if not text:
        return PreApproval(intake=False, recovery=False)
    intake = any(kw in text for kw in _INTAKE_KEYWORDS)
    recovery = any(kw in text for kw in _RECOVERY_KEYWORDS) and not any(
        kw in text for kw in _RECOVERY_BLOCKERS
    )
    return PreApproval(intake=intake, recovery=recovery)


# 起票前レビュー・復旧計画の確認に対する肯定応答
_AFFIRMATIVE_KEYWORDS = (
    "はい",
    "お願いします",
    "お願いいたします",
    "承認",
    "了解",
    "問題ありません",
    "問題ない",
    "その内容で",
    "この内容で",
    "進めて",
    "実施して",
    "実行して",
    "ok",
    "yes",
)

# 肯定語を含んでいても否定として扱う表現
_NEGATIVE_KEYWORDS = (
    "いいえ",
    "しないで",
    "やめて",
    "中止",
    "待って",
    "却下",
    "承認しません",
    "承認できません",
    "修正",
    "訂正",
    "間違",
    "違います",
    "違う",
    "ではなく",
)


def is_affirmative(text: str) -> bool:
    """報告者・担当者の応答が承認(肯定)かどうかを判定する。

    否定・修正依頼の表現が含まれる場合は、肯定語があっても False を返す。

    Args:
        text: 報告者または担当者の応答

    Returns:
        承認とみなせる場合 True。
    """
    if not text:
        return False
    lowered = text.lower()
    if any(kw in lowered for kw in _NEGATIVE_KEYWORDS):
        return False
    return any(kw in lowered for kw in _AFFIRMATIVE_KEYWORDS)
