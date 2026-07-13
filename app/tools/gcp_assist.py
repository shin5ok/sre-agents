"""Gemini Cloud Assist(リモートMCPサーバー)連携。

GCP上に実在するシステム(例: store-app / Cloud Run)の障害調査のため、
Gemini Cloud Assist のリモート MCP サーバーを調査エージェントのツールセットとして
組み込む。docs: https://docs.cloud.google.com/cloud-assist/use-gemini-cloud-assist-mcp

- エンドポイント: https://geminicloudassist.googleapis.com/mcp(グローバル / Streamable HTTP)
- 認証: ADC(Application Default Credentials)の OAuth2 アクセストークンを
  header_provider で毎回供給する(期限切れ時は自動リフレッシュ)
- 調査対象プロジェクトIDは環境変数 SANDBOX_PROJECT_ID から取得する。
  未設定ならツールセット自体を組み込まない(従来のモック調査のみ)。
  eval / CI は SANDBOX_PROJECT_ID なしで実行される前提のため挙動が変わらない。

利用前提(サンドボックスプロジェクト側):
- geminicloudassist.googleapis.com API の有効化(scripts/gcp_incident.sh setup が実施)
- 呼び出しプリンシパルに roles/mcp.toolUser と roles/geminicloudassist.user
- Cloud Assist MCP はプレビュー機能(Pre-GA)
"""

import asyncio

import google.auth
import google.auth.transport.requests

from app.config import get_sandbox_project_id

CLOUD_ASSIST_MCP_URL = "https://geminicloudassist.googleapis.com/mcp"

# 調査(読み取り)用途のみ公開する。design_infra / optimize_costs / invoke_operation は
# 障害調査という本エージェントの役割の外なので意図的に外している。
CLOUD_ASSIST_TOOLS = ["investigate_issue", "ask_cloud_assist"]

_SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]

_credentials = None


def _get_fresh_credentials():
    """ADC を取得し、トークンが無効なら同期リフレッシュして返す。"""
    global _credentials
    if _credentials is None:
        _credentials, _ = google.auth.default(scopes=_SCOPES)
    if not _credentials.valid:
        _credentials.refresh(google.auth.transport.requests.Request())
    return _credentials


async def auth_header_provider(_readonly_context) -> dict[str, str]:
    """MCP セッション確立・ツール呼び出しのたびに認証ヘッダーを供給する。"""
    creds = await asyncio.to_thread(_get_fresh_credentials)
    headers = {"Authorization": f"Bearer {creds.token}"}
    # ユーザー認証のADCで quota project 未設定だと 403 になるため、
    # サンドボックスプロジェクトをクォータプロジェクトとして明示する
    if not getattr(creds, "quota_project_id", None):
        project_id = get_sandbox_project_id()
        if project_id:
            headers["x-goog-user-project"] = project_id
    return headers


def build_cloud_assist_toolset():
    """Cloud Assist の MCP ツールセットを生成する。

    SANDBOX_PROJECT_ID 未設定時は None(GCP調査ツールなしの従来構成)。
    MCP サーバーへの接続はツール一覧取得・呼び出し時まで遅延されるため、
    生成自体はネットワークに出ない(ユニットテストでも安全)。
    """
    if not get_sandbox_project_id():
        return None
    from google.adk.tools.mcp_tool import (
        McpToolset,
        StreamableHTTPConnectionParams,
    )

    return McpToolset(
        connection_params=StreamableHTTPConnectionParams(
            url=CLOUD_ASSIST_MCP_URL,
            timeout=30.0,
            # agents-cli run の SSE クライアントは無通信 120 秒で切断されるため、
            # Cloud Assist の応答待ちもそれより短く打ち切る(調査依頼は要点を絞る)
            sse_read_timeout=110.0,
        ),
        tool_filter=CLOUD_ASSIST_TOOLS,
        header_provider=auth_header_provider,
    )
