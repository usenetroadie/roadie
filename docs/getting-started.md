# Getting started

[← back to the README](../README.md)

This guide is for you if you have never rented or used a server before.

## 1. Rent a VPS

A VPS is a small virtual server in a data center that is yours alone. Any provider works.
Choose:

- **Ubuntu 24.04 LTS**, **Debian 12** or **Debian 13**
- architecture **x86_64** or **ARM64** – the Oracle Cloud *Always Free* ARM server works well
- at least **2 vCPU**, **4 GB RAM** recommended (2 GB works, roadie then adds swap), **20 GB** storage
- a public IPv4 address, nothing else installed or running on it

The provider then shows you an **IP address**, a **username** (often `root` or `ubuntu`) and
either a password or a place to add an SSH key.

### Open the ports in your provider's firewall

Some providers have a firewall of their own in front of the server, separate from the one roadie
sets up. Ports **80** and **443** must be open there (and **22** for SSH), or the HTTPS certificate
cannot be issued.

- **Oracle Cloud:** *Networking → Virtual cloud networks →* your network *→ Security Lists →
  Default Security List → Add Ingress Rules*: source `0.0.0.0/0`, TCP, destination port `80`, then
  again for `443`.
- **Hetzner, AWS, Google Cloud and others:** look for *Firewall* or *Security group* in the
  provider's panel. If the server has none attached, nothing needs to be done.

## 2. Connect for the first time

- **Phone or tablet:** an SSH app such as Termius. Add a host with the IP address, username and password.
- **PC or Mac:** open a terminal and type `ssh USER@IP-ADDRESS`, for example `ssh root@203.0.113.10`.
  Confirm the question with `yes` and enter the password.

You are now on the server. Run the install commands from the [Quick start](../README.md#quick-start).

## 3. What to have ready

roadie asks for these during the setup and checks each one right away:

| What | Where you get it |
|---|---|
| A web address | **either** a free DuckDNS subdomain + token from [duckdns.org](https://www.duckdns.org) (sign in, enter a name, *add domain*; the token is at the top of the page) **or** your own domain – see below |
| Usenet provider login | your provider account (username, password). A second provider on a **different backbone** is a good backup – roadie warns if both are on the same network |
| Usenet indexer | your indexer account: its API address and API key |
| A password manager | for the passwords, keys and the backup key roadie creates for you |
| ntfy app (optional) | free app for push messages; roadie shows you the topic to subscribe to |
| TMDB API key (optional, recommended) | free at [themoviedb.org](https://www.themoviedb.org/settings/api); improves title matching |

### Using your own domain

At your domain provider, create three DNS entries of type **A**, each pointing to your server's
public IPv4 address (roadie shows it during the setup):

| Name | Type | Value |
|---|---|---|
| `aiostreams.example.com` | A | your server's address |
| `auth.example.com` | A | your server's address |
| `prowlarr.example.com` | A | your server's address |

Or a single wildcard entry `*.example.com`. You can also use a subdomain such as
`media.example.com` – then the names are `aiostreams.media.example.com` and so on. New entries can
take a few minutes; roadie checks them and tells you exactly which one is missing or wrong.

Plan about 20–30 minutes. You can stop at any time with Ctrl+C and continue later with `sudo roadie`.

## 4. After the setup

1. **Add the addon:** copy the addon address roadie shows you and add it in Stremio or Nuvio.
   Keep it private – it works without a login.
2. **First login to the settings pages:** open the address roadie shows, log in with your name and
   password, then set up a **passkey** (Face ID / fingerprint) or a code app. The confirmation code
   for this step: `sudo roadie code`.
3. **Push messages:** subscribe to your topic in the ntfy app (`sudo roadie access` shows it).
4. **Backup key:** if roadie created one, it showed you the private key once. Keep it in your password
   manager – without it no backup can be opened. See [Backups](backups.md).
5. **SSH:** log in once with your SSH key as the new admin user. Only then does roadie switch off
   password logins (run `sudo roadie` → *Continue / check everything again* to finish this step).

`sudo roadie access` shows all addresses and logins again at any time.
