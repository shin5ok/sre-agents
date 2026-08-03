# Firestore が必要になるのはいつか

**目的**: 「このコマンドを動かすのに Firestore は要るのか」を毎回コードを追わずに判断できるようにする。
特に `agents-cli eval` のサブコマンド別の要否と、CI に載せるときの影響をまとめる。
eval 全体の運用は [`eval-operations.md`](./eval-operations.md)、実装の経緯は
[`implementation-log.md`](./implementation-log.md) を参照。

**調査日**: 2026-08-03
**調査方法**: `app/` のツール実装と `agents-cli` v1.2.1 のインストール済みソースを直接確認し、
存在しない database を指した実行と、実際の Firestore のドキュメント数で裏を取った。

---

## 目次

1. [結論](#1-結論)
2. [Firestore を使うツールは 5 つだけ](#2-firestore-を使うツールは-5-つだけ)
3. [`agents-cli eval` のサブコマンド別](#3-agents-cli-eval-のサブコマンド別)
4. [`make` ターゲット別](#4-make-ターゲット別)
5. [eval データセット 5 ケースの内訳](#5-eval-データセット-5-ケースの内訳)
6. [接続先の決まり方](#6-接続先の決まり方)
7. [Firestore が使えないときの落ち方](#7-firestore-が使えないときの落ち方)
8. [eval が書き込むデータと蓄積](#8-eval-が書き込むデータと蓄積)
9. [CI に載せるときの判断](#9-ci-に載せるときの判断)

---

## 1. 結論

- **エージェントを実走させるサブコマンドだけ** Firestore が要る(`generate` / `run` /
  `dataset synthesize` / `optimize`)。**採点系(`grade` / `compare` / `analyze`)は不要。**
- クライアントは遅延シングルトン(`app/firestore_client.py:13`)なので、**import や
  `root_agent` の構築だけでは接続しない**。実際にツールが呼ばれた瞬間だけ。
- `make eval` は**実データを書き込む**(1 回で `support_cases` に 3 件、`incident_logs` に 2 件)。
  CI に載せるなら database を分けるかどうかを先に決める必要がある([§9](#9-ci-に載せるときの判断))。

---

## 2. Firestore を使うツールは 5 つだけ

| ツール | 操作 | コレクション | 実装 |
|---|---|---|---|
| `create_support_case` | write | `support_cases` | `app/tools/case_tools.py:162` |
| `get_support_case` | read | `support_cases` | `app/tools/case_tools.py:179` |
| `update_case_status` | read + write | `support_cases` | `app/tools/case_tools.py:201` |
| `search_past_incidents` | read | `incident_logs` | `app/tools/knowledge_tools.py:78` |
| `record_resolution_log` | read + write | 両方 | `app/tools/knowledge_tools.py:174-199` |

### 紛らわしいが触らないもの

- **`propose_case_draft`**(`case_tools.py:27`)— state に草案を置くだけの純関数。
  起票前レビューの HITL ゲートは **DB なしで成立する**。
- **`search_runbooks`**(`knowledge_tools.py:106-140`)— `data/runbooks/*.md` をディスクから読む。
  「手順書検索」と「過去対応ログ検索」で必要なものが違う。
- **`list_managed_systems` / `get_system_status` / `get_server_logs` / 復旧系ツール** —
  `app/mock_world.py` のモックデータ(`mock_world.py:6` は Firestore に言及しているが
  コメントのみで、実際の参照はない)。

---

## 3. `agents-cli eval` のサブコマンド別

| サブコマンド | Firestore | 根拠 |
|---|---|---|
| `eval generate` | **必要** | エージェントを実走させる。5 ケース中 3 ケースが Firestore ツールを呼ぶ([§5](#5-eval-データセット-5-ケースの内訳)) |
| `eval run` | **必要** | `generate` + `grade` の連結 |
| `eval dataset synthesize` | **必要** | `_synthesize_runner.py:186,235` で `root_agent` をロードして推論を回す |
| `eval optimize` | **必要** | GEPA が改善ループのたびにエージェントを実走させる(`cmd_optimize.py:162,236,257`) |
| `eval grade` | 不要 | 既存 trace の採点のみ。LLM judge には Vertex AI が要るが Firestore は無関係 |
| `eval compare` / `analyze` / `metric list` / `results` | 不要 | JSON を読むだけ |
| `eval submit` | **手元では不要** | dataset と `agent=<resource_name>` を投げるだけ(`cmd_submit.py:108-111`)。エージェントは**クラウド側で走る**ので、デプロイ先の SA に Firestore 権限が要る |

決定論メトリクスだけの採点(`--metrics sre_trajectory_checks`)は **GCP を一切呼ばず 4.5 秒**で終わる。
これが CI の T1 層を成立させている根拠([`eval-operations.md` §4-(5)](./eval-operations.md#4-実測で確認した-cli-の挙動))。

---

## 4. `make` ターゲット別

| コマンド | Firestore | 備考 |
|---|---|---|
| `make seed` | **必要(write)** | `incident_logs` にモック投入(`scripts/seed_firestore.py:160`) |
| `make eval` | **必要** | = `eval run` |
| `make run` / `chat` / `playground` | **起票まで進めば必要** | `make welcome`(「こんにちは」)だけなら不要 |
| `make eval-negative` | 不要 | trace を改変して `grade` するだけ |
| `make eval-gate` / `eval-baseline` | 不要 | jq で結果 JSON を読むだけ |
| `make test`(tests/unit) | 不要 | `_FakeFirestore` で差し替え(`tests/unit/test_intake_review.py:27,47`) |
| `tests/integration` | 不要 | 送るのは `"Hi!"` のみでツールを呼ばない(Vertex AI は必要) |
| `make lint` | 不要 | |

---

## 5. eval データセット 5 ケースの内訳

`artifacts/traces/traces_20260803_213523.json`(全ケース満点の正常トレース)の実際の呼び出し列。
**Firestore に到達するのは 3 ケース**。

```
intake_interview_incomplete
  list_managed_systems                                         → 触らない

intake_review_awaits_confirmation
  list_managed_systems → propose_case_draft                    → 触らない

propose_awaits_approval
  … propose_case_draft → create_support_case → transfer_to_agent
  → update_case_status → … → search_past_incidents → …         → 触る

pre_approved_resolution
  … → restart_server → … → record_resolution_log
  → update_case_status                                         → 触る

pre_approved_escalation
  … → escalate_to_engineer → record_resolution_log
  → update_case_status                                         → 触る
```

受付フェーズだけで完結する 2 ケース(起票前レビューの検証)は DB を必要としない。
**HITL の起票前ゲートは Firestore なしでもテストできる**、という設計上の性質がここに出ている。

---

## 6. 接続先の決まり方

| 項目 | 決まり方 | 既定値 |
|---|---|---|
| プロジェクト | `GOOGLE_CLOUD_PROJECT`。未設定なら ADC のデフォルト(`app/config.py:18-28`) | — |
| database | `FIRESTORE_DATABASE`(`app/config.py:11`) | `sre-agents` |
| リージョン | database 作成時に固定 | `asia-northeast1` |

`Makefile:67-69` が `eval` に `GOOGLE_CLOUD_PROJECT=$(PROJECT_ID)` を明示しているのは、
**eval の inference runner が `.env` を `override=False` で読む**ため。シェルに別プロジェクトが
export されていると環境変数側が `.env` より優先され、Firestore 403 で調査系ケースが全滅する。

> エミュレータ対応は無い。`FIRESTORE_EMULATOR_HOST` はコードにもドキュメントにも出てこないので、
> オフラインで Firestore 経路を動かす手段は現状ない。

---

## 7. Firestore が使えないときの落ち方

ツール側に try/except が無い(`knowledge_tools.py:76-82` は素で `get_client()` を呼ぶ)ので、
例外がそのまま上がる。存在しない database を指した実測:

```
$ FIRESTORE_DATABASE=no-such-db-xyz uv run python -c "…search_past_incidents('売上管理 タイムアウト')"
RAISED: NotFound
404 The database no-such-db-xyz does not exist for project your-project-id
```

権限不足なら 403。

**注意すべきなのは eval での見え方**。`eval generate` はツールが落ちても止まらず、
エラー応答を含んだ trace を出力する。それを grade すると `sre_trajectory_checks` の
`logs_consulted` / `past_incidents_consulted` が落ちるため、

> **ゲートは「Firestore が死んでいる」を「エージェントが劣化した」として報告する。**

スコアが落ちたとき、まず trace のツール応答を見て 403/404 が混ざっていないかを確認する。
`eval_gate.sh` が区別できるのは「judge の失敗(`score=null` / 採点エラー)」までで、
**ツール基盤の失敗はエージェントの失敗と区別できない**。

---

## 8. eval が書き込むデータと蓄積

`make eval` 1 回あたり:

- `support_cases` に **3 件**(`create_support_case` を通る 3 ケース)
- `incident_logs` に **2 件**(`record_resolution_log` を通る 2 ケース)

2026-08-03 時点の実測:

```
support_cases: 62 docs   (末尾は CASE-20260803-213655 など eval 由来)
incident_logs: 52 docs   (make seed の分 + INC-CASE-2026… の eval 由来)
```

問題は `incident_logs` が **`search_past_incidents` の検索対象そのもの**だという点。

> **eval が自分の書いた過去ログを、次の eval で読む。**

回数を重ねるほど検索ヒットが増え、評価条件が静かに変わっていく。スコアが動いたときに
「エージェントを直したから」なのか「過去ログが増えたから」なのかを切り分けられなくなる。
現状は 52 件で seed 分に対して無視できない量になっている。

---

## 9. CI に載せるときの判断

[`eval-operations.md` §9](./eval-operations.md#9-作業状況) の未着手項目
「CI 用 Firestore database をどうするか決定」の実体がこれ。

- CI の **T1 層(PR ごと・決定論メトリクスのみ)も `eval generate` を回すので書き込む**。
  「judge を呼ばないから安い」だけで、Firestore への副作用は T2 と同じ。
- 素直な対応は **`FIRESTORE_DATABASE=sre-agents-ci` で分ける**こと。`app/config.py:11` が
  既に env で上書き可能なので**コード変更は不要**。ワークフローの `env:` に 1 行足すだけ。
- 分けた場合、CI 側 database にも `make seed` が要る(`search_past_incidents` が空を返すと
  調査の根拠が無くなり、`past_incidents_consulted` 以外の品質も落ちる)。
- 後始末で済ませる案もあるが、**§8 の自己参照の問題は消えない**(実行中に書いたログを
  同じ実行の後続ケースが読む可能性が残る)。database 分離のほうが素直。

---

## 参照

- [`eval-operations.md`](./eval-operations.md) — eval 結果の読み方と CI ゲート
- [`trace-analysis-20260803-212433.md`](./trace-analysis-20260803-212433.md) — trace の中身とメトリクス検出力
- `app/firestore_client.py` / `app/config.py` — 接続の実装
- `scripts/seed_firestore.py` — モックデータ投入
