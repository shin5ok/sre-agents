# SRE障害対応エージェント 実装作業ログ

**プロジェクト**: 本部システム(GCP)の障害対応を自動化するAIエージェント
**期間**: 2026-07-09(初回)、2026-07-13(HITL追加)
**成果**: Eval 4ケース満点 / ユニットテスト25件パス / lint全チェック通過

---

## 目次

1. [背景と要件](#1-背景と要件)
2. [事前確認(ユーザーへの質問)](#2-事前確認ユーザーへの質問)
3. [環境調査と技術選定](#3-環境調査と技術選定)
4. [実装したもの](#4-実装したもの)
5. [作業手順(実施順)](#5-作業手順実施順)
6. [遭遇した問題と解決策](#6-遭遇した問題と解決策)
7. [評価結果](#7-評価結果)
8. [今後の作業](#8-今後の作業)
9. [追加機能: HITL(復旧実施前の承認ステップ)](#9-追加機能-hitl復旧実施前の承認ステップ)

---

## 1. 背景と要件

### SPEC.md の要件

- 全国に拠点を展開する企業(GCP利用)
- 現状の課題: システム障害 → 店舗申請 → 受付起票 → 運用エンジニアがログ調査・復旧 のチェーンが長く、障害が長引く
- 目的: AIエージェントで自動化・半自動化

### 実装すべき2エージェント

| エージェント | 責務 |
|---|---|
| **受付エージェント** | 障害申請の受付・症状の聞き取り(足りない部分は満足するまで)・DB書き込み・調査エージェントへ通知 |
| **調査エージェント** | 原因調査・ドキュメント/過去ログから対応方法抽出・実施orエスカレーション・対応ログ記録 |

### 技術要件(スペックで固定)

- `agents-cli` で agent テンプレート作成、ADK関連スキルインストール
- Google ADK 2.x / Python 3
- 適切にTool・サブエージェントを分ける
- eval実行できるように
- 各コマンドは `make` で実行できるように Makefile を書く

---

## 2. 事前確認(ユーザーへの質問)

4問を確認し、以下の方針で確定:

| 質問 | 回答 |
|---|---|
| 連携方式 | **単一アプリ内サブエージェント**(受付→調査を transfer_to_agent で引き継ぎ) |
| データベース | **Firestore** + モック用の過去対応ログ・運用ドキュメントも作成 |
| 復旧アクション | **モックツール**(後で実装差し替え可能なインターフェイス) |
| 実行環境 | **Vertex AI + ローカル実行**、Makefileにdeployターゲットも用意 |

---

## 3. 環境調査と技術選定

### インストール済みツールの確認

```
agents-cli  : 0.4.0     → skills(0.5.0)と揃えるため要アップグレード
uv          : 0.11.18
python3     : 3.11.2
gcloud      : project=your-project-id, ADC有効
adk         : 未インストール(uv run adk で使う)
```

### 重要な発見事項

1. **PyPIには google-agents-cli 1.0.0 があるが skills は 0.5.0** → `~=0.5.0` にピン(1.0.0にすると skill と齟齬)
2. **Firestore `(default)` は us-central1 に固定**(リージョン変更不可) → named DB `sre-agents` を asia-northeast1 に新規作成
3. **Vertex モデル**: `gemini-3.5-flash` 存在確認 / `gemini-flash-latest` エイリアスは**なし**(404リスク回避のため具体的IDを固定)
4. **scaffold create は既存ディレクトリでハードエラー** → `/tmp` に scaffold → cp でリポジトリ直下へ移設

### 決定した構成

```
モデル呼び出し     : global エンドポイント(scaffoldがハードコード、維持)
Firestore         : named DB "sre-agents" @ asia-northeast1
manifest/deploy   : asia-northeast1
```

**リージョン3種を混同しないこと** — これが後述の落とし穴の温床でした。

---

## 4. 実装したもの

### アーキテクチャ

```
root_agent = reception_agent (受付エージェント)
  └─ sub_agents=[investigation_agent (調査エージェント)]   # ファクトリ関数で生成
```

- 受付は必須6項目(店舗ID・店舗名/対象システム/症状/発生日時/影響範囲/報告者名)が揃うまで日本語で聞き取り
- 揃ったら `create_support_case` で Firestore へ起票 → `transfer_to_agent` で調査へ
- 調査は手順厳守: ステータス更新 → 状態・ログ確認 → **過去対応ログ+運用手順書の両方検索** → 手順書が認めれば `restart_server` / 認めなければ `escalate_to_engineer` → **必ず** `record_resolution_log`

### ファイル一覧

| パス | 種別 | 内容 |
|---|---|---|
| `app/agent.py` | 改修 | env bootstrap維持、demoツール削除、root_agent = create_reception_agent() |
| `app/config.py` | 新規 | MODEL_ID = "gemini-3.5-flash", FIRESTORE_DATABASE, RUNBOOK_DIR |
| `app/firestore_client.py` | 新規 | named DB "sre-agents" の遅延シングルトン |
| `app/mock_world.py` | 新規 | 決定論的な疑似システム3種(売上/在庫/発注)+固定ログ |
| `app/sub_agents/__init__.py` | 新規 | 共通の `build_model()` / `build_generate_content_config()` |
| `app/sub_agents/reception.py` | 新規 | 受付エージェント(日本語 instruction + ファクトリ) |
| `app/sub_agents/investigation.py` | 新規 | 調査エージェント(日本語 instruction + ファクトリ) |
| `app/tools/case_tools.py` | 新規 | create_support_case / get_support_case / update_case_status |
| `app/tools/diagnostic_tools.py` | 新規 | list_managed_systems / get_system_status / get_server_logs |
| `app/tools/knowledge_tools.py` | 新規 | search_past_incidents / search_runbooks / record_resolution_log |
| `app/tools/remediation_tools.py` | 新規 | restart_server / escalate_to_engineer(モック) |
| `data/runbooks/*.md` | 新規 | 日本語運用手順書5本 |
| `scripts/seed_firestore.py` | 新規 | シードデータ投入(冪等) |
| `tests/unit/test_*.py` | 新規 | mock_world整合性 / 検索ロジック / 配線の3本 |
| `tests/eval/eval_config.yaml` | 改修 | カスタムメトリクス3本(LLM判定2本+決定論的軌跡チェック) |
| `tests/eval/datasets/basic-dataset.json` | 改修 | 3ケース(単一turnプロンプト) |
| `Makefile` | 新規 | install/db-create/seed/run/chat/playground/test/eval/lint/deploy... |
| `.env.example` | 新規 | GOOGLE_CLOUD_PROJECT, FIRESTORE_DATABASE |
| `README.md` | 改修 | 日本語 quick start と make ターゲット表 |
| `CLAUDE.md` | 改修 | Repository status を scaffolded 状態に更新、落とし穴を追記 |

### モックワールドの設計

| system_id | 表示名 | 状態 | 再起動 | ログ異常 | シナリオ結末 |
|---|---|---|---|---|---|
| sales-mgmt | 売上管理システム | degraded | 可 | HikariPool 接続プール枯渇 | 再起動で復旧 |
| inventory-mgmt | 在庫管理システム | degraded | 可 | OutOfMemoryError + Full GC | 再起動で復旧 |
| order-mgmt | 発注管理システム | down | **不可** | No space left on device | **エスカレーション** |

- ログは各8〜12行の固定行(INFOノイズ+診断根拠となる異常行)
- 再起動成功後は `state["remediated_systems"]` 経由で healthy 応答
- **手順書・過去事例・ログの3つが同じ結論を指す整合設計** — LLMが根拠を積み重ねて正解に辿り着ける

### Firestore データモデル

**support_cases/{case_id}**:
- case_id (`CASE-YYYYMMDD-HHMMSS`), store_id, store_name, system_id, symptom, started_at, impact_scope, reporter_name
- status (open/investigating/resolved/escalated), history[{ts,status,note}], created_at

**incident_logs/{incident_id}**:
- incident_id, case_id, system_id, title, symptom, root_cause, action_taken, outcome, escalated(bool)
- tags[日本語キーワード], source("seed"|"agent")

**検索**: 全文検索なしで割り切り — 最大100件fetch + Python内キーワードスコアリング(純関数、ユニットテスト対象)。上位3件返却。

---

## 5. 作業手順(実施順)

### Phase 1: 準備

1. `uv tool install "google-agents-cli~=0.5.0" --force` → 0.5.1 導入確認
2. `agents-cli scaffold create sre-agents --agent adk --prototype --region asia-northeast1 --agent-guidance-filename CLAUDE.md --output-dir /tmp --auto-approve`
3. `/tmp/sre-agents` 検分 → 生成物確認(Makefile なし、tools.py なし、Dockerfile あり)
4. `cp -a /tmp/sre-agents/. ~/repos/sre-agents/`(scaffold版 CLAUDE.md は退避してからマージ)
5. 既存 CLAUDE.md に scaffold ガイダンスを追記、Repository status を更新
6. `agents-cli install` + `uv add google-cloud-firestore` → ADK 2.4.0 導入
7. `gcloud firestore databases create --database=sre-agents --location=asia-northeast1 --type=firestore-native` → DB作成

### Phase 2: アプリコード実装

順序: `config.py` → `mock_world.py` → `firestore_client.py` → `tools/*` 4本 → `sub_agents/*` 2本 → `agent.py` 改修

配線検証:
```
app name: app
root: reception_agent | sub_agents: ['investigation_agent']
reception tools: ['list_managed_systems', 'create_support_case']
investigation tools: [get_support_case, update_case_status, get_system_status,
                      get_server_logs, search_past_incidents, search_runbooks,
                      restart_server, escalate_to_engineer, record_resolution_log]
```

### Phase 3: データとテスト

8. `data/runbooks/*.md` 5本作成(common-restart / sales-mgmt / inventory-mgmt / order-mgmt / escalation-policy)
9. `scripts/seed_firestore.py` 作成 → `make seed` で INC-2025-0001..0005 の5件を投入
10. 検索ロジック検証: 3クエリすべて正しいセクションにヒット(検索精度が足りなかった箇所は `_split_runbook_sections` で `# タイトル` もスコア対象に追加)
11. `Makefile`(11ターゲット、`.PHONY`)、`.env.example`、`.gitignore` に `artifacts/` 追加
12. ユニットテスト3本 → `make test` で **17 passed**

### Phase 4: eval とループ

13. eval データセット(3ケース)と `eval_config.yaml`(カスタムメトリクス)を作成
14. `agents-cli lint --fix` → RUF001(全角カンマ)を `# noqa` で許容、他は自動修正
15. スモークテスト実施 → 発覚した問題を修正(下記6章参照)
16. `make eval` を4回反復してスコアを完成

---

## 6. 遭遇した問題と解決策

### 問題1: 受付が transfer_to_agent を呼ばない

**症状**: 「調査エージェントへ引き継ぎます」と応答するだけで、実際の `transfer_to_agent` を呼ばずに応答終了。

**原因**: instruction の表現が「〜に引き継ぐ」となっており、LLMが「宣言だけで完了」と解釈。

**対処**: instruction に「**transfer_to_agent を呼ぶまで、受付の仕事は完了しない**」と明示。

```diff
- 起票に成功したら、ケースIDを報告者に伝え、直ちに investigation_agent へ転送する。
+ 起票に成功したら、ケースIDを報告者に伝え、続けて同じ応答の中で必ず transfer_to_agent を
+ 呼び出して investigation_agent へ引き継ぐ。ケースIDの報告だけで応答を終えてはならない
+ (transfer_to_agent を呼ぶまで、受付の仕事は完了しない)。
```

### 問題2: Vertex 呼び出しが120秒無応答でハング

**症状**: `agents-cli run` が `HTTPConnectionPool ... Read timed out` で失敗。サーバー側ログには `Sending out request` の後、次のイベントが2分後の cancel まで無応答。

**原因**: `agents-cli run` の SSE クライアントは無通信120秒で切断。Gemini呼び出しがまれに応答なくハングし、リトライも走らずタイムアウトしていた。

**対処**: `app/sub_agents/__init__.py` に共通のモデル設定を追加し、HTTPタイムアウトを **60秒** に設定(クライアント側の120秒より短くしてリトライを走らせる)。

```python
def build_model() -> Gemini:
    return Gemini(
        model=MODEL_ID,
        client_kwargs={
            "http_options": types.HttpOptions(
                timeout=60_000,  # ミリ秒
                retry_options=types.HttpRetryOptions(attempts=3),
            )
        },
    )
```

**教訓(CLAUDE.md に記録)**: agents-cli run の120秒制限より短いタイムアウトをモデルに設定すること。

### 問題3: 組み込み multi_turn_* メトリクスがマルチエージェント非対応

**症状**: `make eval` 1回目で `400 INVALID_ARGUMENT. Multiple agents detected in agent data. The MultiTurnTaskSuccessAutorater autorater does not support multiagent evaluation.`

**原因**: `multi_turn_task_success` と `multi_turn_tool_use_quality` は単一エージェント前提。本プロジェクトは受付+調査の2エージェント構成。

**対処**: カスタムメトリクス3本に切替:

- `sre_task_success`: LLM判定 — ゴール達成度
- `sre_flow_compliance`: LLM判定 — 手順遵守(起票前聞き取り/対応前ナレッジ検索/対応ログ記録)
- `sre_trajectory_checks`: **決定論的Python関数** — ツール呼び出し順序チェック(create_support_case → transfer_to_agent、対応前に get_server_logs + search_past_incidents + search_runbooks、record_resolution_log の存在)

**教訓(CLAUDE.md に記録)**: このアプリではマルチエージェントの間は組み込みメトリクスに戻さない。

### 問題4: カスタムメトリクステンプレートで {prompt} が使えない

**症状**: `400 INVALID_ARGUMENT. Variable prompt is required but not provided.`

**原因**: `agent_data` 形状(継続形式)の eval ケースでは `{prompt}` 変数が提供されない。

**対処**: テンプレートから `{prompt}` を削除し、`{response}` と `{agent_data}` のみで採点するように変更。

### 問題5: agents-cli 0.5.1 の eval が継続形式の履歴を再現しない

**症状**: `full_flow_resolution` ケースを `agent_data.turns` で書いたが、評価時に受付が「(既に報告済みの)店舗ID・症状・発生日時をもう一度質問する」動作をした。トレースを見ると `list_managed_systems` しか呼ばれていない。

**原因**: `agents-cli eval generate` は継続形式の過去 turn を**モデルの会話コンテキストに渡さない**。過去 turn はトレースの飾りになるだけ。

**対処**: eval ケースを**単一turnプロンプト形式**に書き直し(すべての必須項目を1つのユーザーメッセージに含める)。

**教訓(CLAUDE.md に記録)**: 本当のマルチターン評価が必要なら `agents-cli eval dataset synthesize` を使う。

### 問題6: 受付が既に報告済みの項目を再質問する

**症状**: 「渋谷店(店舗ID:1102)の佐藤です...」と最初から店舗名・店舗ID・報告者が明記されていても、これらを再度質問してしまう。

**対処**: instruction を強化:

```
- 質問する前に、必ず会話履歴全体(最初の報告文を含む)を見直し、既に報告されている項目を
  整理する。既に得られている項目を再度質問してはならない。
- 報告者が対象システム名を明言している場合は、list_managed_systems との突き合わせだけ行い、
  報告者への再確認はしない。
- 不足している項目だけを、簡潔な番号付きリストで一度にまとめて質問する。
```

同時に評価者(rubric)側にも「冒頭の報告文に含まれている情報(例:「渋谷店ですが」は店舗名の報告にあたる)は既に得られている項目として扱う」と明記。

### 問題7: 検索精度が足りない

**症状**: `search_runbooks("発注管理 ディスク ストレージ")` で common-restart.md と escalation-policy.md ばかり上位、肝心の order-mgmt.md が3位。

**原因**: `# タイトル`(発注管理システム 運用手順書)がスコア計算に含まれていなかった。

**対処**: `_split_runbook_sections` に文書タイトル抽出を追加し、`_score_text` の対象に `title` を含めた。以降、システム別クエリで正しい手順書が確実に上位ヒット。

---

## 7. 評価結果

### 最終スコア(4回目の反復)

```
┏━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━┳━━━━━━━━┓
┃ Metric Name           ┃ Property        ┃  Value ┃
┡━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━╇━━━━━━━━┩
│ sre_task_success      │ mean_score      │ 5.0000 │
│ sre_flow_compliance   │ mean_score      │ 5.0000 │
│ sre_trajectory_checks │ mean_score      │ 1.0000 │
└───────────────────────┴─────────────────┴────────┘

intake_interview_incomplete : task=5.0 / flow=5.0 / trajectory=1.0
full_flow_resolution        : task=5.0 / flow=5.0 / trajectory=1.0
escalation_flow             : task=5.0 / flow=5.0 / trajectory=1.0
```

### スモークテストの実績

| シナリオ | 結果 |
|---|---|
| 完全報告(売上管理) | 26秒で `list_managed_systems → create_support_case → transfer → update_case_status → get_system_status + get_server_logs → search_past_incidents + search_runbooks → restart_server → get_system_status → record_resolution_log → update_case_status` を自走 |
| エスカレーション(発注管理) | 手順書に従い再起動せず `escalate_to_engineer` → 対応ログ escalated=true で記録 |
| 曖昧な報告(在庫管理) | 追加質問 → セッション継続で満足 → フル対応完了 |

Firestore コンソールで:
- support_cases: 5件(open / resolved / investigating / escalated / resolved)
- incident_logs: シード5件 + エージェント記録3件

### テストとlint

```
pytest tests/unit          : 17 passed
agents-cli lint            : ruff / format / codespell / ty すべてパス
```

---

## 8. 今後の作業

### すぐできること

- `make playground` で adk web の対話UIから触る(デモ用途)
- `make chat PROMPT="..."` でセッション永続の対話テスト

### デプロイ関連(未実施 — ユーザーの明示的な承認待ち)

1. `make deploy-setup` → `agents-cli scaffold enhance . --deployment-target agent_runtime` で prototype に Agent Runtime 設定を追加
2. `make deploy` → `agents-cli deploy` で Agent Engine にデプロイ
   - 注意: Agent Runtime は Python 3.12 固定、デプロイに5〜10分
   - `data/runbooks/` がバンドルに含まれることを確認する必要あり

### 拡張候補

- eval ケース追加(現状は3ケース、5-10ケースまで増やすと安定性が上がる)
- 復旧ツールの実装差し替え(remediation_tools.py の同じシグネチャを維持したまま実GCP操作へ)
- 過去対応ログ検索の高度化(現状はキーワードマッチ、必要なら Vertex AI Search / Vector Search へ)

### 未コミット

- git は greenfield 状態(SPEC.md と CLAUDE.md のみ既存)
- 今回の作業はすべて未コミット
- コミットはユーザーの明示的な指示があってから

---

## 補足: 学んだこと(memory化候補)

1. **agents-cli 0.5.1 の eval は継続形式(agent_data)を実効しない** — 単一turnで書くか synthesize を使う
2. **組み込み multi_turn_* メトリクスはマルチエージェント非対応** — カスタムメトリクスで代替
3. **agents-cli run の SSE は120秒無通信でタイムアウト** — Gemini モデルのHTTPタイムアウトを 60秒に設定
4. **Firestore の (default) DB はリージョン変更不可** — 別リージョンで運用するなら named DB を新規作成
5. **google-agents-cli 1.0.0 がPyPIにあるが skills は 0.5.0** — `~=0.5.0` にピン
6. **eval の LLM judge テンプレートは `{prompt}` を含めないと user の初回発話を見ない** — trace の agent_data だけでは初回 user 発話が判定に使えないため、単一turnケースでは `{prompt}` を明示的に含める

---

## 9. 追加機能: HITL(復旧実施前の承認ステップ)

### 動機
実運用では、SRE エージェントが独断で `restart_server` や `escalate_to_engineer` を
実行するのはリスクが大きい(業務影響のあるオペレーション)。担当者が「原因・作業内容・
作業中の影響」を確認してから承認する Human-in-the-Loop ステップを間に挟むよう変更。

### 実装

**1. 新ツール `propose_recovery_plan`** (`app/tools/remediation_tools.py`)
- 引数: `case_id, root_cause, planned_action, action_type("restart"|"escalate"), impact_during_work`
- 副作用: `tool_context.state["recovery_plan_proposed"] = action_type` をセット
- 返却: `{"status": "awaiting_approval", "plan": {...}, "message": "...承認してから実施ツールを呼んでください"}`
- 実際の復旧操作は行わない(計画の登録・提示のみ)

**2. 実施系ツールに防御深化のゲート追加**
- `restart_server` / `escalate_to_engineer` は state の `recovery_plan_proposed` を確認
- 未セット or action_type 不一致なら status=error を返却して実行拒否
- LLM が指示を無視しても実際の操作は走らない設計
- `escalate_to_engineer` に `tool_context` 引数を追加(シグネチャ変更)

**3. Instruction 更新** (`app/sub_agents/investigation.py`)
- 手順4「復旧方針の提示(HITL)」を明示 — propose_recovery_plan の呼び出し必須、
  呼び出し後は原則応答を止めて次入力での承認を待つ
- 例外: 初回依頼文に「そのまま実施」「事前承認済み」等の明示的事前承認があれば
  そのまま実施へ進んでよい(運用上の柔軟性)
- 承認前の実施系ツール呼び出しは禁止事項として明記

**4. eval を4ケースに再設計** (`tests/eval/datasets/basic-dataset.json`)
- `intake_interview_incomplete`: 情報不足 → 追加質問(既存)
- `propose_awaits_approval`: 完全報告(事前承認なし) → propose して承認待ちで停止
- `pre_approved_resolution`: 完全報告(事前承認あり)+ sales-mgmt → propose→restart→完了
- `pre_approved_escalation`: 完全報告(事前承認あり)+ order-mgmt → propose→escalate→完了

**5. 決定論的軌跡チェック更新** (`sre_trajectory_checks`)
- action(restart/escalate)が呼ばれた場合、その前に propose_recovery_plan があること
- action が呼ばれず投稿だけの場合、propose_recovery_plan は呼ばれていること
- 承認前の実施系呼び出しは無し

**6. HITL 動作の追加ユニットテスト** (`tests/unit/test_hitl_gating.py` — 8件)
- propose なしでの restart/escalate は error
- action_type 不一致(restart 計画で escalate 実施等)は error
- propose 済みなら実行成功
- 再起動不可システムは承認があっても実行不可(実世界制約)

### 遭遇した問題

**問題A: judge が「事前承認」を認識せず、trajectory は満点なのに flow_compliance が 1〜2点**
- 原因: LLM judge の template が `{response}` と `{agent_data}` しか渡していなかった。
  trace の `agent_data` には agent の tool call/text のみが入り、user の初回発話は含まれない。
  結果、judge は「trace のどこにも user の "承認" 表現がない」と判断して減点していた。
- 対処: template に `{prompt}` を追加(単一turnケースでは populate される)。CLAUDE.md にも
  「単一turnなら {prompt} 使ってよい」と記録した。

**問題B: `agents-cli eval grade` は artifacts/traces/ 内の全ファイルを対象にする**
- 症状: 新規4ケースを grade したはずが19件評価されていた
- 対処: 古い trace ファイル(pre-HITL のもの5個)を `rm` してから再 grade
- 教訓: 大きな仕様変更後は `artifacts/traces/` を一度掃除するのが安全

### 最終スコア(HITL適用後)

| ケース | task_success | flow_compliance | trajectory_checks |
|---|---|---|---|
| intake_interview_incomplete | 5.0 | 5.0 | 1.0 |
| propose_awaits_approval | 5.0 | 5.0 | 1.0 |
| pre_approved_resolution | 5.0 | 5.0 | 1.0 |
| pre_approved_escalation | 5.0 | 5.0 | 1.0 |
| **平均** | **5.0** | **5.0** | **1.0** |

### HITL スモークテスト実績(ADK Runner 直叩き)

**Case A(事前承認なし)** — 9 tool calls、27秒:
```
reception: list_managed_systems → create_support_case → transfer_to_agent
investigation: update_case_status → get_system_status → get_server_logs
             → search_past_incidents → search_runbooks → propose_recovery_plan → STOP
```
最終応答に原因・作業内容・作業中影響・「進めてよろしいでしょうか?」の確認文が含まれる。
`restart_server` / `escalate_to_engineer` は呼ばれない。

**Case B(事前承認あり)** — 13 tool calls、41秒:
```
... (Case A と同じ流れ) ... → propose_recovery_plan
             → restart_server → get_system_status(healthy確認)
             → record_resolution_log → update_case_status("resolved")
```
propose の後に続けて実施系ツールを呼び、対応ログ記録まで完了。

---

## 2026-07-13: GCP障害シナリオ + Gemini Cloud Assist MCP 連携

**ゴール**: GCPの障害の例を追加し、Gemini Cloud Assist(リモートMCP)で実GCP環境を
調査できるようにする。調査対象プロジェクトIDは環境変数 `SANDBOX_PROJECT_ID` から取得。

### 追加・変更点

- `app/tools/gcp_assist.py`(新規): Cloud Assist リモートMCP
  (`https://geminicloudassist.googleapis.com/mcp` / Streamable HTTP)の `McpToolset`。
  - 認証: `header_provider` で ADC のアクセストークンを毎回供給(期限切れは自動refresh)。
    quota project 未設定の資格情報には `x-goog-user-project: $SANDBOX_PROJECT_ID` を付与。
  - `tool_filter=["investigate_issue", "ask_cloud_assist"]` — mutative な
    `invoke_operation` / `design_infra` / `optimize_costs` は意図的に非公開。
  - `SANDBOX_PROJECT_ID` 未設定なら None(eval / CI は従来と完全同一の構成)。
  - `sse_read_timeout=110s`(agents-cli run の SSE 120s 切断より短く)。
- `app/config.py`: `get_sandbox_project_id()` 追加。
- `app/sub_agents/investigation.py`: SANDBOX_PROJECT_ID 設定時のみ instruction に
  「GCP層の調査(Cloud Assist)」節を追記("projects/<id>" を展開、contextId 引き継ぎ、
  読み取り専用の縛り、タイムアウト時の再試行1回)+ ツールセットを追加。
- `app/mock_world.py`: 4つ目のシステム `store-app`(店舗アプリAPI / Cloud Run)。
  モック側は外形監視の抜粋のみ(HTTP 500 多発、/healthz は 200)で、真因は
  Cloud Assist 経由でしか特定できない設計。`platform` キーで GCPホストを示す
  (`get_system_status` が platform をパススルー)。
- `data/runbooks/store-app.md`: Cloud Assist での調査手順と既知事象(環境変数設定不備
  → エスカレーション)。`scripts/seed_firestore.py`: INC-2026-0006 追加(計6件)。
- GCP障害の実体: `scripts/gcp_incident_app/`(Flask、`FAIL_MODE=db_timeout` で
  DB接続タイムアウト風 ERROR ログ + 500)+ `scripts/gcp_incident.sh`
  (setup/deploy/expose/break/status/resolve/clean)+ `make gcp-incident-*`。
  デプロイは認証必須が既定(公開は `expose` の明示実行のみ)。組織ポリシーで
  未認証が拒否される環境では ID トークンで自動フォールバック。

### 検証結果(初回は sandbox = your-project-id で実施 → 後に your-sandbox-project-id へ切替、旧サービスは削除済み)

- MCP `tools/list`(認証不要): OK。ADK `McpToolset.get_tools()` 経由でも
  `['ask_cloud_assist', 'investigate_issue']` を取得(認証ヘッダー供給経路も通過)。
- `deploy` → 5x 200 / `break` → 15x 500、Cloud Logging に severity=ERROR の
  `DB connection failed: could not connect to server db.internal:5432` を確認。
- 認証付き `tools/call` は **"The caller does not have permission"** — `roles/owner`
  だけでは不足(プレビューの `mcp.tools.call`)。`roles/mcp.toolUser`(GA)+
  `roles/geminicloudassist.user`(BETA)の付与が必要で、`gcp_incident.sh setup` に
  組み込み済み(このセッションでは IAM 変更が承認されず人間の実行待ち)。
- ユニットテスト 29 件 / `agents-cli lint` 全て pass。eval への影響なし
  (eval は SANDBOX_PROJECT_ID なしで実行される)。
