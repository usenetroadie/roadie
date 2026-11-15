#!/usr/bin/env bash
# Installs roadie from this folder to /opt/roadie/app and adds the
# `roadie` command. Needs only what a fresh Ubuntu 24.04 already has.
set -euo pipefail
if [ "$(id -u)" -ne 0 ]; then echo "Please run with sudo: sudo bash install.sh" >&2; exit 1; fi
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
