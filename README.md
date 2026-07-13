# sre-agents

本部システム(GCP)の障害対応を自動化・半自動化する AI エージェント。
仕様は [`SPEC.md`](./SPEC.md)、開発ガイド・落とし穴は [`CLAUDE.md`](./CLAUDE.md)、
実装経緯は [`working/implementation-log.md`](./working/implementation-log.md) を参照。

Google ADK 2.x / Python / Vertex AI(Gemini)/ Firestore で構築。
`agents-cli` scaffold(v0.5.1)で生成。

---

## 概要

現状は「店舗の障害申請 → 受付が起票 → 運用エンジニアがログ調査・復旧」というチェーンで
時間がかかる。ここに 2 つの AI エージェントを入れる:

- **受付エージェント(reception_agent)** — 店舗からの障害報告を日本語で聞き取り、
  必須項目(店舗ID・店舗名 / 対象システム / 症状 / 発生日時 / 影響範囲 / 報告者名)が
  揃ったら Firestore にサポートケースを起票し、調査エージェントへ引き継ぐ
- **調査エージェント(investigation_agent)** — サーバー状態・ログ確認、
  過去対応ログ検索 + 運用手順書検索を経て**復旧計画を提示 → 承認待ち(HITL)**、
  承認後に復旧(モック)またはエスカレーションを実行、対応ログを Firestore に記録

## アーキテクチャ

```
User(店舗) ── report ──► reception_agent (root)
                             │
                             ▼ 必須6項目が揃ったら
                       create_support_case ──► Firestore(support_cases)
                             │
                             ▼ transfer_to_agent
                       investigation_agent (sub-agent)
                             │
                             ├─ update_case_status("investigating")
                             ├─ get_system_status + get_server_logs
                             ├─ search_past_incidents + search_runbooks
                             │
                             ▼ ★ HITL(復旧実施前の承認ステップ)
                       propose_recovery_plan
                          {原因 / 作業内容 / 作業種別 / 作業中の影響}
                             │
                             ▼ ── 担当者の承認 ──►(未承認なら停止)
                             │
                             ├─ restart_server(モック) or escalate_to_engineer(モック)
                             ├─ record_resolution_log ──► Firestore(incident_logs)
                             └─ update_case_status("resolved" or "escalated")
```

**HITL の要点**: `restart_server` / `escalate_to_engineer` は
`propose_recovery_plan` で承認情報が state にセットされていないと
ツール側でエラー返却(**指示層 + ツール層の二重ゲート**)。
初回依頼文に「そのまま実施してください」等の**明示的な事前承認**があれば
propose 直後に連続実行する運用も可能。

## デモシナリオ(モック)

| system_id | 表示名 | ログ異常 | 期待される対応 |
|---|---|---|---|
| `sales-mgmt` | 売上管理システム | DB接続プール枯渇(HikariPool timeout) | 再起動で復旧 |
| `inventory-mgmt` | 在庫管理システム | OutOfMemoryError + Full GC 連発 | 再起動で復旧 |
| `order-mgmt` | 発注管理システム | `No space left on device`(DBストレージ枯渇) | 再起動不可 → **エスカレーション** |

各システムのログ 8〜12 行、過去対応ログ 5 件、運用手順書 5 本を用意していて、
LLM は「ログの異常 → 過去事例 → 手順書」の 3 点が同じ結論を指すのを確認しながら判断する。

---

## セットアップ

必要なツール:
- **uv**: [インストール手順](https://docs.astral.sh/uv/getting-started/installation/)
- **Google Cloud SDK**: `gcloud` にログインし ADC を有効化(`gcloud auth application-default login`)

```bash
make install     # agents-cli~=0.5.0 を tool install + uv sync(dev/eval/lint 全部)
make db-create   # Firestore named DB "sre-agents"(asia-northeast1)を作成 [初回のみ]
make seed        # 過去対応ログ 5 件を投入(冪等)
```

GCP プロジェクトは環境変数 `GOOGLE_CLOUD_PROJECT` で指定。未設定なら ADC から自動解決。
Firestore の named database 名は `FIRESTORE_DATABASE`(既定: `sre-agents`)。
参考は [`.env.example`](./.env.example)。

---

## 使い方(make)

| target | 内容 |
|---|---|
| `make install` | 依存一式のインストール(agents-cli ~=0.5.0 + uv sync) |
| `make db-create` | Firestore named DB `sre-agents` を asia-northeast1 に作成(初回のみ) |
| `make seed` | 過去対応ログ 5 件を投入(冪等) |
| `make run` | ワンショット実行(`PROMPT="..."` で上書き可能) |
| `make chat` | サーバー起動+ワンショット。続きは `agents-cli run --session-id <ID> "..."` |
| `make playground` | `adk web` の対話 UI(ブラウザで動作確認) |
| `make test` | ユニットテスト(LLM 非依存、25 件) |
| `make eval` | `agents-cli eval run`(generate + grade、4 ケース) |
| `make eval-compare BASE=... CAND=...` | 2 つの grade 結果の回帰比較 |
| `make lint` | ruff / format / codespell / ty のチェック |
| `make deploy-setup` | prototype に Agent Runtime 設定を追加(初回のみ) |
| `make deploy` | Agent Runtime へのデプロイ(**要・人間の明示的承認**) |

---

## 動かしてみる(3つの経路)

### 1. 情報不足の報告 → 追加質問

```bash
make run PROMPT="渋谷店ですが、売上管理システムの調子が悪くて会計に時間がかかっています。なんとかしてもらえますか。"
```

→ 受付が不足項目(店舗ID/発生日時/影響範囲/報告者名 等)を番号付きで質問して終わる。
   ケース起票はしない。

### 2. 完全報告(承認待ち経路)

```bash
make run PROMPT="渋谷店(店舗ID:1102)の佐藤です。売上管理システムで今朝9時ごろからレジの会計時に「売上データの送信がタイムアウトしました」というエラーが出ます。店内のレジ2台すべてで発生していて、他の業務は正常です。原因を調査して対応方針を提案してください。復旧作業の実施は私の承認を得てから進めてください。"
```

→ 受付が起票 → 調査が調査 → **propose_recovery_plan で計画提示 → 停止して承認待ち**。
   `restart_server` は呼ばれない。

### 3. 完全報告 + 事前承認(フル自走)

```bash
make run PROMPT="渋谷店(店舗ID:1102)の佐藤です。売上管理システムで今朝9時ごろからレジの会計時に「売上データの送信がタイムアウトしました」というエラーが出ます。店内のレジ2台すべてで発生していて、他の業務は正常です。原因を特定し対応計画を提示した上で、そのまま復旧作業まで実施してください。追加の承認は不要です(事前承認済み)。"
```

→ 起票 → 調査 → propose → **承認済みとみなして** restart_server → 稼働確認 →
   record_resolution_log → status を resolved に更新まで自走(所要 30〜40 秒)。

対話 UI から触りたい場合は `make playground`(ブラウザで開く)。

---

## プロジェクト構成

```
sre-agents/
├── SPEC.md                          # スペック(日本語、権威)
├── CLAUDE.md                        # AI開発ガイド + 落とし穴メモ
├── README.md                        # このファイル
├── Makefile                         # よく使うコマンド一式
├── pyproject.toml / uv.lock         # 依存関係(google-adk 2.x, google-cloud-firestore)
├── agents-cli-manifest.yaml         # agents-cli 管理(手動編集禁止)
├── .env.example                     # 環境変数のテンプレート
├── app/
│   ├── agent.py                     # App(name="app") + root_agent
│   ├── config.py                    # MODEL_ID, FIRESTORE_DATABASE, RUNBOOK_DIR
│   ├── firestore_client.py          # named DB "sre-agents" の遅延シングルトン
│   ├── mock_world.py                # 疑似システム3種の決定論的レジストリ
│   ├── sub_agents/
│   │   ├── __init__.py              # build_model / build_generate_content_config
│   │   ├── reception.py             # 受付エージェント(日本語 instruction)
│   │   └── investigation.py         # 調査エージェント(HITL 手順を厳守)
│   └── tools/
│       ├── case_tools.py            # create/get/update support_case
│       ├── diagnostic_tools.py      # list_managed_systems / get_system_status / get_server_logs
│       ├── knowledge_tools.py       # search_past_incidents / search_runbooks / record_resolution_log
│       └── remediation_tools.py     # ★ propose_recovery_plan / restart_server / escalate_to_engineer
├── data/runbooks/                   # 運用手順書 5 本(日本語)
├── scripts/seed_firestore.py        # 過去対応ログ 5 件のシード(冪等)
├── tests/
│   ├── unit/                        # ユニットテスト 25 件(LLM 非依存)
│   │   ├── test_mock_world.py       # レジストリ整合性
│   │   ├── test_search_logic.py     # キーワード検索の純関数
│   │   ├── test_agent_wiring.py     # エージェント配線
│   │   └── test_hitl_gating.py      # HITL ゲート(propose なしなら実施拒否)
│   ├── integration/                 # (scaffold の雛形。今は未使用)
│   └── eval/
│       ├── eval_config.yaml         # カスタムメトリクス 3 本(LLM判定 2 + 決定論 1)
│       └── datasets/basic-dataset.json # 4 ケース
└── working/implementation-log.md    # 実装作業ログ
```

---

## 評価(eval)

4 ケース、全て満点(2026-07-13 時点):

| ケース | task_success | flow_compliance | trajectory_checks |
|---|---|---|---|
| `intake_interview_incomplete`(情報不足→追加質問) | 5.0 | 5.0 | 1.0 |
| `propose_awaits_approval`(完全報告 → propose で停止) | 5.0 | 5.0 | 1.0 |
| `pre_approved_resolution`(事前承認 + sales-mgmt → 再起動で復旧) | 5.0 | 5.0 | 1.0 |
| `pre_approved_escalation`(事前承認 + order-mgmt → エスカレ) | 5.0 | 5.0 | 1.0 |

メトリクス:
- `sre_task_success`(LLM 判定): その時点で達成すべきゴールを達成しているか
- `sre_flow_compliance`(LLM 判定): 受付・調査・HITL・完了の各段階の手順遵守
- `sre_trajectory_checks`(決定論): ツール呼び出し順序(propose 前に action しない等)

`make eval` は generate + grade を一気に走らせる。artifact は `artifacts/traces/` と
`artifacts/grade_results/` に保存(`.gitignore` 済み)。
仕様変更後は古い trace を掃除してから再実行(全 trace が grade 対象になるため)。

---

## デプロイ(未実施 — 明示的な承認が必要)

```bash
make deploy-setup    # scaffold enhance で agent_runtime 設定を追加(初回のみ)
make deploy          # agents-cli deploy(Agent Runtime へ、所要5〜10分)
```

- Agent Runtime の Python は 3.12 固定
- `data/runbooks/` がバンドルに含まれることを事前確認
- **`make deploy` は人間の明示的な承認のもとでのみ実行すること**

## 実装差し替えのポイント

現在の復旧・エスカレーションはすべてモック。
[`app/tools/remediation_tools.py`](./app/tools/remediation_tools.py) の
`restart_server` / `escalate_to_engineer` を実装差し替えるだけで実環境に接続可能:

| ツール | 差し替え先の例 |
|---|---|
| `restart_server` | Compute Engine API の instances.reset / MIG の rolling restart |
| `escalate_to_engineer` | PagerDuty / Opsgenie / Slack webhook / Cloud Tasks |

シグネチャと返却契約(`{"status": ..., ...}` の dict)を維持すれば、
エージェントの instruction や eval を書き換えずに実運用へ移行できる。

## 観測性

`agents-cli` が生成した OpenTelemetry 計装で Cloud Trace / Cloud Logging に自動連携。
BigQuery Agent Analytics を有効化する場合は
`agents-cli scaffold enhance --bq-analytics`。
