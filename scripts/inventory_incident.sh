#!/usr/bin/env bash
# 在庫管理システム(inventory-mgmt)のAPサーバー役 GCEインスタンスの
# 作成・状態確認・手動リセット・削除。
#
# store-web-fleet(scripts/gce_incident.sh)と違い、障害の筋書き自体は
# モック(app/mock_world.py の Java heap OOM ログと data/runbooks/inventory-mgmt.md)
# で完結する。このインスタンスは restart_server の実リセット対象としてだけ存在する
# (調査はモック、復旧操作のみ実GCPに反映)ため、break(障害注入)は無い。
#
# 対象プロジェクトは環境変数 SANDBOX_PROJECT_ID から取得する(必須。他シナリオと共通)。
# ゾーンは SANDBOX_ZONE(既定: ${SANDBOX_REGION:-asia-northeast1}-a)。
set -euo pipefail

usage() {
  cat <<'EOF'
usage: SANDBOX_PROJECT_ID=<project> scripts/inventory_incident.sh <command>

commands:
  create   inventory-mgmt(e2-micro)を作成する(存在すればスキップ)。
           障害注入はない: 在庫管理の障害シナリオはモックログで完結し、
           このインスタンスは restart_server の実リセット対象としてだけ使う
  status   インスタンスの稼働状態と直近の操作履歴(reset の実行記録)を表示する
  resolve  inventory-mgmt を手動でリセットする(エージェントの restart_server と同等)
  clean    インスタンスを削除する
EOF
}

cmd="${1:-}"
if [[ -z "$cmd" || "$cmd" == "-h" || "$cmd" == "--help" ]]; then
  usage
  exit 1
fi

: "${SANDBOX_PROJECT_ID:?環境変数 SANDBOX_PROJECT_ID を設定してください(GCEシナリオ用サンドボックスプロジェクトのID)}"
REGION="${SANDBOX_REGION:-asia-northeast1}"
ZONE="${SANDBOX_ZONE:-${REGION}-a}"
INSTANCE="inventory-mgmt"

gce() { gcloud compute "$@" --project "$SANDBOX_PROJECT_ID"; }

case "$cmd" in
  create)
    gcloud services enable compute.googleapis.com --project "$SANDBOX_PROJECT_ID"
    if gce instances describe "$INSTANCE" --zone "$ZONE" &>/dev/null; then
      echo "$INSTANCE はすでに存在します(スキップ)"
    else
      echo "creating $INSTANCE ..."
      gce instances create "$INSTANCE" \
        --zone "$ZONE" --machine-type=e2-micro \
        --image-family=debian-12 --image-project=debian-cloud \
        --boot-disk-size=10GB --network=default --subnet=default \
        --labels="purpose=sre-agent-demo,role=inventory-ap"
    fi
    echo "create完了: restart_server(\"inventory-mgmt\") がこのインスタンスを実リセットします。"
    ;;

  status)
    gce instances describe "$INSTANCE" --zone "$ZONE" --format="table(name,status)"
    echo
    # lastStartTimestamp は reset では更新されないため、実リセットの確認は操作履歴で行う
    echo "直近の操作(restart_server の実リセットは operationType=reset で記録される):"
    gce operations list --zones "$ZONE" --filter="targetLink~instances/$INSTANCE\$" \
      --sort-by=~insertTime --limit=5 \
      --format="table(operationType,insertTime,status,user)"
    ;;

  resolve)
    gce instances reset "$INSTANCE" --zone "$ZONE"
    echo "$INSTANCE をリセットしました(エージェントの restart_server と同等の操作)。"
    ;;

  clean)
    gce instances delete "$INSTANCE" --zone "$ZONE" --quiet 2>/dev/null || \
      echo "$INSTANCE: 削除済みまたは存在しません"
    ;;

  *)
    usage
    exit 1
    ;;
esac
