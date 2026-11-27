# Security

[← back to the README](../README.md)

roadie runs as root and handles your passwords, so here is what it does to protect your server.
To report a security problem, see [SECURITY.md](../SECURITY.md).

## How the server is protected

Only SSH and the web ports 80 and 443 are open. Every settings page sits behind a login with a
passkey or a code app, and a firewall, fail2ban and automatic security updates guard the rest.
Your apps cannot log in, so only the exact addon and playback paths they use are open, and only
for reading. SSH accepts only your key as soon as you finished *Finish SSH setup* once; until
then password login stays on, so nobody gets locked out. All passwords are readable only by root
and never appear on the command line, in the output or in problem reports.

Your server explains this itself, with its real ports, rules and files, and why each part is
there:

```sh
sudo roadie explain network
sudo roadie explain security
```

The same protection built by hand, with every file: [Build it yourself](manual-setup.md#part-2--secure-the-server).

## No telemetry

roadie only talks to what your setup needs: package and image downloads, DuckDNS, ntfy, your
Usenet services, and GitHub for its own updates.

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
