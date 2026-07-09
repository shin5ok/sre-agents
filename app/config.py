"""実行環境に依存する設定値。"""

import os
from pathlib import Path

# 両エージェント共通のモデル。このプロジェクトの Vertex AI には
# gemini-flash-latest エイリアスが存在しないため、具体的なIDを固定する。
MODEL_ID = "gemini-3.5-flash"

# Firestore の named database(asia-northeast1)
FIRESTORE_DATABASE = os.environ.get("FIRESTORE_DATABASE", "sre-agents")

# adk web / agents-cli run / eval で CWD が異なるため、パスはこのファイル基準で解決する
RUNBOOK_DIR = Path(__file__).resolve().parents[1] / "data" / "runbooks"


def get_project_id() -> str:
    project = os.environ.get("GOOGLE_CLOUD_PROJECT")
    if project:
        return project
    import google.auth

    _, project = google.auth.default()
    if not project:
        raise RuntimeError(
            "GCPプロジェクトを特定できません。GOOGLE_CLOUD_PROJECT を設定してください。"
        )
    return project
