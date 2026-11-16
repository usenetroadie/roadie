# End-to-end test (fresh Ubuntu 24.04, Debian 12/13; x86_64 or arm64)

`tests/e2e/run.sh` runs the whole wizard unattended in test mode (`"test_mode": true`:
self-signed certificate, no DuckDNS call, no DNS check, smaller disk allowed) with a fake
indexer, then checks the result with `verify.sh`, runs the setup a second time, makes a backup
and shows the status.

```sh
sudo bash tests/e2e/run.sh            # DuckDNS address (example.duckdns.org)
sudo bash tests/e2e/run.sh domain     # own domain (example.com)
```

The GitHub workflow `e2e.yml` runs it on Ubuntu runners (x86_64 and arm64) and in Debian 12 and
13 LXD containers. Locally:
`lxc launch ubuntu:24.04 t -c security.nesting=true -c limits.memory=3GiB -c limits.cpu=2`
(or `images:debian/13`; minimal Debian images need `python3 curl ca-certificates openssh-server`).

After one login with the test key as the admin user, a second run switches password and root
login off (`sshd -T | grep passwordauthentication`).

## Backup and restore (tested 14.11.2026)

On machine A: `sudo roadie backup` → `~<admin>/backups/roadie-backup-<date>.tar.age`.
On a fresh machine B: install, `sudo roadie restore <file> --identity <age key file>`, then
`sudo roadie` → *Continue*. Expected: same addon URL as on A, `verify.sh` all `ok`
(the questionnaire is not asked again; the SSH key check waits for one key login).
