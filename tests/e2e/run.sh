#!/usr/bin/env bash
# Full unattended run in test mode on this (fresh) machine: install, fake indexer, setup,
# checks, a harmless second run, backup and status. Used by the e2e workflow (runners and
# Debian containers) and for manual tests. Run as root from anywhere:
#   bash tests/e2e/run.sh            DuckDNS address (example.duckdns.org)
#   bash tests/e2e/run.sh domain     own domain (example.com)
#   bash tests/e2e/run.sh upgrade    set up with the latest release, then update to this
#                                    checkout: the way every existing user takes
# With a TMDB API key in /root/tmdb-key (mode 600) the own AIOMetadata is set up and checked too.
set -euo pipefail
cd "$(dirname "$0")/../.."
mode="${1:-duckdns}"

src="$PWD"  # the tree the first setup runs from
if [ "$mode" = upgrade ]; then
    command -v curl >/dev/null || { apt-get update -q && DEBIAN_FRONTEND=noninteractive apt-get install -y -q curl; }
    tag="$(curl -fsSL https://api.github.com/repos/usenetroadie/roadie/releases/latest \
           | python3 -c 'import json,sys; print(json.load(sys.stdin)["tag_name"])')"
    src=/root/roadie-release
    rm -rf "$src" && mkdir -p "$src"
    curl -fsSL "https://github.com/usenetroadie/roadie/archive/refs/tags/$tag.tar.gz" | tar -xz --strip-components=1 -C "$src"
    echo "upgrade test: first setup with release $tag"
fi
bash "$src/install.sh"
ip="$(hostname -I | awk '{print $1}')"
(setsid nohup python3 tests/e2e/mock_newznab.py 8099 >/dev/null 2>&1 &)
# the wizard switches the firewall on; the containers must still reach the fake indexer
command -v ufw >/dev/null || { apt-get update -q && DEBIAN_FRONTEND=noninteractive apt-get install -y -q ufw; }
ufw allow 8099/tcp >/dev/null
key=/root/e2e-key
[ -f "$key" ] || ssh-keygen -q -t ed25519 -N "" -f "$key"

domain="$(python3 - "$ip" "$mode" "$key.pub" "$src" <<'PY'
import json, os, sys
ip, mode, key, src = sys.argv[1:]
d = json.load(open(f"{src}/examples/answers.example.json"))
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
if [ "$mode" = upgrade ]; then
    bash "$src/tests/e2e/verify.sh" "$domain" /tmp/run.log   # the release works before the update
    old="$(roadie --version)"
    ROADIE_NO_START=1 bash get.sh   # the one-line installer: real download, checksum, signature
    [ "$(roadie --version)" = "$old" ] || { echo "FAIL  get.sh did not install the latest release"; exit 1; }
    bash install.sh
    echo "upgrade test: $old -> $(roadie --version)"
    roadie --skip-self-update update | tee /tmp/run.log
    roadie watch --dry | tee /tmp/watch.log
    if grep -q '^problem: .*aiometadata' /tmp/watch.log; then echo "FAIL  watcher complains about AIOMetadata"; exit 1; fi
fi
bash tests/e2e/verify.sh "$domain" /tmp/run.log
roadie setup --answers /tmp/answers.json >/dev/null    # a second run must be harmless
bash tests/e2e/verify.sh "$domain"
roadie backup
roadie status
roadie report --dry
# the example provider does not exist, so doctor reports a problem: only its single checks count
roadie doctor | tee /tmp/doctor.log || true
grep -q "Indexer A: " /tmp/doctor.log && grep -q "news.example.com: " /tmp/doctor.log \
    || { echo "FAIL  doctor did not test provider and indexer"; exit 1; }
echo "e2e passed ($mode, $(. /etc/os-release && echo "$ID $VERSION_ID"), $(dpkg --print-architecture))"
