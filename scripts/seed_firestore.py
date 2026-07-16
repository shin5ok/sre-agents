"""incident_logs コレクションに過去の対応ログ(モックデータ)を投入する。

固定のドキュメントIDで set() するため、何度実行しても冪等。
シナリオ整合: app/mock_world.py のログ異常・data/runbooks/ の既知事象と同じ結論を指す。
"""

from app.firestore_client import INCIDENTS_COLLECTION, get_client

SEED_INCIDENTS: list[dict] = [
    {
        "incident_id": "INC-2025-0001",
        "case_id": None,
        "system_id": "sales-mgmt",
        "title": "売上送信タイムアウト多発(DB接続プール枯渇)",
        "symptom": "全国の店舗レジで売上送信がタイムアウト。エラー率40%超。",
        "root_cause": "APサーバーのDB接続プール枯渇。ログに HikariPool-1 の接続取得タイムアウトが多発、pool usage active=50/50。コネクションリークが原因。",
        "action_taken": "APサーバーを再起動し接続プールを解放。エラー率・レイテンシの正常化を確認。恒久対応としてコネクションリーク改修を開発チームへ起票。",
        "outcome": "resolved",
        "escalated": False,
        "tags": ["売上管理", "タイムアウト", "接続プール", "再起動", "HikariPool"],
        "occurred_at": "2025-11-18",
        "recorded_at": "2025-11-18T14:20:00+09:00",
        "source": "seed",
    },
    {
        "incident_id": "INC-2025-0002",
        "case_id": None,
        "system_id": "inventory-mgmt",
        "title": "在庫照会が極端に遅い(OutOfMemoryError)",
        "symptom": "店舗の在庫照会画面の表示に30秒前後かかり、断続的にエラー。",
        "root_cause": "APサーバーのヒープ枯渇。java.lang.OutOfMemoryError: Java heap space と Full GC の連発をログで確認。キャッシュローダーのメモリリーク。",
        "action_taken": "APサーバーを再起動して復旧。恒久対応としてヒープサイズ増強とキャッシュ改修を計画。",
        "outcome": "resolved",
        "escalated": False,
        "tags": ["在庫管理", "OOM", "OutOfMemoryError", "メモリ", "再起動", "遅延"],
        "occurred_at": "2025-12-03",
        "recorded_at": "2025-12-03T11:05:00+09:00",
        "source": "seed",
    },
    {
        "incident_id": "INC-2025-0003",
        "case_id": None,
        "system_id": "order-mgmt",
        "title": "発注登録が全店で失敗(DBストレージ枯渇)",
        "symptom": "全店舗で発注登録がエラー。発注業務が停止。",
        "root_cause": "データベースのストレージ枯渇。ログに No space left on device / SQLSTATE 53100 disk_full。ストレージ使用率100%。",
        "action_taken": "マネージドDB構成のため再起動では回復しないと判断し、インフラ運用チームへエスカレーション。ストレージ容量拡張により復旧。",
        "outcome": "escalated",
        "escalated": True,
        "tags": ["発注管理", "ディスク", "ストレージ", "エスカレーション", "disk_full"],
        "occurred_at": "2026-01-22",
        "recorded_at": "2026-01-22T09:40:00+09:00",
        "source": "seed",
    },
    {
        "incident_id": "INC-2026-0004",
        "case_id": None,
        "system_id": "sales-mgmt",
        "title": "TLS証明書期限切れによるレジ接続エラー",
        "symptom": "一部店舗のレジで売上管理システムへの接続時に証明書エラー。",
        "root_cause": "ロードバランサのTLS証明書の期限切れ。",
        "action_taken": "インフラ運用チームへエスカレーションし、証明書を更新して復旧。証明書期限の監視アラートを追加。",
        "outcome": "escalated",
        "escalated": True,
        "tags": ["売上管理", "証明書", "TLS", "接続エラー", "エスカレーション"],
        "occurred_at": "2026-02-14",
        "recorded_at": "2026-02-14T16:30:00+09:00",
        "source": "seed",
    },
    {
        "incident_id": "INC-2026-0005",
        "case_id": None,
        "system_id": "inventory-mgmt",
        "title": "夜間在庫同期バッチの遅延",
        "symptom": "夜間の在庫同期バッチが朝までに完了せず、開店時の在庫表示が前日のまま。",
        "root_cause": "棚卸期間の入力データ急増によるバッチ処理遅延。システム異常ではない。",
        "action_taken": "バッチを再実行し日中に同期完了。棚卸期間中の並列度を引き上げる設定変更を実施。",
        "outcome": "resolved",
        "escalated": False,
        "tags": ["在庫管理", "バッチ", "遅延", "夜間処理"],
        "occurred_at": "2026-03-30",
        "recorded_at": "2026-03-30T13:10:00+09:00",
        "source": "seed",
    },
    {
        "incident_id": "INC-2026-0006",
        "case_id": None,
        "system_id": "store-app",
        "title": "店舗アプリAPIの5xx多発(Cloud Run 環境変数の設定不備)",
        "symptom": "店舗スタッフ向けアプリの操作が全店でエラー。外形監視で HTTP 500 多発(/healthz は 200)。",
        "root_cause": (
            "Cloud Run サービス store-app の環境変数の設定不備。アプリがDB接続"
            "タイムアウト(DB connection failed: could not connect to server "
            "db.internal:5432)を起こし全リクエストが500を返していた。"
            "Gemini Cloud Assist の調査でエラーログと直前のリビジョン更新"
            "(環境変数変更)を特定。"
        ),
        "action_taken": (
            "Cloud Run はアプリ側からの再起動対象外のためインフラ運用チームへ"
            "エスカレーション。環境変数を修正した新リビジョンのデプロイで復旧。"
            "エスカレーション時に Cloud Assist で特定したログとリビジョン情報を添付。"
        ),
        "outcome": "escalated",
        "escalated": True,
        "tags": [
            "店舗アプリ",
            "Cloud Run",
            "GCP",
            "5xx",
            "環境変数",
            "エスカレーション",
            "Cloud Assist",
        ],
        "occurred_at": "2026-05-27",
        "recorded_at": "2026-05-27T18:45:00+09:00",
        "source": "seed",
    },
    {
        "incident_id": "INC-2026-0007",
        "case_id": None,
        "system_id": "store-web-fleet",
        "title": "店舗Webサーバーのヘルスチェック失敗(メモリリーク→OOM)",
        "symptom": (
            "store-web-02 のみ応答遅延とヘルスチェック503。メモリ使用率が"
            "74%→88%→95%と継続上昇。他の2台は正常。"
        ),
        "root_cause": (
            "常駐プロセスのメモリリークによるメモリ枯渇。BigQuery のログエクスポートで"
            "カーネルの「Out of memory: Killed process (python3)」と"
            "「memleak.service: Failed with result 'oom-kill'」を確認。"
            "OOM Killer に強制終了されたプロセスは自動復旧せずサービス劣化が継続。"
        ),
        "action_taken": (
            "手順書に基づき、承認を得て該当インスタンス store-web-02 を再起動"
            "(Compute Engine リセット)し復旧。恒久対応としてリークプロセスの"
            "修正を開発チームへ起票。"
        ),
        "outcome": "resolved",
        "escalated": False,
        "tags": [
            "店舗Web",
            "GCE",
            "Compute Engine",
            "OOM",
            "メモリリーク",
            "再起動",
            "BigQuery",
            "oom-kill",
        ],
        "occurred_at": "2026-06-18",
        "recorded_at": "2026-06-18T15:30:00+09:00",
        "source": "seed",
    },
]


def main() -> None:
    client = get_client()
    for incident in SEED_INCIDENTS:
        client.collection(INCIDENTS_COLLECTION).document(incident["incident_id"]).set(
            incident
        )
        print(f"seeded: {incident['incident_id']}  {incident['title']}")
    print(f"done: {len(SEED_INCIDENTS)} incidents")


if __name__ == "__main__":
    main()
