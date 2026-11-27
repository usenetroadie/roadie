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
         "/api/v1/proxy/..%2Fuser" "/stremio/..%2F..%2Fapi%2Fv1%2Fuser" \
         "/stremio/x/y/manifest.json%3F/configure"; do
  check "encoded path $p needs login" 302 "$(code "https://$aio$p")"
done
check "API needs login"                     401 "$(code -H 'Accept: application/json' -X POST "https://$aio/api/v1/user")"
if docker ps --format '{{.Names}}' | grep -q aiometadata; then  # own AIOMetadata (with a TMDB key)
  meta="metadata.$domain"
  mcode() { curl -sk -o /dev/null -w '%{http_code}' --max-time 60 --resolve "$meta:443:127.0.0.1" "$@"; }
  check "AIOMetadata settings need login"     302 "$(mcode "https://$meta/configure")"
  check "AIOMetadata API needs login"         303 "$(mcode -X POST "https://$meta/api/config/save")"
  # its catalogs reach the player through AIOStreams, with real titles from TMDB
  titles="$(python3 - "$aio" "$path" <<'PY'
import http.client, json, ssl, sys
host, path = sys.argv[1:]
def get(p):
    c = http.client.HTTPSConnection("127.0.0.1", 443, context=ssl._create_unverified_context(), timeout=90)
    c.request("GET", p, headers={"Host": host})
    return json.loads(c.getresponse().read())
cats = [c for c in get(path)["catalogs"] if "roadiemetadata" in c["id"] and c["type"] == "movie"]
metas = get(path.replace("manifest.json", f"catalog/movie/{cats[0]['id']}.json")).get("metas", []) if cats else []
print(min(len(metas), 1))
PY
)"
  check "AIOMetadata catalog has titles"      1 "${titles:-0}"
fi
st="status.$domain"
check "status page needs login"             302 "$(curl -sk -o /dev/null -w '%{http_code}' --max-time 60 --resolve "$st:443:127.0.0.1" "https://$st/")"
check "status page is written"              1 "$(grep -c '<title>roadie</title>' /opt/roadie/stack/data/status/index.html 2>/dev/null)"
check "status page is served inside"        1 "$(docker exec roadie-status-1 wget -q -O - http://127.0.0.1:8080/ 2>/dev/null | grep -c '<title>roadie</title>')"
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
