"""SRE障害対応ワークフロー(受付 → 起票 → 調査 → 復旧)のグラフ定義。

LLM に残す判断は「聞き取り」(reception_agent)と「原因調査」(investigation_agent)の
ノードだけで、起票・復旧実施・記録はグラフの関数ノードが決定的に行う。

HITL の2ゲートはグラフの構造そのもので担保する。RequestInput はワークフローを
実際に停止させるため、承認を経ずに restart_server / escalate_to_engineer へ
到達する経路がグラフ上に存在しない。加えて app/tools/ 側の state チェックも
そのまま残してある(defense-in-depth)。

  START → detect_intent → reception_agent → intake_router
    ├ ASK   → ask_reporter ⇢(停止・回答待ち)⇢ collect_answer → reception_agent
    └ READY → review_router
                ├ ASK          → ask_reporter
                ├ NEEDS_REVIEW → review_gate ⇢(停止)⇢ review_decision
                │                  ├ APPROVE → file_case
                │                  └ REVISE  → reception_agent
                └ PRE_APPROVED → file_case
  file_case
    ├ FAILED → halt
    └ FILED  → investigation_agent → plan_router
                 ├ NEEDS_APPROVAL → plan_gate ⇢(停止)⇢ approval_decision
                 │                    ├ APPROVED → exec_router
                 │                    └ DENIED   → halt
                 └ PRE_APPROVED   → exec_router
  exec_router
    ├ RESTART  → restart_node  ─ OK →  record_node【終端・唯一の出力】
    └ ESCALATE → escalate_node ─ OK →  record_node
                 (どちらも FAILED → halt)
"""

from google.adk import Workflow

from app.graph.nodes import (
    approval_decision,
    ask_reporter,
    collect_answer,
    detect_intent,
    escalate_node,
    exec_router,
    file_case,
    halt,
    intake_router,
    plan_gate,
    plan_router,
    record_node,
    restart_node,
    review_decision,
    review_gate,
    review_router,
)
from app.sub_agents.investigation import create_investigation_agent
from app.sub_agents.reception import create_reception_agent


def build_workflow() -> Workflow:
    """SRE障害対応ワークフローを構築する。

    Workflow の構築時にグラフ検証(到達不能ノード・無条件サイクル・
    スキーマ不整合など)が走るため、配線ミスはここで例外になる。
    """
    reception_agent = create_reception_agent()
    investigation_agent = create_investigation_agent()

    return Workflow(
        name="sre_incident_workflow",
        description=(
            "店舗からの障害報告の受付・起票から、原因調査・復旧対応までを"
            "人間の承認つきで実行するSRE障害対応ワークフロー。"
        ),
        edges=[
            # 受付: 初回報告の解析 → 聞き取り(不足があれば後退エッジで繰り返す)
            ("START", detect_intent, reception_agent, intake_router),
            (intake_router, {"ASK": ask_reporter, "READY": review_router}),
            (ask_reporter, collect_answer, reception_agent),
            # 起票前レビュー(HITL ゲート1)
            (
                review_router,
                {
                    "ASK": ask_reporter,
                    "NEEDS_REVIEW": review_gate,
                    "PRE_APPROVED": file_case,
                },
            ),
            (review_gate, review_decision),
            (review_decision, {"APPROVE": file_case, "REVISE": reception_agent}),
            # 起票 → 調査
            (file_case, {"FILED": investigation_agent, "FAILED": halt}),
            (investigation_agent, plan_router),
            # 復旧承認(HITL ゲート2)
            (plan_router, {"NEEDS_APPROVAL": plan_gate, "PRE_APPROVED": exec_router}),
            (plan_gate, approval_decision),
            (approval_decision, {"APPROVED": exec_router, "DENIED": halt}),
            # 承認後の実施と記録
            (exec_router, {"RESTART": restart_node, "ESCALATE": escalate_node}),
            (restart_node, {"OK": record_node, "FAILED": halt}),
            (escalate_node, {"OK": record_node, "FAILED": halt}),
        ],
    )
