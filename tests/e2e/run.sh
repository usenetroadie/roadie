#!/usr/bin/env bash
# Full unattended run in test mode on this (fresh) machine: install, fake indexer, setup,
# checks, a harmless second run, backup and status. Used by the e2e workflow (runners and
# Debian containers) and for manual tests. Run as root from anywhere:
#   bash tests/e2e/run.sh            DuckDNS address (example.duckdns.org)
#   bash tests/e2e/run.sh domain     own domain (example.com)
# With a TMDB API key in /root/tmdb-key (mode 600) the own AIOMetadata is set up and checked too.
set -euo pipefail
cd "$(dirname "$0")/../.."
mode="${1:-duckdns}"

bash install.sh
ip="$(hostname -I | awk '{print $1}')"
(setsid nohup python3 tests/e2e/mock_newznab.py 8099 >/dev/null 2>&1 &)
# the wizard switches the firewall on; the containers must still reach the fake indexer
command -v ufw >/dev/null || { apt-get update -q && DEBIAN_FRONTEND=noninteractive apt-get install -y -q ufw; }
ufw allow 8099/tcp >/dev/null
key=/root/e2e-key
[ -f "$key" ] || ssh-keygen -q -t ed25519 -N "" -f "$key"

domain="$(python3 - "$ip" "$mode" "$key.pub" <<'PY'
import json, os, sys
ip, mode, key = sys.argv[1:]
d = json.load(open("examples/answers.example.json"))
d.update(test_mode=True, admin_user="roadieadmin", ssh_public_key=open(key).read().strip())
d["indexers"][0]["url"] = f"http://{ip}:8099"
tmdb = open("/root/tmdb-key").read().strip() if os.path.exists("/root/tmdb-key") else ""
if tmdb:  # the key is never printed or passed on a command line
    d["tmdb_api_key"] = tmdb
if mode == "domain":
    d.pop("duckdns")
    d["domain"] = "example.com"
json.dump(d, open("/tmp/answers.json", "w"))
print(d.get("domain") or f"{d['duckdns']['subdomain']}.duckdns.org")
PY
)"

roadie setup --answers /tmp/answers.json | tee /tmp/run.log
bash tests/e2e/verify.sh "$domain" /tmp/run.log
roadie setup --answers /tmp/answers.json >/dev/null    # a second run must be harmless
bash tests/e2e/verify.sh "$domain"
roadie backup
roadie status
echo "e2e passed ($mode, $(. /etc/os-release && echo "$ID $VERSION_ID"), $(dpkg --print-architecture))"
