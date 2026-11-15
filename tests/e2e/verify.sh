#!/usr/bin/env bash
# End-to-end checks after `roadie setup` in test mode. Run as root on the test machine.
# Usage: verify.sh <domain>      e.g. verify.sh example.duckdns.org
set -u
domain="${1:-example.duckdns.org}"
aio="aiostreams.$domain"
addon="$(cat /etc/roadie/addon-url.txt)"
path="${addon#https://$aio}"
fail=0
check() {  # check <description> <expected> <actual>
  if [ "$2" = "$3" ]; then echo "ok    $1"; else echo "FAIL  $1 (expected $2, got $3)"; fail=1; fi
}
code() { curl -sk -o /dev/null -w '%{http_code}' --max-time 60 --resolve "$aio:443:127.0.0.1" --resolve "$aio:80:127.0.0.1" "$@"; }

check "addon manifest without login"        200 "$(code "https://$aio$path")"
check "stream request without login"        200 "$(code "https://$aio${path%manifest.json}stream/movie/tt0000001.json")"
streams() { curl -sk --max-time 90 --resolve "$aio:443:127.0.0.1" "https://$aio${path%manifest.json}stream/$1.json" | python3 -c 'import json,sys;print(min(len(json.load(sys.stdin).get("streams",[])),1))' 2>/dev/null || echo 0; }
check "indexer search finds streams"        1 "$(streams movie/tt0111161)"
# every person has an own addon URL (name<TAB>url per line); each must answer with streams
if [ -f /etc/roadie/addon-urls.txt ]; then
  while IFS=$'\t' read -r pname purl; do
    ppath="${purl#https://$aio}"
    got="$(curl -sk --max-time 90 --resolve "$aio:443:127.0.0.1" "https://$aio${ppath%manifest.json}stream/movie/tt0111161.json" | python3 -c 'import json,sys;print(min(len(json.load(sys.stdin).get("streams",[])),1))' 2>/dev/null || echo 0)"
    check "person '$pname' finds streams" 1 "$got"
  done < /etc/roadie/addon-urls.txt
fi
check "start page needs login"              302 "$(code "https://$aio/")"
check "configure page needs login"          302 "$(code "https://$aio/stremio/configure")"
check "user config page needs login"        302 "$(code "https://$aio${path%manifest.json}configure")"
# encoded paths (Traefik lets them through for AIOStreams' file names) must not skip the login
for p in "/stremio/x/y/%63onfigure" "/stremio%2Fconfigure" "/stremio/%2E%2E/configure" \
         "/api/v1/proxy/..%2Fuser" "/stremio/..%2F..%2Fapi%2Fv1%2Fuser"; do
  check "encoded path $p needs login" 302 "$(code "https://$aio$p")"
done
check "API needs login"                     401 "$(code -H 'Accept: application/json' -X POST "https://$aio/api/v1/user")"
check "POST on addon path is not bypassed"  401 "$(code -H 'Accept: application/json' -X POST "https://$aio$path")"
check "http redirects to https"             301 "$(code "http://$aio/")"
check "unknown host is not served"          404 "$(code --resolve "other.$domain:443:127.0.0.1" "https://other.$domain/")"
check "firewall active"                     1 "$(ufw status | grep -c '^Status: active')"
check "fail2ban jails"                      4 "$(fail2ban-client status | grep -o 'sshd-invalid\|traefik-scanner\|recidive\|sshd,' | sort -u | wc -l)"
check "secrets file root only"              600 "$(stat -c %a /etc/roadie/secrets.json)"
check "env files root only"                 600 "$(stat -c %a /opt/roadie/stack/env/aiostreams.env)"
check "only 80/443 published by docker"     2 "$(docker ps --format '{{.Ports}}' | grep -o '0.0.0.0:[0-9]*' | sort -u | wc -l)"
# shown on purpose: the addon URL (config id + encrypted password) and the push address to subscribe to
check "no secrets in wizard output"         0 "$(grep -c -F -f <(python3 -c 'import json;[print(v) for k, v in json.load(open("/etc/roadie/secrets.json")).items() if len(v) > 11 and not k.startswith("aiostreams_config_") and k != "ntfy_topic"]') "${2:-/dev/null}")"
exit $fail
