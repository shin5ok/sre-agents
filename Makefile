# SRE障害対応エージェント 共通コマンド
# (SPEC の要件: よく使うコマンドはすべて make で実行できること)

PROJECT_ID ?= your-project-id
REGION ?= asia-northeast1
FIRESTORE_DB ?= sre-agents
PROMPT ?= 渋谷店(店舗ID:1102)です。売上管理システムで今朝9時頃からレジ2台の会計時に売上送信がタイムアウトします。報告者は佐藤です。以上の内容で対応をお願いします。

.PHONY: all install db-create seed run chat playground test eval eval-compare lint deploy-setup deploy

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

# adk web の対話UI
playground:
	agents-cli playground --port 18080

## テスト・評価 ---------------------------------------------------------------

# ユニットテスト(LLM 非依存)
test:
	uv run pytest tests/unit

# eval 実行(generate + grade。結果は artifacts/ 配下)
eval:
	agents-cli eval run

# eval 結果の比較(make eval-compare BASE=... CAND=...)
eval-compare:
	agents-cli eval compare $(BASE) $(CAND)

lint:
	agents-cli lint

## デプロイ -------------------------------------------------------------------

# prototype 構成に Agent Runtime のデプロイ設定を追加(初回のみ)
deploy-setup:
	agents-cli scaffold enhance . --deployment-target agent_runtime --region $(REGION) --agent-guidance-filename CLAUDE.md --auto-approve

# デプロイ(人間の明示的な承認のもとでのみ実行すること)
deploy:
	agents-cli deploy
