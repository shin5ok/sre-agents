"""受付・調査のサブエージェント定義と共通モデル設定。"""

from google.adk.models import Gemini
from google.genai import types

from app.config import MODEL_ID


def build_model() -> Gemini:
    """両エージェント共通のモデル設定を生成する。

    Vertex への呼び出しがまれに応答のないままハングすることがあるため、
    明示的なタイムアウトで失敗させてリトライに乗せる(agents-cli run の
    SSE クライアントは無通信 120 秒で切断されるので、それより短くする)。
    """
    return Gemini(
        model=MODEL_ID,
        client_kwargs={
            "http_options": types.HttpOptions(
                timeout=60_000,  # ミリ秒
                retry_options=types.HttpRetryOptions(attempts=3),
            )
        },
    )


def build_generate_content_config() -> types.GenerateContentConfig:
    """eval を安定させるための低 temperature・低 thinking の共通生成設定。"""
    return types.GenerateContentConfig(
        temperature=0.1,
        thinking_config=types.ThinkingConfig(thinking_level=types.ThinkingLevel.LOW),
    )
