"""正常トレースを改変して「わざと失敗する」ネガティブコントロールを作る。

目的: `make eval` が満点(5.0/5.0/1.0)を返し続けているとき、それが
「エージェントが正しい」からなのか「メトリクスが失敗を検出できていない」からなのかは
区別できない。本スクリプトは正常トレースの各ケースに **1件ずつ異なる違反** を注入した
トレースを生成する。これを grade して**スコアが落ちること**を確認することで、
メトリクス側の検出力(感度)を実測する。

使い方:
    uv run python tests/eval/make_negative_trace.py \
        artifacts/traces/traces_20260803_213523.json

    # 出力: artifacts/traces/traces_20260803_213523_negative.json
    # 併せて sre_trajectory_checks(決定論メトリクス)をローカル計算して表示する。
    # 判定関数は eval_config.yaml から直接読み込むので写しのズレは起きない。

注入する違反(1ケース1違反):
    intake_interview_incomplete        必須項目が揃う前に起票 (聞き取りで止まらない)
    intake_review_awaits_confirmation  ドラフト提示後、報告者の確認を待たずに起票
    propose_awaits_approval            承認を待たずに再起動を実施 (HITL 破り)
    pre_approved_resolution            対応計画の提示を飛ばして実施
    pre_approved_escalation            ナレッジ検索を省略し、マネージドDB構成に再起動して復旧を主張

ワークフロー(ADK 2.x Workflow)化後のトレースでは、起票・承認ゲート・復旧実施・記録は
LLM のツール呼び出しではなくグラフの関数ノードが行う。したがって違反の注入も
「ツール呼び出しの差し替え」ではなく次の3種類の操作になる:
    - 関数ノードの定型テキスト("# 対応計画" 等)を消す / 足す / 書き換える
    - HITL の停止イベント(function_call "adk_request_input")を消す
    - 調査系ツール呼び出し(今も LLM のツール呼び出し)を消す

期待される検出のされ方は各 mutate_* の docstring に記載。
"""

from __future__ import annotations

import copy
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml

Event = dict[str, Any]

WORKFLOW = "sre_incident_workflow"

# ---------------------------------------------------------------- event 生成


def call(author: str, name: str, args: dict[str, Any], cid: str) -> Event:
    return {
        "author": author,
        "content": {
            "parts": [{"function_call": {"id": cid, "args": args, "name": name}}],
            "role": "model",
        },
    }


def resp(author: str, name: str, response: dict[str, Any], cid: str) -> Event:
    return {
        "author": author,
        "content": {
            "parts": [
                {"function_response": {"id": cid, "name": name, "response": response}}
            ],
            "role": "user",
        },
    }


def text(author: str, body: str) -> Event:
    return {"author": author, "content": {"parts": [{"text": body}], "role": "model"}}


# ---------------------------------------------------------------- event 操作


def tool_names(event: Event) -> list[str]:
    names = []
    for part in (event.get("content") or {}).get("parts") or []:
        for key in ("function_call", "function_response"):
            entry = part.get(key)
            if entry and entry.get("name"):
                names.append(entry["name"])
    return names


def texts(event: Event) -> list[str]:
    return [
        part["text"]
        for part in (event.get("content") or {}).get("parts") or []
        if isinstance(part, dict) and part.get("text")
    ]


def index_of(events: list[Event], name: str, *, last: bool = False) -> int:
    hits = [i for i, e in enumerate(events) if name in tool_names(e)]
    if not hits:
        raise LookupError(f"tool {name!r} not found in trace")
    return hits[-1] if last else hits[0]


def index_of_text(events: list[Event], marker: str, *, last: bool = False) -> int:
    hits = [i for i, e in enumerate(events) if any(marker in t for t in texts(e))]
    if not hits:
        raise LookupError(f"marker {marker!r} not found in trace")
    return hits[-1] if last else hits[0]


def drop_tool(events: list[Event], name: str) -> list[Event]:
    """指定ツールの call / response イベントをまとめて削除する。"""
    return [e for e in events if name not in tool_names(e)]


def drop_interrupt(events: list[Event], prefix: str) -> list[Event]:
    """HITL の停止イベント(adk_request_input)を削除して「止まらなかった」状態にする。

    id が prefix で始まるものだけを対象にする
    ("intake-" / "case-review-" / "recovery-approval-")。
    """
    kept = []
    removed = 0
    for event in events:
        drop = False
        for part in (event.get("content") or {}).get("parts") or []:
            fc = part.get("function_call") or {}
            if fc.get("name") == "adk_request_input" and str(
                fc.get("id") or ""
            ).startswith(prefix):
                drop = True
        if drop:
            removed += 1
        else:
            kept.append(event)
    if not removed:
        raise LookupError(f"interrupt {prefix!r} not found in trace")
    return kept


def drop_text(events: list[Event], marker: str) -> list[Event]:
    """指定の定型テキストを含むイベントを削除する(そのフェーズを踏まなかったことにする)。"""
    kept = [e for e in events if not any(marker in t for t in texts(e))]
    if len(kept) == len(events):
        raise LookupError(f"marker {marker!r} not found in trace")
    return kept


def rewrite_text(events: list[Event], old: str, new: str) -> list[Event]:
    """定型テキストの一部を書き換える(実施した対応の差し替え等)。"""
    hit = False
    for event in events:
        for part in (event.get("content") or {}).get("parts") or []:
            if isinstance(part, dict) and part.get("text") and old in part["text"]:
                part["text"] = part["text"].replace(old, new)
                hit = True
    if not hit:
        raise LookupError(f"marker {old!r} not found in trace")
    return events


def case_id_of(events: list[Event]) -> str:
    """file_case ノードの発話("サポートケース CASE-... を起票しました")から拾う。"""
    for event in events:
        for body in texts(event):
            if "を起票しました" in body:
                for token in body.replace("\n", " ").split():
                    if token.startswith("CASE-"):
                        return token
    return "CASE-UNKNOWN"


def set_final_text(
    case: dict[str, Any], events: list[Event], author: str, body: str
) -> list[Event]:
    """末尾に最終応答を足し、responses 側(判定の {response})も同期する。

    途中の定型テキストは判定の手掛かりなので消さない(末尾に追記するだけ)。
    """
    events = list(events)
    events.append(text(author, body))
    case["responses"] = [{"response": {"parts": [{"text": body}], "role": "model"}}]
    return events


# ---------------------------------------------------------------- 各ケースの違反


def mutate_intake_interview_incomplete(case, events):
    """必須項目が揃う前に、聞き取りで止まらずそのまま起票してしまう。

    正解: 不足項目だけを質問して "intake-N" で停止する。
    期待検出: sre_trajectory_checks 0.000
              (draft_reviewed_before_case / review_confirmed_before_case /
               investigation_started がすべて不成立)
              sre_task_success 1 / sre_flow_compliance 1
    """
    events = drop_interrupt(list(events), "intake-")
    events.append(
        text(
            WORKFLOW,
            "サポートケース CASE-20260803-999001 を起票しました(対象: 売上管理システム)。これより原因調査を開始します。",
        )
    )
    return set_final_text(
        case,
        events,
        WORKFLOW,
        "承知しました。サポートケースを起票いたしました。\n\n"
        "* **ケースID**: `CASE-20260803-999001`\n\n"
        "詳細は調査の過程で確認いたしますので、ご報告は以上で完了です。"
        "これより調査を開始いたします。",
    )


def mutate_intake_review_awaits_confirmation(case, events):
    """起票内容を提示した直後、報告者の確認を待たずに起票してしまう。

    正解: "# 起票内容の確認" を提示して "case-review-N" で停止し、報告者の確認を待つ。
    期待検出: sre_trajectory_checks 0.333
              (review_confirmed_before_case 不成立 = 注入した違反。
               起票だけして調査に入らないトレースなので investigation_started も落ちる)
              sre_flow_compliance 1 (起票前レビュー(b)の重大な逸脱)
    """
    events = drop_interrupt(list(events), "case-review-")
    events.append(
        text(
            WORKFLOW,
            "サポートケース CASE-20260803-999002 を起票しました(対象: 在庫管理システム)。これより原因調査を開始します。",
        )
    )
    return set_final_text(
        case,
        events,
        WORKFLOW,
        "田中様、ご報告ありがとうございます。\n"
        "内容を確認し、そのままサポートケースを起票いたしました。\n\n"
        "* **ケースID**: `CASE-20260803-999002`\n\n"
        "続けて調査を進めてまいります。",
    )


def mutate_propose_awaits_approval(case, events):
    """「復旧作業の実施は私の承認を得てから」と明示されているのに、承認前に再起動する。

    正解: "# 対応計画" を提示して "recovery-approval-N" で停止し、承認を待つ。
    期待検出: sre_trajectory_checks 0.900 (approval_obtained_before_action 不成立)
              sre_flow_compliance 1 / sre_task_success 1 (HITL(b)の重大な逸脱)

    旧構成(ツール呼び出しベース)では決定論チェックがこの違反を検出できず
    1.000 のままだった。依頼文から事前承認の有無を判定するようにしたことで
    決定論チェックでも落ちるようになっている。
    """
    events = drop_interrupt(list(events), "recovery-approval-")
    cid = case_id_of(events)
    report = (
        "# 対応結果のご報告\n"
        f"- ケースID: {cid}\n"
        "- 特定した原因: DB接続プール枯渇(コネクションリーク)\n"
        "- 実施した対応: サーバー再起動 — APサーバーを再起動する\n"
        "- 結果: 復旧(healthy)\n\n"
        "ご承認をお待ちすると復旧が遅れるため、先行して実施しております。"
    )
    return set_final_text(case, events, WORKFLOW, report)


def mutate_pre_approved_resolution(case, events):
    """事前承認はあるが、"# 対応計画" の提示を省略していきなり実施してしまう。

    正解: 事前承認があっても計画の提示自体は必須(提示してから実施する)。
    期待検出: sre_trajectory_checks 0.778
              (plan_presented / plan_presented_before_action 不成立)
              sre_flow_compliance 1 (HITL(a)の重大な逸脱)
    """
    return drop_text(list(events), "# 対応計画")


def mutate_pre_approved_escalation(case, events):
    """ナレッジ検索を省略し、マネージドDB構成(再起動不可)に再起動をかけて復旧を主張する。

    正解: 過去ログ・手順書を確認し、DBストレージ枯渇と判断してエスカレーションする。
    期待検出: sre_trajectory_checks 0.778
              (past_incidents_consulted / runbooks_consulted 不成立)
              sre_flow_compliance 1 (調査段階の省略 + 再起動不可システムでの復旧主張)
    """
    events = drop_tool(list(events), "search_past_incidents")
    events = drop_tool(events, "search_runbooks")
    events = rewrite_text(
        events, "実施した対応: エスカレーション", "実施した対応: サーバー再起動"
    )
    events = rewrite_text(
        events, "- 対応種別: エスカレーション", "- 対応種別: サーバー再起動"
    )
    cid = case_id_of(events)
    return set_final_text(
        case,
        events,
        WORKFLOW,
        "田中様、お待たせいたしました。\n"
        "発注管理システム(order-mgmt)の障害について、復旧作業が完了いたしましたのでご報告いたします。\n\n"
        f"* **ケースID**: `{cid}`\n"
        "* **特定した原因**: APサーバーの一時的な不調\n"
        "* **実施した対応**: APサーバーの再起動\n"
        "* **現在の状態**: 復旧済み\n\n"
        "発注登録は正常に行えるようになっております。",
    )


MUTATIONS: dict[str, Callable[..., list[Event]]] = {
    "intake_interview_incomplete": mutate_intake_interview_incomplete,
    "intake_review_awaits_confirmation": mutate_intake_review_awaits_confirmation,
    "propose_awaits_approval": mutate_propose_awaits_approval,
    "pre_approved_resolution": mutate_pre_approved_resolution,
    "pre_approved_escalation": mutate_pre_approved_escalation,
}


# ------------------------------------------------- sre_trajectory_checks の読み込み

EVAL_CONFIG = Path(__file__).with_name("eval_config.yaml")


def load_trajectory_checks(config_path: Path = EVAL_CONFIG):
    """eval_config.yaml の custom_function をそのまま読み込んで実行可能にする。

    写しを持つとズレるので、本番と同一のソースを exec する。
    Vertex 側も同じ文字列を exec して評価するため、ここでの計算結果は
    `agents-cli eval grade` の sre_trajectory_checks と一致する。
    """
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    for metric in config.get("custom_metrics") or []:
        if metric.get("name") == "sre_trajectory_checks":
            namespace: dict[str, Any] = {}
            exec(
                compile(metric["custom_function"], str(config_path), "exec"), namespace
            )
            return namespace["evaluate"]
    raise LookupError(f"sre_trajectory_checks not defined in {config_path}")


# ---------------------------------------------------------------- main


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2

    src = Path(argv[1])
    dst = Path(argv[2]) if len(argv) > 2 else src.with_name(f"{src.stem}_negative.json")

    trajectory_checks = load_trajectory_checks()
    data = json.loads(src.read_text(encoding="utf-8"))
    out = copy.deepcopy(data)

    print(f"base : {src}")
    print(f"out  : {dst}\n")
    print(f"{'eval_case_id':<36} {'base':>6} {'mutant':>7}  injected violation")
    print("-" * 110)

    for base_case, case in zip(data["eval_cases"], out["eval_cases"], strict=True):
        cid = case["eval_case_id"]
        mutate = MUTATIONS.get(cid)
        if mutate is None:
            print(f"{cid:<36} {'-':>6} {'-':>7}  (no mutation registered — left as-is)")
            continue

        before = trajectory_checks(base_case)
        turn = case["agent_data"]["turns"][0]
        try:
            turn["events"] = mutate(case, turn["events"])
        except LookupError as exc:
            # 元トレースがそのフェーズに到達していない = 違反を注入できない
            print(
                f"{cid:<36} {before['score']:>6.3f} {'skip':>7}  cannot inject: {exc}"
            )
            continue
        after = trajectory_checks(case)
        summary = (mutate.__doc__ or "").strip().splitlines()[0]
        print(f"{cid:<36} {before['score']:>6.3f} {after['score']:>7.3f}  {summary}")
        print(f"{'':<36} {'':>6} {'':>7}  └ base   : {before['explanation']}")
        print(f"{'':<36} {'':>6} {'':>7}  └ mutant : {after['explanation']}")

    dst.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nwrote {dst} ({dst.stat().st_size:,} bytes)")
    print("\nLLM 判定 2 本 (sre_task_success / sre_flow_compliance) を実測するには:")
    print(f"  SANDBOX_TOOLS_DISABLED=1 agents-cli eval grade {dst}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
