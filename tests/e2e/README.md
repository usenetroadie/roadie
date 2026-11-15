# End-to-end test (fresh Ubuntu 24.04 machine or LXD container)

Runs the whole wizard unattended in test mode (`"test_mode": true` in the answer file:
self-signed certificate, no DuckDNS call, smaller disk allowed).

1. Copy the project to the test machine and run `sudo bash install.sh`.
2. Start the fake indexer: `python3 tests/e2e/mock_newznab.py 8099 &` (allow the port: `ufw allow 8099/tcp`).
3. Copy `examples/answers.example.json`, add `"test_mode": true`, set `indexers[0].url` to
   `http://<machine-ip>:8099` and `ssh_public_key` to a real test key.
4. `sudo roadie setup --answers answers.json` → exit code 0.
5. `sudo bash tests/e2e/verify.sh example.duckdns.org` → every line starts with `ok`. With several people, `/etc/roadie/addon-urls.txt`
   (`name<TAB>url` per line) is checked too: each person's addon must return streams.
6. Log in once with the test key as the admin user, run step 4 again → password and root
   login are switched off (`sshd -T | grep passwordauthentication`).

LXD: `lxc launch ubuntu:24.04 sw -c security.nesting=true -c limits.memory=3GiB -c limits.cpu=2`.

## Backup and restore (tested 14.11.2026)

On machine A: `sudo roadie backup` → `~<admin>/backups/roadie-backup-<date>.tar.age`.
On a fresh machine B: install, `sudo roadie restore <file> --identity <age key file>`, then
`sudo roadie` → *Continue*. Expected: same addon URL as on A, `verify.sh` all `ok`
(the questionnaire is not asked again; the SSH key check waits for one key login).
