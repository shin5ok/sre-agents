#!/bin/bash
# store-web-01 / store-web-03 用の起動スクリプト(健全なWebサーバー)。
# GCEの startup-script は再起動のたびに実行されるため、冪等な処理のみ書くこと。
set -e

INSTANCE_NAME=$(curl -s -H "Metadata-Flavor: Google" "http://metadata.google.internal/computeMetadata/v1/instance/name")

apt-get update
apt-get install -y nginx rsyslog

cat > /var/www/html/index.nginx-debian.html <<HTMLEOF
<html><body><h1>${INSTANCE_NAME}</h1><p>Store Backend - OK</p></body></html>
HTMLEOF

systemctl enable nginx
systemctl restart nginx
systemctl enable rsyslog
systemctl restart rsyslog

curl -sSO https://dl.google.com/cloudagents/add-google-cloud-ops-agent-repo.sh
bash add-google-cloud-ops-agent-repo.sh --also-install
rm -f add-google-cloud-ops-agent-repo.sh
