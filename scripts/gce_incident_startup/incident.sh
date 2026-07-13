#!/bin/bash
# store-web-02 用の起動スクリプト(base.sh相当 + 障害シナリオ一式)。
#
# 重要: GCEの startup-script は起動のたびに(再起動時も)実行される。
# memleak.service / incident-logsim.service はここでは systemctl enable も
# start もしない(=常に未起動のまま作られる)。障害注入は
# `scripts/gce_incident.sh break` が明示的に start する。こうしないと、
# resolve(インスタンスの再起動)のたびに障害が自動再発してしまう。
set -e

INSTANCE_NAME=$(curl -s -H "Metadata-Flavor: Google" "http://metadata.google.internal/computeMetadata/v1/instance/name")

apt-get update
apt-get install -y nginx rsyslog python3

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

# --- 障害シナリオ: メモリリークプロセス(未起動のまま設置) ---
cat > /usr/local/bin/memleak.py <<'PYEOF'
#!/usr/bin/env python3
import time

leak = []
CHUNK_MB = 5
INTERVAL_SEC = 5

while True:
    leak.append(bytearray(CHUNK_MB * 1024 * 1024))
    time.sleep(INTERVAL_SEC)
PYEOF
chmod +x /usr/local/bin/memleak.py

cat > /etc/systemd/system/memleak.service <<'UNITEOF'
[Unit]
Description=Demo memory leak process (intentional, for SRE agent demo)
After=network.target

[Service]
ExecStart=/usr/bin/python3 /usr/local/bin/memleak.py
Restart=no
StandardOutput=journal
StandardError=journal
SyslogIdentifier=memleak-app

[Install]
WantedBy=multi-user.target
UNITEOF

# --- 障害シナリオ: 擬似障害ログ生成(未起動のまま設置) ---
cat > /usr/local/bin/incident_logsim.sh <<'SIMEOF'
#!/bin/bash
STORE_ID="1042"
MSGS=(
  "WARN response_time_ms=1800 endpoint=/api/inventory store_id=${STORE_ID}"
  "WARN memory usage high: 74% rss"
  "WARN response_time_ms=2600 endpoint=/api/sales store_id=${STORE_ID}"
  "ERROR db connection pool exhausted, retrying"
  "WARN memory usage high: 88% rss"
  "ERROR request timeout endpoint=/api/inventory store_id=${STORE_ID} after 5000ms"
  "WARN gc pause 1200ms"
  "ERROR failed health check /healthz status=503"
  "CRIT memory usage critical: 95% rss, service may become unresponsive"
)

while true; do
  MSG=${MSGS[$RANDOM % ${#MSGS[@]}]}
  logger -t store-app "$MSG"
  sleep $((RANDOM % 30 + 15))
done
SIMEOF
chmod +x /usr/local/bin/incident_logsim.sh

cat > /etc/systemd/system/incident-logsim.service <<'UNITEOF'
[Unit]
Description=Demo synthetic incident log generator (intentional, for SRE agent demo)
After=network.target

[Service]
ExecStart=/usr/local/bin/incident_logsim.sh
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
UNITEOF

systemctl daemon-reload
