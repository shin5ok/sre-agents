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


def get_sandbox_project_id() -> str | None:
    """GCP障害シナリオの調査対象サンドボックスプロジェクトID。

    環境変数 SANDBOX_PROJECT_ID から取得する(エージェント自身の実行プロジェクト
    GOOGLE_CLOUD_PROJECT とは別物)。未設定なら None を返し、その場合
    GCP調査ツール(Cloud Assist MCP / BigQueryログ検索)は組み込まれず、
    restart_server の実リセットも無効になる。

    SANDBOX_TOOLS_DISABLED が真値なら SANDBOX_PROJECT_ID があっても None。
    .env に SANDBOX_PROJECT_ID を書いた状態でも eval を従来構成で回すための
    明示スイッチ(agents-cli 0.5.1 の eval generate は tools 内の McpToolset を
    関数と誤解して落ちるため、make eval が設定する)。
    """
    if os.environ.get("SANDBOX_TOOLS_DISABLED"):
        return None
    return os.environ.get("SANDBOX_PROJECT_ID") or None
