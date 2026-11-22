#!/usr/bin/env bash
# Installs roadie from this folder to /opt/roadie/app and adds the
# `roadie` command. Needs only Python 3 (Ubuntu 24.04 and Debian cloud images have it).
set -euo pipefail
if [ "$(id -u)" -ne 0 ]; then echo "Please run with sudo: sudo bash install.sh" >&2; exit 1; fi
# same list as SYSTEMS in roadie/preflight.py; checked here because older systems have a Python
# that cannot even start roadie (no -P before Python 3.11)
os="$(. /etc/os-release 2>/dev/null; echo "${ID:-} ${VERSION_ID:-}")"
case "$os" in
    "ubuntu 24.04"|"debian 12"|"debian 13") ;;
    *) echo "roadie needs Ubuntu 24.04, Debian 12 or Debian 13 (found: $(. /etc/os-release 2>/dev/null; echo "${PRETTY_NAME:-unknown}"))." >&2
       echo "Reinstall the server with Ubuntu 24.04 (most providers offer this in their control panel), then run this again." >&2
       exit 1 ;;
esac
if ! command -v python3 >/dev/null; then  # minimal Debian images come without it
    apt-get update -q && DEBIAN_FRONTEND=noninteractive apt-get install -y -q python3
fi
src="$(cd "$(dirname "$0")" && pwd)"
install -d -m 0755 /opt/roadie/app
rm -rf /opt/roadie/app/roadie
cp -r "$src/roadie" /opt/roadie/app/
find /opt/roadie/app -name __pycache__ -prune -exec rm -rf {} +
cat > /usr/local/bin/roadie <<'WRAPPER'
#!/bin/sh
# app.prev: earlier version kept by a self-update, used only if app is missing
if [ -d /opt/roadie/app/roadie ]; then export PYTHONPATH=/opt/roadie/app
else export PYTHONPATH=/opt/roadie/app.prev; fi
# -P: never import from the current folder (sudo roadie in /tmp must not run planted code)
exec python3 -P -m roadie "$@"
WRAPPER
chmod 0755 /usr/local/bin/roadie
echo "Installed. Start the wizard with: sudo roadie"
