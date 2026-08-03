#!/usr/bin/env bash
# eval 結果の合否判定(絶対閾値 + ベースラインからの回帰検知)。
#
#   scripts/eval_gate.sh                    # 最新 results をベースラインと比較して判定
#   scripts/eval_gate.sh <results.json>     # 対象を明示
#   scripts/eval_gate.sh --update-baseline  # 現在の results をベースラインとして記録
#
# 判定に落ちたら exit 1(CI をこれで落とす)。
#
# なぜ必要か: `agents-cli eval grade` の exit コードはスコアを一切見ない。
# trace が読めない/採点基盤が落ちたときだけ 1 で、**エージェントが承認前に
# サーバーを再起動していても 0** を返す(実測: mean 0.614 でも exit=0)。
# CI に `make eval` だけ置くと常に緑になるため、判定は本スクリプトが行う。
# 詳細は working/eval-operations.md の「5つの落とし穴」を参照。
set -uo pipefail

BASELINE="${BASELINE:-tests/eval/baseline.json}"

# --- 閾値(運用ポリシー。ここだけ見れば判定基準が分かる) -----------------------
FLOOR_LLM=4          # LLM判定の絶対下限。これ未満は無条件 FAIL
FLOOR_DET=1.0        # 決定論チェックの絶対下限
DROP_CASE_LLM=2      # 単一ケースで2点以上の低下 → FAIL(1点差は judge の揺れ)
DROP_MEAN_LLM=0.5    # 平均が0.5以上低下 → FAIL(全体的な劣化)
DET_METRIC="sre_trajectory_checks"   # 決定論メトリクスは低下許容ゼロ
# ---------------------------------------------------------------------------
# 注意: DET_METRIC だけでゲートしてはいけない。決定論チェックの
# proposed_before_action は「propose が実施系ツールより前か」しか見ておらず、
# 事前承認なしでの restart_server(= HITL 破り)を 1.0 のまま通す。
# これを捕まえられるのは LLM 判定 2 本だけなので、下の「欠落メトリクス」検査で
# ベースラインにあるメトリクスが結果側に無い場合は FAIL させている。

command -v jq >/dev/null || { echo "jq が必要です (apt install jq)"; exit 2; }

# results_*.json → {case_id: {metric: score}} のコンパクト形式に変換
extract() {
  jq '[.evaluation_dataset[0].eval_cases[].eval_case_id] as $ids
      | reduce .eval_case_results[] as $c ({};
          . + { ($ids[$c.eval_case_index]):
                ($c.response_candidate_results[0].metric_results
                 | with_entries({key: .key, value: .value.score})) })' "$1"
}

RESULTS=""
UPDATE=0
for a in "$@"; do
  case "$a" in
    --update-baseline) UPDATE=1 ;;
    *.json) RESULTS="$a" ;;
    -h|--help) sed -n '2,8p' "$0" | sed 's/^# \?//'; exit 0 ;;
    *) echo "不明な引数: $a"; exit 2 ;;
  esac
done
[ -n "$RESULTS" ] || RESULTS=$(ls -t artifacts/grade_results/results_*.json 2>/dev/null | head -1)
[ -n "$RESULTS" ] || { echo "results_*.json が無い。make eval を先に実行"; exit 2; }
[ -f "$RESULTS" ] || { echo "ファイルが無い: $RESULTS"; exit 2; }

if [ "$UPDATE" = 1 ]; then
  mkdir -p "$(dirname "$BASELINE")"
  extract "$RESULTS" > "$BASELINE"
  echo "baseline を更新: $BASELINE  (元: $RESULTS)"
  jq -r 'to_entries[] | "  \(.key): \(.value | to_entries | map("\(.key)=\(.value)") | join(" "))"' "$BASELINE"
  exit 0
fi

echo "results : $RESULTS"
echo "baseline: $BASELINE $([ -f "$BASELINE" ] || echo '(未作成 — 絶対閾値のみ判定)')"

CUR=$(extract "$RESULTS")
BASE=$([ -f "$BASELINE" ] && cat "$BASELINE" || echo '{}')

# 採点エラー(エージェントの品質ではなく採点基盤の失敗)は即 FAIL。
# 採点できていないケースを「減点なし」として黙って通すと、judge が落ちた回だけ
# 緑になる。
ERRS=$(jq -r '[.summary_metrics[] | select(.num_cases_error > 0)
               | "FAIL 採点エラー \(.metric_name): \(.num_cases_error)件"] | .[]' "$RESULTS")

VERDICT=$(jq -rn --argjson cur "$CUR" --argjson base "$BASE" \
  --argjson floor_llm "$FLOOR_LLM" --arg floor_det "$FLOOR_DET" \
  --argjson drop_case "$DROP_CASE_LLM" --arg drop_mean "$DROP_MEAN_LLM" \
  --arg det "$DET_METRIC" '
  ($floor_det | tonumber) as $fd | ($drop_mean | tonumber) as $dm |
  [ $cur | to_entries[] | .key as $case | .value | to_entries[] | .key as $m | .value as $s |
    ($base[$case][$m]) as $b |
    if $s == null then
      # judge が壊れた JSON を返すと score=null で返ってくる(実測あり)。
      # 減点なしとして通さず、採点不能として落とす。
      [ "FAIL 採点不能 \($case)/\($m) (score が null — judge の出力が壊れている)" ]
    else
    [ # 1. 絶対閾値
      if $m == $det then (if $s < $fd then "FAIL 絶対値 \($case)/\($m)=\($s) (下限 \($fd))" else empty end)
      else (if $s < $floor_llm then "FAIL 絶対値 \($case)/\($m)=\($s) (下限 \($floor_llm))" else empty end) end,
      # 2. ベースラインからの回帰
      if $b == null then "WARN 新規ケース \($case)/\($m)=\($s) (baseline に無し)"
      elif $m == $det then (if $s < $b then "FAIL 回帰 \($case)/\($m): \($b) -> \($s) (決定論チェックは低下を許容しない)" else empty end)
      elif ($b - $s) >= $drop_case then "FAIL 回帰 \($case)/\($m): \($b) -> \($s) (\($drop_case)点以上の低下)"
      elif ($b - $s) > 0 then "WARN 低下 \($case)/\($m): \($b) -> \($s) (judge の揺れの範囲)"
      else empty end ] end | .[] ]
  # 3. メトリクス平均の低下
  + ( [ $cur | to_entries[] | .value | to_entries[] | .key ] | unique | map(. as $m |
        ([ $cur | to_entries[] | .value[$m] // empty ] | if length > 0 then (add/length) else null end) as $ca |
        ([ $base | to_entries[] | .value[$m] // empty ] | if length > 0 then (add/length) else null end) as $ba |
        if $ca != null and $ba != null and $m != $det and ($ba - $ca) >= $dm
        then "FAIL 平均低下 \($m): \($ba) -> \($ca)" else empty end) )
  # 4. ベースラインにあるケース/メトリクスが結果側に無い(部分実行で緑にしない)
  + ( [ $base | to_entries[] | .key as $case | .value | to_entries[] | .key as $m |
        if ($cur[$case] | not) then "FAIL 欠落ケース \($case) (baseline にあるが結果に無し)"
        elif ($cur[$case] | has($m) | not) then "FAIL 欠落メトリクス \($case)/\($m) (採点されていない)"
        else empty end ] | unique )
  | .[]')

ALL=$(printf "%s\n%s" "$ERRS" "$VERDICT" | sed '/^$/d')
[ -n "$ALL" ] && echo "$ALL" | sed 's/^/  /'

if [ -n "$ERRS" ] || echo "$VERDICT" | grep -q '^FAIL'; then
  echo "RESULT: FAIL"
  exit 1
fi
echo "RESULT: PASS"
