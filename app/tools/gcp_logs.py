"""サンドボックスGCP環境のVMログ(BigQueryエクスポート)検索ツール。

GCE(Compute Engine)ホストのシステム(例: store-web-fleet)の障害調査用。
scripts/gce_incident.sh setup が作成する Cloud Logging → BigQuery エクスポート
(データセット sre_agents_demo_logs / テーブル syslog)を検索し、カーネル・
systemd・アプリの実ログから根本原因(例: Out of memory / oom-kill)を確認する。

- 対象プロジェクトは環境変数 SANDBOX_PROJECT_ID から取得する(gcp_assist と共通)。
  未設定ならこのツール自体がエージェントに組み込まれない。
- LLM から生SQLは受け取らない(キーワードと期間のみのパラメータ化クエリ)。
"""

from app.config import get_sandbox_project_id

SANDBOX_LOGS_DATASET = "sre_agents_demo_logs"
SANDBOX_LOGS_TABLE = "syslog"

_MAX_LIMIT = 50
_MAX_HOURS = 24 * 7


def query_incident_bigquery_logs(
    keyword: str, hours: int = 24, limit: int = 20
) -> dict:
    """サンドボックスGCP環境のVM実ログ(BigQueryエクスポート)をキーワード検索する。

    GCEホストのシステム(platform が Compute Engine のもの)の調査で使う。
    監視サマリー(get_server_logs)には出ないカーネル・systemd・アプリの実ログを
    確認し、根本原因の一次証拠を得るためのツール。

    検索キーワードの例:
    - "Out of memory" / "oom-kill": カーネルの OOM Killer の発動記録
    - "store-app": 店舗Webサーバー上のアプリログ(応答遅延・ヘルスチェック失敗など)
    - "memleak": メモリリークプロセスの systemd 記録
    - インスタンス名(例: "store-web-02"): 特定インスタンスの全ログ

    Args:
        keyword: 検索キーワード(ログ本文に対する部分一致。1語ずつ検索すること)
        hours: 何時間前までのログを対象にするか(既定24、最大168)
        limit: 取得する最大行数(既定20、最大50)

    Returns:
        新しい順のログ行(timestamp / instance_id / message)。
    """
    project_id = get_sandbox_project_id()
    if not project_id:
        return {
            "status": "error",
            "message": "SANDBOX_PROJECT_ID が未設定のため、GCP実ログは検索できません。",
        }
    keyword = (keyword or "").strip()
    if not keyword:
        return {"status": "error", "message": "検索キーワードを指定してください。"}
    hours = max(1, min(int(hours), _MAX_HOURS))
    limit = max(1, min(int(limit), _MAX_LIMIT))

    from google.cloud import bigquery

    table = f"{project_id}.{SANDBOX_LOGS_DATASET}.{SANDBOX_LOGS_TABLE}"
    query = f"""
        SELECT
          FORMAT_TIMESTAMP('%Y-%m-%d %H:%M:%S%Ez', timestamp, 'Asia/Tokyo') AS ts_jst,
          resource.labels.instance_id AS instance_id,
          COALESCE(textPayload, jsonPayload.message) AS message
        FROM `{table}`
        WHERE timestamp > TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL @hours HOUR)
          AND COALESCE(textPayload, jsonPayload.message) LIKE CONCAT('%', @keyword, '%')
        ORDER BY timestamp DESC
        LIMIT @limit
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("keyword", "STRING", keyword),
            bigquery.ScalarQueryParameter("hours", "INT64", hours),
            bigquery.ScalarQueryParameter("limit", "INT64", limit),
        ]
    )
    try:
        client = bigquery.Client(project=project_id)
        rows = list(client.query(query, job_config=job_config).result(timeout=60))
    except Exception as e:
        return {
            "status": "error",
            "message": f"BigQueryのログ検索に失敗しました: {e}",
            "hint": (
                "サンドボックスのログエクスポートが未作成の可能性があります"
                "(scripts/gce_incident.sh setup)。モックログと過去事例・手順書で"
                "判断し、その旨を報告してください。"
            ),
        }
    logs = [
        {
            "timestamp_jst": row["ts_jst"],
            "instance_id": row["instance_id"],
            "message": (row["message"] or "")[:500],
        }
        for row in rows
    ]
    return {
        "status": "success",
        "project_id": project_id,
        "keyword": keyword,
        "hours": hours,
        "row_count": len(logs),
        "logs": logs,
    }
