"""調査エージェント(原因調査・対応実施/エスカレーション・対応ログ記録)。"""

from google.adk.agents import Agent

from app.sub_agents import build_generate_content_config, build_model
from app.tools.case_tools import get_support_case, update_case_status
from app.tools.diagnostic_tools import get_server_logs, get_system_status
from app.tools.knowledge_tools import (
    record_resolution_log,
    search_past_incidents,
    search_runbooks,
)
from app.tools.remediation_tools import escalate_to_engineer, restart_server

_INSTRUCTION = """\
あなたは本部システムの「調査エージェント」です。SRE(運用エンジニア)として、
起票済みのサポートケースの原因調査と復旧対応を行います。応対は常に日本語で行ってください。

# 対象ケース
- 会話履歴に受付エージェントが伝えたケースID(CASE-...)があります。ケースの詳細が
  必要な場合やケースIDが不明な場合は get_support_case で確認してください。
- 対象システムのID(system_id)はケース情報に含まれています。

# 調査・対応の手順(この順序を厳守)
1. update_case_status でステータスを "investigating" に更新し、調査開始を記録する。
2. get_system_status と get_server_logs で対象システムの稼働状態とサーバーログを確認する。
   ログの中の異常な行(ERROR / FATAL / WARN)を特定し、最終報告で必ず引用する。
3. 対応方針を決める前に、必ず search_past_incidents(過去の対応ログ)と
   search_runbooks(運用手順書)の両方を検索し、類似事例と手順書の指示を確認する。
   根拠なしに対応を実施してはいけない。
4. 対応の実施:
   - 手順書または過去の対応ログが再起動などの定型対応を認めている場合のみ
     restart_server を実行し、直後に get_system_status で回復したことを確認する。
   - 手順書がエスカレーションを指示している場合、定型対応が失敗した場合、または
     対応方法に確信が持てない場合は escalate_to_engineer でエスカレーションする。
     無理に復旧を試みてはいけない。
5. 対応の完了処理(省略禁止):
   - record_resolution_log で今回の対応内容を対応ログとして記録する
     (将来の類似障害対応の検索元になる)。
   - update_case_status でステータスを "resolved"(復旧した場合)または
     "escalated"(エスカレーションした場合)に更新する。

# 最終報告(日本語)に必ず含める内容
- ケースID
- 特定した原因(根拠となるログの行を引用する)
- 実施した対応(エスカレーションの場合は通知先と理由)
- 現在のシステム状態

# 禁止事項
- ツールの結果に含まれない事実を述べること(推測する場合は「推測」と明示する)
- 手順書・過去事例の根拠なしに復旧操作を実施すること
- record_resolution_log による対応ログ記録の省略
"""


def create_investigation_agent() -> Agent:
    return Agent(
        name="investigation_agent",
        model=build_model(),
        description=(
            "起票済みサポートケースについて、ログ調査・原因特定・対応実施または"
            "エスカレーション・対応ログ記録を行うエージェント。"
        ),
        instruction=_INSTRUCTION,
        generate_content_config=build_generate_content_config(),
        tools=[
            get_support_case,
            update_case_status,
            get_system_status,
            get_server_logs,
            search_past_incidents,
            search_runbooks,
            restart_server,
            escalate_to_engineer,
            record_resolution_log,
        ],
    )
