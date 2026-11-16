# Security

[← back to the README](../README.md)

roadie runs as root and handles your passwords, so here is what it does to protect your server.
To report a security problem, see [SECURITY.md](../SECURITY.md).

## What is reachable from outside

| Port | What | Protection |
|---|---|---|
| 22 (your SSH port) | SSH | key login; rate limit; fail2ban |
| 80 / 443 | Traefik | HTTPS (Let's Encrypt), security headers; fail2ban against web scanners |

All other services (AIOStreams, Authelia, Prowlarr) only listen inside Docker. There is no Docker
socket in any container.

## Logins

- **Settings pages** (AIOStreams, Prowlarr) sit behind Authelia: password plus a passkey or a code
  app. Only the few paths your player needs (addon, streams, playback) are open – and only for
  `GET`/`HEAD`/`OPTIONS`. Configuration pages and the API always need the login.
- **SSH:** roadie creates an admin user with your SSH key. Password and root login are switched off
  **only after** a key login of that user was seen, so the setup does not lock you out by mistake.
  The addresses you used during the setup are never banned by fail2ban.

## On the server

- **Firewall** (UFW): only SSH, 80 and 443. Because Docker ports bypass UFW, web bans go into the
  `DOCKER-USER` chain.
- **fail2ban:** jails for SSH (also unknown user names), web scanners and repeat offenders.
- **Automatic security updates**, with a restart at night only when an update needs it.
- **Secrets** (passwords, keys, tokens) are stored root-only (`/etc/roadie`, mode 600) and never
  passed on the command line. Output and problem reports remove them.
- **No telemetry:** roadie only talks to what your setup needs – package and image downloads,
  DuckDNS, ntfy, your Usenet services – and to GitHub for its own updates.

## Signed updates

`sudo roadie update` first looks for a newer roadie release. It installs it only if the checksum
matches **and** the release carries a valid signature from the project's release key
(OpenSSH `ssh-keygen -Y verify`; the public key is built into roadie). The new version must start
before it replaces the old one; the old one stays in `/opt/roadie/app.prev`. If anything fails,
nothing changes.

Going back by hand:

```sh
sudo rm -rf /opt/roadie/app && sudo mv /opt/roadie/app.prev /opt/roadie/app
```

Docker, Traefik, Authelia, AIOStreams and Prowlarr are installed in fixed versions that passed the
end-to-end test (`roadie/data/versions.json`); the Docker apt key is checked by its fingerprint.
