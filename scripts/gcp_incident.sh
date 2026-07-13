#!/usr/bin/env bash
# GCP障害シナリオ(store-app / Cloud Run)の作成・障害注入・復旧・削除。
#
# 対象プロジェクトは環境変数 SANDBOX_PROJECT_ID から取得する(必須)。
# エージェント本体の実行プロジェクト(GOOGLE_CLOUD_PROJECT)とは独立した
# サンドボックスプロジェクトを想定。リージョンは SANDBOX_REGION(既定: asia-northeast1)。
set -euo pipefail

usage() {
  cat <<'EOF'
usage: SANDBOX_PROJECT_ID=<project> scripts/gcp_incident.sh <command>

commands:
  setup    必要な API(run / cloudbuild / artifactregistry / logging /
           geminicloudassist)を有効化し、
           1) 現在の gcloud アカウントに Cloud Assist MCP 用ロール
              (mcp.toolUser / geminicloudassist.user)を付与
           2) デフォルトのコンピュート SA に Cloud Build 用ロール
              (cloudbuild.builds.builder)を付与 — 新規プロジェクトで
              gcloud run deploy --source が失敗するのを防ぐため
  deploy   store-app を正常状態(FAIL_MODE=none)でデプロイする(--source ビルド、
           認証必須のまま。検証リクエストは ID トークンで自動フォールバック)
  expose   任意: サービスを未認証公開にする(必要な場合のみ明示実行)
  break    障害を注入する(FAIL_MODE=db_timeout)。エラーログ発生用に
           リクエストも送信する
  status   サービスの環境変数と直近リクエストの結果を表示する
  resolve  障害を復旧する(FAIL_MODE=none に戻す)
  clean    store-app サービスを削除する
  create   setup + deploy + break を一括実行(デモ準備)
EOF
}

cmd="${1:-}"
if [[ -z "$cmd" || "$cmd" == "-h" || "$cmd" == "--help" ]]; then
  usage
  exit 1
fi

: "${SANDBOX_PROJECT_ID:?環境変数 SANDBOX_PROJECT_ID を設定してください(GCP障害シナリオ用サンドボックスプロジェクトのID)}"
REGION="${SANDBOX_REGION:-asia-northeast1}"
SERVICE="store-app"
APP_DIR="$(cd "$(dirname "$0")" && pwd)/gcp_incident_app"

run_cmd() { gcloud run "$@" --project "$SANDBOX_PROJECT_ID" --region "$REGION"; }

service_url() { run_cmd services describe "$SERVICE" --format='value(status.url)'; }

hit() { # $1: リクエスト回数(障害ログの発生・回復確認に使う)
  local url first
  local -a auth=()
  url="$(service_url)"
  first="$(curl -s -o /dev/null -w '%{http_code}' "$url/" || true)"
  if [[ "$first" == "401" || "$first" == "403" ]]; then
    # 組織ポリシーで未認証アクセスが禁止されている環境向けの
    # フォールバック: gcloud の ID トークンで認証付きアクセスする
    echo "(unauthenticated access blocked -> using identity token)"
    auth=(-H "Authorization: Bearer $(gcloud auth print-identity-token)")
  fi
  echo "GET $url/ x$1:"
  for _ in $(seq "$1"); do
    curl -s -o /dev/null -w '%{http_code} ' "${auth[@]}" "$url/" || true
  done
  echo
}

case "$cmd" in
  setup)
    gcloud services enable \
      run.googleapis.com \
      cloudbuild.googleapis.com \
      artifactregistry.googleapis.com \
      logging.googleapis.com \
      geminicloudassist.googleapis.com \
      --project "$SANDBOX_PROJECT_ID"
    echo "APIs enabled on $SANDBOX_PROJECT_ID"
    # Cloud Assist MCP の呼び出しには roles/owner でも足りない(プレビューの
    # mcp.tools.call 権限は基本ロールに含まれない)ため、明示的にロールを付与する
    account="$(gcloud config get-value account 2>/dev/null)"
    if [[ -n "$account" ]]; then
      member="user:$account"
      [[ "$account" == *gserviceaccount.com ]] && member="serviceAccount:$account"
      for role in roles/mcp.toolUser roles/geminicloudassist.user; do
        echo "granting $role to $member ..."
        gcloud projects add-iam-policy-binding "$SANDBOX_PROJECT_ID" \
          --member="$member" --role="$role" --condition=None >/dev/null
      done
    fi
    # 新規プロジェクトで gcloud run deploy --source が失敗する典型原因への対処:
    # デフォルトのコンピュート SA (Cloud Build のデフォルトビルダー) に
    # ソースGCS読取・AR書込・ログ書込を含む builds.builder ロールを付与する。
    project_number="$(gcloud projects describe "$SANDBOX_PROJECT_ID" --format='value(projectNumber)')"
    compute_sa="${project_number}-compute@developer.gserviceaccount.com"
    echo "granting roles/cloudbuild.builds.builder to $compute_sa ..."
    gcloud projects add-iam-policy-binding "$SANDBOX_PROJECT_ID" \
      --member="serviceAccount:$compute_sa" \
      --role="roles/cloudbuild.builds.builder" --condition=None >/dev/null
    ;;
  deploy)
    # 既定は認証必須のままデプロイする(hit は 403 時に ID トークンへ
    # 自動フォールバックする)。公開したい場合のみ expose を明示実行すること。
    run_cmd deploy "$SERVICE" --source "$APP_DIR" \
      --no-allow-unauthenticated --set-env-vars FAIL_MODE=none --quiet
    hit 5
    ;;
  expose)
    # 任意: サービスを未認証公開にする(組織ポリシーで拒否される場合あり)。
    # 公開はインターネット全体への露出を意味するため、必要な場合のみ実行すること。
    run_cmd services add-iam-policy-binding "$SERVICE" \
      --member=allUsers --role=roles/run.invoker
    ;;
  break)
    run_cmd services update "$SERVICE" --update-env-vars FAIL_MODE=db_timeout --quiet
    hit 15
    echo "障害を注入しました(FAIL_MODE=db_timeout)。Cloud Logging に ERROR が出ています。"
    ;;
  status)
    run_cmd services describe "$SERVICE" \
      --format='value(status.url, spec.template.spec.containers[0].env)'
    hit 3
    ;;
  resolve)
    run_cmd services update "$SERVICE" --update-env-vars FAIL_MODE=none --quiet
    hit 5
    echo "復旧しました(FAIL_MODE=none)。"
    ;;
  clean)
    run_cmd services delete "$SERVICE" --quiet
    ;;
  create)
    "$0" setup
    "$0" deploy
    "$0" break
    ;;
  *)
    usage
    exit 1
    ;;
esac
