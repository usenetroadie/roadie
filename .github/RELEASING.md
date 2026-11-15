# Release setup (GitHub repository settings)

`roadie update` installs a new version as root on every server, and it trusts only the
release signature. So whoever can make the release workflow sign can run code on all servers.
Set these once in the public repository:

1. **Environment `release`** (Settings → Environments → New environment):
   - *Required reviewers*: the maintainer account. Every tag push then waits for approval.
   - *Deployment branches and tags*: "Selected branches and tags", add the tag rule `v*`.
   - *Environment secret* `RELEASE_SIGNING_KEY`: the OpenSSH private key whose public key is
     `ALLOWED_SIGNERS` in `roadie/ops/selfupdate.py`.
     Do **not** keep a repository secret with the same name.
2. **Tag protection** (Settings → Rules → Rulesets → New tag ruleset): target `v*`, restrict
   creation, update and deletion to the maintainer (bypass list), block force pushes.
3. **Actions** (Settings → Actions → General): "Read repository contents" as default
   workflow permissions; require approval for workflows from outside contributors.
4. Keep the signing key offline as well (password manager). If it ever leaks: make a new key
   pair, ship the new `ALLOWED_SIGNERS` in a release signed with the old key, then delete the old
   key everywhere.

Stronger option for later: sign on your own computer (`ssh-keygen -Y sign -n roadie-release`)
and upload only the `.sig` file, so the key never reaches GitHub.
