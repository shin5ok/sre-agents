# sre-agents

本部システム(GCP)の障害対応を自動化・半自動化する AI エージェント。
**受付エージェント**が店舗からの障害報告を聞き取ってサポートケースを Firestore に起票し、
**調査エージェント**がログ調査・過去対応ログと運用手順書の検索・復旧(モック)または
エスカレーション・対応ログの記録までを行う(仕様は `SPEC.md` 参照)。

Agent generated with `agents-cli` version `0.5.1`

## 使い方(make)

```bash
make install     # 依存一式(agents-cli / uv sync)
make db-create   # Firestore named DB "sre-agents" 作成(初回のみ)
make seed        # 過去対応ログのモックデータ投入(冪等)
make playground  # adk web の対話UIで動かす
make run         # ワンショット実行(PROMPT="..." で上書き可)
make test        # ユニットテスト
make eval        # エージェント評価(3ケース)
make lint        # コード品質チェック
make deploy-setup && make deploy   # Agent Runtime へのデプロイ(要・人間の承認)
```

デモ用の障害シナリオ(モック): 売上管理システム=DB接続プール枯渇(再起動で復旧)、
在庫管理システム=OutOfMemoryError(再起動で復旧)、発注管理システム=DBストレージ枯渇
(再起動不可→エスカレーション)。

## Project Structure

```
sre-agents/
├── app/         # Core agent code
│   ├── agent.py               # Main agent logic
│   └── app_utils/             # App utilities and helpers
│   ├── sub_agents/            # 受付・調査エージェント定義
│   └── tools/                 # ケース起票・診断・ナレッジ検索・復旧ツール
├── data/runbooks/             # 運用手順書(モック、日本語)
├── scripts/seed_firestore.py  # 過去対応ログのシードデータ投入
├── tests/                     # Unit, integration, and eval tests
├── CLAUDE.md                  # AI-assisted development guide
└── pyproject.toml             # Project dependencies
```

## Requirements

Before you begin, ensure you have:
- **uv**: Python package manager (used for all dependency management in this project) - [Install](https://docs.astral.sh/uv/getting-started/installation/) ([add packages](https://docs.astral.sh/uv/concepts/dependencies/) with `uv add <package>`)
- **agents-cli**: Agents CLI - Install with `uv tool install google-agents-cli`
- **Google Cloud SDK**: For GCP services - [Install](https://cloud.google.com/sdk/docs/install)


## Quick Start

Install `agents-cli` and its skills if not already installed:

```bash
uvx google-agents-cli setup
```

Install required packages:

```bash
agents-cli install
```

Test the agent with a local web server:

```bash
agents-cli playground
```

You can also use features from the [ADK](https://adk.dev/) CLI with `uv run adk`.

## Commands

| Command              | Description                                                                                 |
| -------------------- | ------------------------------------------------------------------------------------------- |
| `agents-cli install` | Install dependencies using uv                                                         |
| `agents-cli playground` | Launch local development environment                                                  |
| `agents-cli lint`    | Run code quality checks                                                               |
| `agents-cli eval`    | Evaluate agent behavior (generate, grade, analyze, and more — see `agents-cli eval --help`) |
| `uv run pytest tests/unit tests/integration` | Run unit and integration tests                                                        |

## 🛠️ Project Management

| Command | What It Does |
|---------|--------------|
| `agents-cli scaffold enhance` | Add CI/CD pipelines and Terraform infrastructure |
| `agents-cli infra cicd` | One-command setup of entire CI/CD pipeline + infrastructure |
| `agents-cli scaffold upgrade` | Auto-upgrade to latest version while preserving customizations |

---

## Development

Edit your agent logic in `app/agent.py` and test with `agents-cli playground` - it auto-reloads on save.

## Deployment

```bash
gcloud config set project <your-project-id>
agents-cli deploy
```

To add CI/CD and Terraform, run `agents-cli scaffold enhance`.
To set up your production infrastructure, run `agents-cli infra cicd`.

## Observability

Built-in telemetry exports to Cloud Trace, BigQuery, and Cloud Logging.
