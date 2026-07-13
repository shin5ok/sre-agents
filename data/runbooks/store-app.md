# 店舗アプリAPI 運用手順書

## システム概要
店舗スタッフ向けアプリ・モバイルオーダーのバックエンドAPI。
**GCPサンドボックスプロジェクト上の Cloud Run サービス(サービス名: store-app / asia-northeast1)。**
システムID: store-app

## 調査の原則(GCPホストのシステム)
- 社内の監視基盤から見えるのは外形監視(synthetic-monitor / edge-gateway)の結果のみ。
  **Cloud Run 側のアプリケーションログ・リビジョン構成は Gemini Cloud Assist で調査する。**
- Cloud Assist への調査依頼(investigate_issue)には、サービス名(store-app)・症状
  (HTTP 500 多発など)・発生時間帯を具体的に含めること。
- 追跡質問では前回応答の contextId を引き継ぐこと。
- Cloud Assist は調査(読み取り)のみに使用し、構成変更・再デプロイの実行を依頼しないこと。

## 既知事象: HTTP 500 の多発(環境変数の設定不備)
- 症状: 外形監視で `/` への GET が 500 を返し続ける。`/healthz` は 200(プロセス自体は起動)。
- ログの特徴(Cloud Run 側): `DB connection failed: could not connect to server db.internal:5432`、
  直前に環境変数(`FAIL_MODE` 等)を変更したリビジョンのデプロイ履歴。
- 原因: Cloud Run サービスの環境変数の設定不備により、アプリがDB接続タイムアウトを起こし
  全リクエストが 500 を返す。
- 対応: **Cloud Run はアプリケーション側からの再起動の対象外。必ずインフラ運用チームへ
  エスカレーションし、環境変数を修正した新リビジョンのデプロイを依頼する**
  (escalation-policy.md 参照)。エスカレーション時には Cloud Assist で特定した
  エラーログと該当リビジョンの情報を添えること。

## 既知事象: デプロイ直後のコンテナ起動失敗
- 症状: 新リビジョンのデプロイ後、全リクエストが 503 になる。
- ログの特徴(Cloud Run 側): `Container failed to start`、STARTUP プローブの失敗。
- 対応: インフラ運用チームへエスカレーションし、直前の正常リビジョンへの
  トラフィック切り戻しを依頼する。
