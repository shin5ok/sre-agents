"""疑似システムレジストリ(モックワールド)。

実環境(GCP)の代わりに決定論的な稼働状態とサーバーログを返すための固定データ。
eval の安定性のため、乱数や現在時刻に依存する値を一切含めない。
シナリオ整合: 各システムのログ異常は data/runbooks/ の既知事象および
scripts/seed_firestore.py の過去対応ログと同じ結論(再起動 or エスカレーション)を指す。

store-app のみ「GCP実環境と接続する例」: ここにあるのは外形監視の抜粋だけで、
クラウド側の実体(Cloud Run サービス)は scripts/gcp_incident.sh で
SANDBOX_PROJECT_ID のプロジェクトに作成し、Gemini Cloud Assist(MCP)で調査する。
"""

SYSTEMS: dict[str, dict] = {
    "sales-mgmt": {
        "display_name": "売上管理システム",
        "description": "各店舗のレジから売上データを受信・集計するシステム",
        "status": "degraded",
        "status_detail": "売上送信APIの応答遅延とエラー率上昇(エラー率42%、p95レイテンシ31秒)",
        "restartable": True,
        "healthy_detail": "全API正常応答(エラー率0.1%、p95レイテンシ180ms)",
        "log_excerpt": [
            "2026-07-09 08:52:11.031 INFO  [http-nio-8080-exec-4] c.e.sales.api.SalesController - POST /api/v1/sales store=0417 accepted",
            "2026-07-09 08:55:02.113 INFO  [http-nio-8080-exec-12] c.e.sales.api.SalesController - POST /api/v1/sales store=1102 received",
            "2026-07-09 08:55:32.118 ERROR [http-nio-8080-exec-12] c.e.sales.api.SalesController - sales upload failed",
            "java.sql.SQLTransientConnectionException: HikariPool-1 - Connection is not available, request timed out after 30000ms.",
            "2026-07-09 08:55:33.201 WARN  [HikariPool-1 housekeeper] com.zaxxer.hikari.pool.HikariPool - HikariPool-1 - Thread starvation or clock leap detected (housekeeper delta=52s788ms).",
            "2026-07-09 08:56:40.552 WARN  [http-nio-8080-exec-3] c.e.sales.db.ConnectionPoolMonitor - pool usage: active=50/50 idle=0 waiting=127",
            "2026-07-09 08:57:05.771 ERROR [http-nio-8080-exec-9] c.e.sales.api.SalesController - sales upload failed",
            "java.sql.SQLTransientConnectionException: HikariPool-1 - Connection is not available, request timed out after 30000ms.",
            "2026-07-09 08:59:14.905 INFO  [http-nio-8080-exec-1] c.e.sales.api.HealthController - GET /healthz 200 (db check skipped)",
            "2026-07-09 09:01:23.442 WARN  [http-nio-8080-exec-7] c.e.sales.db.ConnectionPoolMonitor - pool usage: active=50/50 idle=0 waiting=214",
            "2026-07-09 09:03:48.310 ERROR [http-nio-8080-exec-2] c.e.sales.api.SalesController - sales upload failed",
            "java.sql.SQLTransientConnectionException: HikariPool-1 - Connection is not available, request timed out after 30000ms.",
        ],
    },
    "inventory-mgmt": {
        "display_name": "在庫管理システム",
        "description": "店舗の在庫照会・棚卸・自動補充計算を行うシステム",
        "status": "degraded",
        "status_detail": "在庫照会APIの極端な遅延(30秒前後)と断続的なエラー",
        "restartable": True,
        "healthy_detail": "全API正常応答(p95レイテンシ220ms)、夜間バッチ正常",
        "log_excerpt": [
            "2026-07-09 07:40:11.031 INFO  [scheduler-1] c.e.inv.batch.StockSyncJob - nightly stock sync started (stores=1200)",
            "2026-07-09 07:42:02.874 WARN  [GC] Full GC (Ergonomics) 4093M->4088M(4096M), 3.914 secs",
            "2026-07-09 07:42:56.120 WARN  [GC] Full GC (Ergonomics) 4094M->4091M(4096M), 4.102 secs",
            "2026-07-09 07:43:41.663 WARN  [GC] Full GC (Ergonomics) 4095M->4093M(4096M), 4.771 secs",
            "2026-07-09 07:44:18.409 WARN  [http-nio-8080-exec-5] c.e.inv.api.StockController - GET /api/v1/stock latency=28744ms store=0308",
            "2026-07-09 07:45:10.412 ERROR [http-nio-8080-exec-7] c.e.inv.api.StockController - request aborted",
            "java.lang.OutOfMemoryError: Java heap space",
            "    at com.example.inv.cache.StockCacheLoader.loadAll(StockCacheLoader.java:118)",
            "2026-07-09 07:45:12.001 ERROR [scheduler-1] c.e.inv.batch.StockSyncJob - job aborted after 213/1200 stores",
            "java.lang.OutOfMemoryError: Java heap space",
            "2026-07-09 07:46:30.514 WARN  [http-nio-8080-exec-2] c.e.inv.api.StockController - GET /api/v1/stock latency=31022ms store=1102",
        ],
    },
    "store-app": {
        "display_name": "店舗アプリAPI",
        "description": (
            "店舗スタッフ向けアプリ・モバイルオーダーのバックエンドAPI"
            "(GCPサンドボックスプロジェクト上の Cloud Run サービス store-app)"
        ),
        "platform": "Cloud Run(GCPサンドボックスプロジェクト / asia-northeast1)",
        "status": "degraded",
        "status_detail": (
            "外形監視で HTTP 500 を多数検知(エラー率90%超、p95レイテンシ2.1秒)。"
            "クラウド側のアプリケーションログは本ツールからは参照不可"
            "(Gemini Cloud Assist で GCP 側を調査すること)。"
        ),
        "restartable": False,
        "restart_block_reason": (
            "Cloud Run のフルマネージドサービスのため、"
            "従来型のサーバー再起動の対象外です。"
        ),
        "healthy_detail": "外形監視 全チェック 200 OK(p95レイテンシ310ms)",
        "log_excerpt": [
            "2026-07-13 09:12:04.101 INFO  [synthetic-monitor] GET https://store-app-*.run.app/ -> 500 (2043ms) probe=tokyo-1",
            "2026-07-13 09:12:34.144 INFO  [synthetic-monitor] GET https://store-app-*.run.app/ -> 500 (1988ms) probe=tokyo-2",
            "2026-07-13 09:13:04.190 ERROR [edge-gateway] upstream error: store-app (Cloud Run) returned 5xx for 27/30 requests in last 60s",
            "2026-07-13 09:13:04.191 WARN  [edge-gateway] Cloud Run 側のアプリケーションログは本ゲートウェイからは参照できません。GCP側の調査には Gemini Cloud Assist を使用してください。",
            "2026-07-13 09:13:34.226 INFO  [synthetic-monitor] GET https://store-app-*.run.app/healthz -> 200 (95ms) probe=tokyo-1",
            "2026-07-13 09:14:04.233 INFO  [synthetic-monitor] GET https://store-app-*.run.app/ -> 500 (2101ms) probe=tokyo-1",
            "2026-07-13 09:14:34.301 ERROR [edge-gateway] upstream error: store-app (Cloud Run) returned 5xx for 28/30 requests in last 60s",
            "2026-07-13 09:15:04.377 INFO  [synthetic-monitor] GET https://store-app-*.run.app/ -> 500 (1967ms) probe=tokyo-2",
        ],
    },
    "order-mgmt": {
        "display_name": "発注管理システム",
        "description": "店舗からの商品発注を受け付け、仕入先へ連携するシステム(マネージドDB構成)",
        "status": "down",
        "status_detail": "発注登録APIが全件失敗(データベースへの書き込みが全て失敗)",
        "restartable": False,
        "restart_block_reason": "マネージドDB構成のため、アプリケーション側からの再起動権限がありません。",
        "healthy_detail": "発注登録・仕入先連携とも正常",
        "log_excerpt": [
            "2026-07-09 07:55:44.120 INFO  [order-api] c.e.order.api.OrderController - POST /api/v1/orders store=2201 items=48",
            '2026-07-09 07:55:45.883 ERROR [postgres] FATAL:  could not extend file "base/16384/2601": No space left on device',
            "2026-07-09 07:55:45.884 ERROR [postgres] HINT:  Check free disk space.",
            "2026-07-09 07:55:46.001 ERROR [order-api] c.e.order.api.OrderController - order create failed: SQLSTATE 53100 disk_full",
            '2026-07-09 07:58:02.310 ERROR [postgres] FATAL:  could not extend file "base/16384/2601": No space left on device',
            "2026-07-09 07:58:02.377 ERROR [order-api] c.e.order.api.OrderController - order create failed: SQLSTATE 53100 disk_full",
            "2026-07-09 08:00:15.912 WARN  [order-api] c.e.order.db.StorageMonitor - db storage usage 100% (500GB/500GB) - writes are failing",
            "2026-07-09 08:01:00.020 INFO  [order-api] c.e.order.api.HealthController - GET /healthz 500 (db write check failed)",
            "2026-07-09 08:03:27.454 ERROR [order-api] c.e.order.api.OrderController - order create failed: SQLSTATE 53100 disk_full",
        ],
    },
}
