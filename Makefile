# SRE障害対応エージェント 共通コマンド
# (SPEC の要件: よく使うコマンドはすべて make で実行できること)

PROJECT_ID ?= your-project-id
REGION ?= asia-northeast1
FIRESTORE_DB ?= sre-agents
PROMPT ?= 渋谷店(店舗ID:1102)です。売上管理システムで今朝9時頃からレジ2台の会計時に売上送信がタイムアウトします。報告者は佐藤です。以上の内容で対応をお願いします。

.PHONY: all install db-create seed run chat welcome playground test eval eval-compare lint deploy-setup deploy \
	gcp-incident-create gcp-incident-status gcp-incident-resolve gcp-incident-clean \
	gce-incident-create gce-incident-break gce-incident-status gce-incident-resolve gce-incident-clean \
	inventory-incident-create inventory-incident-status inventory-incident-resolve inventory-incident-clean

all: install seed test

## セットアップ ---------------------------------------------------------------

# 依存一式のインストール(agents-cli は skills と互換の 0.5 系に固定)
install:
	uv tool install "google-agents-cli~=0.5.0"
	agents-cli install
	uv sync --dev --extra eval --extra lint

# Firestore named database の作成(初回のみ。作成済みならエラーを無視)
db-create:
	gcloud firestore databases create --database=$(FIRESTORE_DB) --location=$(REGION) --type=firestore-native --project=$(PROJECT_ID) || true

# 過去対応ログのモックデータ投入(冪等)
seed:
	uv run python scripts/seed_firestore.py

## 実行 -----------------------------------------------------------------------

# ワンショット実行(PROMPT=... で上書き可能)
run:
	agents-cli run "$(PROMPT)"

# サーバーを起動したままワンショット実行(続きは agents-cli run --session-id <ID> "...")
chat:
	agents-cli run --start-server "$(PROMPT)"

# ウェルカム画面の確認(受付エージェントが「SRE エージェントへようこそ」を返す)
welcome:
	agents-cli run "こんにちは"

# adk web の対話UI
playground:
	agents-cli playground --port 18080

## テスト・評価 ---------------------------------------------------------------

# ユニットテスト(LLM 非依存)
test:
	uv run pytest tests/unit

# eval 実行(generate + grade。結果は artifacts/ 配下)
# SANDBOX_TOOLS_DISABLED=1: .env に SANDBOX_PROJECT_ID があっても GCPツールなしの
# 従来構成で評価する(agents-cli 0.5.1 の eval generate は McpToolset 非対応で落ちる)
eval:
	SANDBOX_TOOLS_DISABLED=1 agents-cli eval run

# eval 結果の比較(make eval-compare BASE=... CAND=...)
eval-compare:
	agents-cli eval compare $(BASE) $(CAND)

lint:
	agents-cli lint

## GCP障害シナリオ(store-app / Cloud Run + Gemini Cloud Assist) ---------------
# 対象プロジェクトは環境変数 SANDBOX_PROJECT_ID(未設定ならエラー)

# デモ準備の一括実行: API有効化 → 正常デプロイ → 障害注入(FAIL_MODE=db_timeout)
gcp-incident-create:
	scripts/gcp_incident.sh create

# サービスの環境変数と直近リクエストの結果を表示
gcp-incident-status:
	scripts/gcp_incident.sh status

# 障害を復旧(インフラ運用チーム役: FAIL_MODE=none に戻す)
gcp-incident-resolve:
	scripts/gcp_incident.sh resolve

# デモ環境の削除(Cloud Run サービスを削除)
gcp-incident-clean:
	scripts/gcp_incident.sh clean

## GCEインフラ障害シナリオ(store-web-01/02/03 / e2-micro + Ops Agent → BigQuery) -----
# 対象プロジェクトは環境変数 SANDBOX_PROJECT_ID(未設定ならエラー。store-appと共通)

# デモ準備の一括実行: API有効化+ロール付与 → 3台デプロイ → store-web-02で障害注入
gce-incident-create:
	scripts/gce_incident.sh create

# store-web-02 で障害を(再)注入する(resolve後にもう一度試す場合)
gce-incident-break:
	scripts/gce_incident.sh break

# 3台のWeb応答とstore-web-02の障害プロセス・メモリ状態を表示
gce-incident-status:
	scripts/gce_incident.sh status

# 障害を復旧(store-web-02を再起動。リーク解消、障害プロセスは自動再開しない)
gce-incident-resolve:
	scripts/gce_incident.sh resolve

# デモ環境の削除(3台のインスタンスを削除。firewall/BigQuery/sinkは残す)
gce-incident-clean:
	scripts/gce_incident.sh clean

## 在庫管理APサーバー(inventory-mgmt / restart_server の実リセット対象) --------
# 対象プロジェクトは環境変数 SANDBOX_PROJECT_ID(未設定ならエラー。他シナリオと共通)
# 障害の筋書きはモックログで完結するため break は無い(復旧対象のVMだけを用意する)

# inventory-mgmt インスタンス(e2-micro)を作成(存在すればスキップ)
inventory-incident-create:
	scripts/inventory_incident.sh create

# インスタンスの稼働状態と最終起動時刻(リセットで更新)を表示
inventory-incident-status:
	scripts/inventory_incident.sh status

# インスタンスを手動リセット(エージェントの restart_server と同等)
inventory-incident-resolve:
	scripts/inventory_incident.sh resolve

# インスタンスを削除
inventory-incident-clean:
	scripts/inventory_incident.sh clean

## デプロイ -------------------------------------------------------------------

# prototype 構成に Agent Runtime のデプロイ設定を追加(初回のみ)
deploy-setup:
	agents-cli scaffold enhance . --deployment-target agent_runtime --region $(REGION) --agent-guidance-filename CLAUDE.md --auto-approve

# デプロイ(人間の明示的な承認のもとでのみ実行すること)
deploy:
	agents-cli deploy
