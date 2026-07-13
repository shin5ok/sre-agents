"""GCP障害シナリオ用のサンプルAPI(store-app / Cloud Run にデプロイされる)。

環境変数 FAIL_MODE で挙動を切り替える:
- none(既定): 全エンドポイント正常
- db_timeout: "/" がDB接続タイムアウト(擬似)で 500 を返し、ERROR ログを出す
  (/healthz はプロセス死活のみ見るため 200 のまま — mock_world の外形監視と整合)

デプロイ・障害注入・復旧は scripts/gcp_incident.sh から行う。
"""

import json
import os
import sys
import time

from flask import Flask

app = Flask(__name__)


def _log(severity: str, message: str) -> None:
    # Cloud Run は stdout の JSON を構造化ログとして取り込む(severity が反映される)
    print(
        json.dumps({"severity": severity, "message": message}),
        file=sys.stdout,
        flush=True,
    )


@app.get("/")
def index():
    if os.environ.get("FAIL_MODE", "none") == "db_timeout":
        time.sleep(1.5)
        _log(
            "ERROR",
            "DB connection failed: could not connect to server db.internal:5432 "
            "(connection timed out after 1500ms) service=store-app",
        )
        return {
            "error": "internal server error",
            "detail": "db connection timeout",
        }, 500
    return {"status": "ok", "service": "store-app"}, 200


@app.get("/healthz")
def healthz():
    return {"status": "ok"}, 200
