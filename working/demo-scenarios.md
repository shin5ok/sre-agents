# サンドボックスの障害デモシナリオ 一覧

**目的**: `SANDBOX_PROJECT_ID`(= `your-sandbox-project-id`)上に用意した「実GCP環境の障害」
デモシナリオのリファレンス。実装の経緯は
[`implementation-log.md`](./implementation-log.md)、操作方法の詳細は
[`README.md`](../README.md) にもあるが、本ファイルは**シナリオの中身(ground truth・
証跡の場所・現状の制約)を1か所にまとめたもの**。

前提: どちらのシナリオも `export SANDBOX_PROJECT_ID=your-sandbox-project-id`(`.env` 設定済み)
で有効化される。エージェント本体のプロジェクト(`GOOGLE_CLOUD_PROJECT`)とは別。

---

## シナリオ1: `store-app`(Cloud Run / アプリ層障害)

| 項目 | 内容 |
|---|---|
| 実体 | Cloud Run サービス `store-app`(Flask、`scripts/gcp_incident_app/`) |
| 障害の入れ方 | 環境変数 `FAIL_MODE=db_timeout` に切替(`scripts/gcp_incident.sh break`) |
| 表面症状 | 外形監視で HTTP 500 多発。`/healthz` は 200 のまま |
| 真の根本原因 | Cloud Run側の環境変数設定不備によるDB接続タイムアウト(モックログには出ない) |
| 証跡の場所 | Cloud Logging の Cloud Run サービスログ(`severity=ERROR`、
  `DB connection failed: could not connect to server db.internal:5432`) |
| 調査手段 | `investigation_agent` に組み込み済みの **Gemini Cloud Assist MCP**
  (`investigate_issue` / `ask_cloud_assist`、`app/tools/gcp_assist.py`) |
| 期待される対応 | エスカレーション(再起動では直らない設計) |
| 操作 | `make gcp-incident-create` / `-status` / `-resolve` / `-clean` |
| 必要な権限 | `roles/mcp.toolUser` + `roles/geminicloudassist.user`
  (`roles/owner` だけでは不足。`gcp_incident.sh setup` が付与) |

詳細: README.md「GCP障害シナリオ(Gemini Cloud Assist 連携)」、
implementation-log.md「2026-07-13: GCP障害シナリオ + Gemini Cloud Assist MCP 連携」。

---

## シナリオ2: `store-web-02`(GCE / インフラ層障害・メモリリーク)

**作成日**: 2026-07-13。`store-app` とは独立した、VMインフラ層の障害シナリオ。

### 何が用意されているか

| リソース | 内容 |
|---|---|
| VM(健全、比較対象) | `store-web-01` / `store-web-03`(e2-micro, asia-northeast1-a, nginx) |
| VM(障害注入対象) | `store-web-02`(同上 + メモリリーク+擬似障害ログの仕組み) |
| ファイアウォール | `allow-http-demo`(tcp:80、target-tag `http-server`) |
| BigQueryデータセット | `sre_agents_demo_logs`(asia-northeast1) |
| BigQueryテーブル | `syslog`(本体) / `ops_agent_fluent_bit` / `ops_agent_health` |
| Loggingシンク | `sre-agents-demo-sink`(`resource.type="gce_instance"` → 上記データセット) |
| default Compute SAへの権限 | `roles/logging.logWriter`, `roles/monitoring.metricWriter` |
| 現在のIPアドレス確認 | `make gce-incident-status` または
  `gcloud compute instances list --project=your-sandbox-project-id`(ephemeral IPのため固定値は記載しない) |

### 障害のground truth(=「正解」)

- **表面症状**(擬似ログ。`incident-logsim.service` が `logger -t store-app` で
  15〜45秒間隔で生成): 応答遅延(`response_time_ms` 上昇)→ DB接続プール枯渇 →
  ヘルスチェック失敗(503)→ メモリ使用率警告(74% → 88% → 95% rss)
- **真の根本原因**(本物のカーネル/systemdログ。擬似ではない): `memleak.py` が
  5MB/5秒でメモリを確保し続けるプロセスで、e2-micro(メモリ1GB)上ではおよそ
  8〜10分後に Linux カーネルの OOM Killer に強制終了される。
  `memleak.service` は `Restart=no` のため、キルされた後は `failed` のまま自然復旧しない
  (=「サーバー再起動が必要」という筋書き)。
- **期待される対応**: インスタンス再起動。`propose_recovery_plan` → 承認 →
  `restart_server` 相当。`scripts/gce_incident.sh resolve` が
  `gcloud compute instances reset` で実施する。

### 証跡の見つけ方(BigQuery)

```sql
-- 本物のOOMカーネル/systemdログ(ground truthの一次証拠)
SELECT timestamp, resource.labels.instance_id,
       COALESCE(textPayload, jsonPayload.message) AS message
FROM `your-sandbox-project-id.sre_agents_demo_logs.syslog`
WHERE COALESCE(textPayload, jsonPayload.message) LIKE '%Out of memory%'
   OR COALESCE(textPayload, jsonPayload.message) LIKE '%oom-kill%'
ORDER BY timestamp DESC;

-- 擬似障害ログ(store-appタグ、表面症状)
SELECT timestamp, COALESCE(textPayload, jsonPayload.message) AS message
FROM `your-sandbox-project-id.sre_agents_demo_logs.syslog`
WHERE COALESCE(textPayload, jsonPayload.message) LIKE '%store-app%'
ORDER BY timestamp DESC;
```

Cloud Logging(BigQueryへの反映を待たずに直接見る場合):

```bash
gcloud logging read 'resource.type="gce_instance" AND logName:"syslog"' \
  --project=your-sandbox-project-id --freshness=30m \
  --format="table(timestamp,resource.labels.instance_id,textPayload,jsonPayload.message)"
```

### 操作コマンド

```bash
export SANDBOX_PROJECT_ID=your-sandbox-project-id   # .env に設定済み

make gce-incident-create    # setup(API有効化+IAM+BigQuery+sink) → 3台デプロイ → 障害注入
make gce-incident-status    # 3台のWeb応答 + store-web-02の障害プロセス/メモリ状態
make gce-incident-resolve   # store-web-02を再起動して復旧(リーク解消)
make gce-incident-break     # resolve後にもう一度障害を試す場合
make gce-incident-clean     # 3台のインスタンスを削除(ファイアウォール/BigQuery/sinkは残す)
```

### 現状の制約・未実装(次にやるなら)

1. **`investigation_agent` からはまだ調査できない**。`app/tools/diagnostic_tools.py` は
   `mock_world.py` 専用の決定論的モックしか読まない。`app/tools/gcp_assist.py`
   (Gemini Cloud Assist MCP)は同じ `SANDBOX_PROJECT_ID` を向いているため、
   理論上 Compute Engine 層の情報(ログ・メトリクス)を拾える可能性があるが
   **このシナリオでは未検証**(`store-app` では Cloud Run 向けの動作を確認済み)。
2. エージェントから調査させたい場合の候補:
   - (a) Cloud Assist の `investigate_issue` が Compute Engine / BigQuery エクスポート
     テーブルをどこまで見られるか検証する
   - (b) `bq query` を叩く専用ツール(例: `query_incident_bigquery_logs`)を新設し、
     `mock_world.py` に5つ目の system_id(例: `store-web-fleet`)を登録して
     `get_system_status` / `get_server_logs` 経由で誘導する
3. **コスト**: `asia-northeast1` は Always Free 枠の対象外リージョン。e2-micro x3 は
   稼働中わずかに課金される。使わない時は `make gce-incident-clean`。

### 設計上の注意点(ハマりどころ)

- GCEの `startup-script` は**再起動のたびに実行される**。そのため
  `scripts/gce_incident_startup/incident.sh` は memleak.service /
  incident-logsim.service を `enable` / `start` しない(設置のみ)。障害注入は
  `break` コマンドが明示的に `systemctl start` する。ここを誤ると `resolve`
  (インスタンスreset)のたびに障害が自動再発してしまう。
- 擬似ログの文字列に `%%` のような二重エスケープを書かないこと
  (`printf` ではなく `logger` にそのまま渡すため、`%%` は文字通り出力される)。
- BigQuery データセットのIAM付与は `bq add-iam-policy-binding` が
  allowlist 機能で使えない環境があるため、`scripts/gce_incident.sh` は
  `bq show --format=prettyjson` → JSON編集 → `bq update --source` で
  冪等にACLを付与している(`grant_bq_dataset_access` 関数)。
