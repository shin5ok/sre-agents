#!/usr/bin/env bash
# GCE障害シナリオ(store-web-01/02/03 / e2-micro + Ops Agent + BigQueryエクスポート)の
# 作成・障害注入・復旧・削除。scripts/gcp_incident.sh (Cloud Run版)と対になる、
# インフラ層(VMのメモリリーク)の障害シナリオ。
#
# 対象プロジェクトは環境変数 SANDBOX_PROJECT_ID から取得する(必須。gcp_incident.sh と共通)。
# リージョンは SANDBOX_REGION(既定: asia-northeast1)、ゾーンは SANDBOX_ZONE
# (既定: ${SANDBOX_REGION}-a)。
set -euo pipefail

usage() {
  cat <<'EOF'
usage: SANDBOX_PROJECT_ID=<project> scripts/gce_incident.sh <command>

commands:
  setup    API(compute)有効化、ファイアウォール(HTTP)、default Compute SA
           へのロール付与(logging.logWriter / monitoring.metricWriter)、
           BigQueryデータセット、Cloud Logging→BigQueryのエクスポートsink
           (と、そのwriter identityへのデータセットアクセス権)を作成する
  deploy   store-web-01/02/03(e2-micro)を作成する。3台ともnginx+Ops Agent。
           store-web-02 だけメモリリーク/擬似障害ログの仕組みを設置する
           (この時点ではまだ起動しない)
  break    store-web-02 で障害を注入する(メモリリークプロセスと擬似障害
           ログ生成を起動する)
  status   3台の起動状態とWeb応答、store-web-02の障害プロセス状態・
           メモリ使用量を表示する
  resolve  store-web-02 を再起動して復旧する(リーク解消。障害プロセスは
           自動再開しない設計なので、再度試すには break を呼ぶこと)
  clean    3台のインスタンスを削除する(ファイアウォール/BigQuery/sinkは
           残す。作り直す際に setup の再実行が不要になるため)
  create   setup + deploy + break を一括実行(デモ準備)
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
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
STARTUP_DIR="$SCRIPT_DIR/gce_incident_startup"

DATASET="sre_agents_demo_logs"
SINK="sre-agents-demo-sink"
FIREWALL="allow-http-demo"
INSTANCES=(store-web-01 store-web-02 store-web-03)
INCIDENT_INSTANCE="store-web-02"

gce() { gcloud compute "$@" --project "$SANDBOX_PROJECT_ID"; }

instance_exists() { gce instances describe "$1" --zone "$ZONE" &>/dev/null; }

instance_role() {
  [[ "$1" == "$INCIDENT_INSTANCE" ]] && echo incident || echo web
}

instance_startup_script() {
  [[ "$1" == "$INCIDENT_INSTANCE" ]] && echo "$STARTUP_DIR/incident.sh" || echo "$STARTUP_DIR/base.sh"
}

web_check() {
  local name ip code
  for name in "${INSTANCES[@]}"; do
    ip="$(gce instances describe "$name" --zone "$ZONE" \
      --format='value(networkInterfaces[0].accessConfigs[0].natIP)' 2>/dev/null || true)"
    if [[ -z "$ip" ]]; then
      echo "$name: (見つかりません)"
      continue
    fi
    code="$(curl -s -o /dev/null -m 5 -w '%{http_code}' "http://$ip/" || echo 000)"
    echo "$name: http://$ip/ -> $code"
  done
}

grant_bq_dataset_access() {
  local writer="$1"
  python3 - "$SANDBOX_PROJECT_ID" "$DATASET" "$writer" <<'PYEOF'
import json
import subprocess
import sys
import tempfile
import os

project, dataset, writer = sys.argv[1], sys.argv[2], sys.argv[3]
ref = f"{project}:{dataset}"
current = json.loads(subprocess.check_output(["bq", "show", "--format=prettyjson", ref]))
access = current.get("access", [])
if any(e.get("iamMember") == writer for e in access):
    print(f"{writer} はすでに {ref} へのアクセス権を持っています(スキップ)")
else:
    access.append({"role": "roles/bigquery.dataEditor", "iamMember": writer})
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump({"access": access}, f)
        path = f.name
    try:
        subprocess.run(["bq", "update", "--source", path, ref], check=True)
    finally:
        os.unlink(path)
    print(f"{writer} に {ref} への bigquery.dataEditor を付与しました")
PYEOF
}

case "$cmd" in
  setup)
    gcloud services enable compute.googleapis.com --project "$SANDBOX_PROJECT_ID"

    gce firewall-rules describe "$FIREWALL" &>/dev/null || \
      gce firewall-rules create "$FIREWALL" \
        --network=default --direction=INGRESS --action=ALLOW \
        --rules=tcp:80 --source-ranges=0.0.0.0/0 --target-tags=http-server

    project_number="$(gcloud projects describe "$SANDBOX_PROJECT_ID" --format='value(projectNumber)')"
    compute_sa="${project_number}-compute@developer.gserviceaccount.com"
    for role in roles/logging.logWriter roles/monitoring.metricWriter; do
      echo "granting $role to $compute_sa ..."
      gcloud projects add-iam-policy-binding "$SANDBOX_PROJECT_ID" \
        --member="serviceAccount:$compute_sa" --role="$role" --condition=None >/dev/null
    done

    bq show --project_id="$SANDBOX_PROJECT_ID" "$DATASET" &>/dev/null || \
      bq mk --project_id="$SANDBOX_PROJECT_ID" --dataset --location="$REGION" \
        --description="SRE agent demo: VM logs exported from Cloud Logging" \
        "$DATASET"

    gcloud logging sinks describe "$SINK" --project "$SANDBOX_PROJECT_ID" &>/dev/null || \
      gcloud logging sinks create "$SINK" \
        "bigquery.googleapis.com/projects/$SANDBOX_PROJECT_ID/datasets/$DATASET" \
        --project "$SANDBOX_PROJECT_ID" \
        --log-filter='resource.type="gce_instance"' \
        --use-partitioned-tables

    writer="$(gcloud logging sinks describe "$SINK" --project "$SANDBOX_PROJECT_ID" --format='value(writerIdentity)')"
    grant_bq_dataset_access "$writer"
    echo "setup完了: $SANDBOX_PROJECT_ID"
    ;;

  deploy)
    for name in "${INSTANCES[@]}"; do
      if instance_exists "$name"; then
        echo "$name はすでに存在します(スキップ)"
        continue
      fi
      echo "creating $name ..."
      gce instances create "$name" \
        --zone "$ZONE" --machine-type=e2-micro \
        --image-family=debian-12 --image-project=debian-cloud \
        --boot-disk-size=10GB --network=default --subnet=default \
        --tags=http-server \
        --labels="purpose=sre-agent-demo,role=$(instance_role "$name")" \
        --metadata-from-file="startup-script=$(instance_startup_script "$name")"
    done
    echo "起動スクリプト実行の完了まで1〜2分待ってから status で確認してください。"
    ;;

  break)
    gce ssh "$INCIDENT_INSTANCE" --zone "$ZONE" --tunnel-through-iap \
      --command="sudo systemctl start memleak.service incident-logsim.service"
    echo "$INCIDENT_INSTANCE で障害を注入しました(メモリリーク+擬似障害ログを開始)。"
    ;;

  status)
    web_check
    echo
    gce ssh "$INCIDENT_INSTANCE" --zone "$ZONE" --tunnel-through-iap \
      --command="echo -n 'memleak: '; systemctl is-active memleak.service 2>&1; echo -n 'incident-logsim: '; systemctl is-active incident-logsim.service 2>&1; free -h | grep Mem" 2>/dev/null || true
    ;;

  resolve)
    gce instances reset "$INCIDENT_INSTANCE" --zone "$ZONE"
    echo "$INCIDENT_INSTANCE を再起動しました。障害プロセスは自動再開しません(復旧)。再度試すには break を実行してください。"
    ;;

  clean)
    for name in "${INSTANCES[@]}"; do
      gce instances delete "$name" --zone "$ZONE" --quiet 2>/dev/null || echo "$name: 削除済みまたは存在しません"
    done
    ;;

  create)
    "$0" setup
    "$0" deploy
    echo "起動スクリプトの完了を待っています(90秒)..."
    sleep 90
    "$0" break
    ;;

  *)
    usage
    exit 1
    ;;
esac
