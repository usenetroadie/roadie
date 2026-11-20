<p align="center">
  <img src="assets/logo.svg" width="112" alt="roadie logo">
</p>

<h1 align="center">roadie</h1>

<p align="center">
  <b>Days of tinkering. One guided setup.</b><br>
  A terminal wizard that turns a fresh VPS into your own
  <a href="https://github.com/Viren070/AIOStreams">AIOStreams</a> + Usenet streaming server.
</p>

<p align="center">
  <a href="https://github.com/usenetroadie/roadie/releases"><img src="https://img.shields.io/github/v/release/usenetroadie/roadie?color=3fb950" alt="Release"></a>
  <a href="https://github.com/usenetroadie/roadie/actions/workflows/e2e.yml"><img src="https://img.shields.io/github/actions/workflow/status/usenetroadie/roadie/e2e.yml?branch=main&label=end-to-end" alt="End-to-end tests"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue" alt="MIT license"></a>
</p>

You answer a few questions: your Usenet provider, your indexer, what you like to watch. roadie
checks each answer, sets up the server and gives you the addon address for Stremio or Nuvio.
Afterwards it keeps the server running and shows you how it works, so you can take over yourself.

![The questions, in demo mode (nothing is changed)](assets/questions.gif)

## Why it exists

I have run AIOStreams on my own small VPS since September, for a few people. Getting there took
days: a reverse proxy, a real login in front of the settings pages, providers on different
backbones, a firewall that does not lock me out, backups that restore. roadie is that setup as a
guided install.

roadie is written with a lot of help from Claude, an AI. What it sets up comes from running this
server myself, and every release is installed and checked automatically on all supported systems
before it is published.

If you would rather build it all by hand, [Build it yourself](docs/manual-setup.md) walks you
through the same server step by step. If roadie disappeared tomorrow, your server would keep
running: it is a plain Docker Compose setup, and `sudo roadie explain` shows how to look after it
without roadie.

## What you get

- AIOStreams with its built-in Usenet streaming and Prowlarr for your indexers, with your
  languages first and recent, complete releases ranked higher
- with several indexers, the main ones are searched first and the others only when too little is found
- catalogs on the start page, and with a free TMDB key your own [AIOMetadata](https://github.com/cedya77/aiometadata)
- one addon address per person, each with their own languages and quality if wanted
- HTTPS on a free DuckDNS name or your own domain, and a passkey login in front of every settings page
- SSH with keys only (switched on once your key login worked), a firewall, fail2ban and automatic
  security updates
- push messages when something breaks, encrypted weekly backups you can download in the browser,
  updates only to tested versions
- `sudo roadie help`: what to do next, what to do when something does not work, and every service,
  port, file and scheduled job on your server, and why

No telemetry. No automatic reports. You decide what to share.

## Quick start

You need a fresh VPS with Ubuntu 24.04, Debian 12 or Debian 13 (x86_64 or ARM64, 2 GB RAM or
more, 4 GB recommended), a DuckDNS name or your own domain, a Usenet provider and at least one indexer.
Never rented a server? See [Getting started](docs/getting-started.md).

Log in to the server and run:

```sh
curl -fsSL https://raw.githubusercontent.com/usenetroadie/roadie/main/get.sh | sudo bash
```

This downloads the newest release, checks its checksum and signature, installs it and starts the
wizard. If the connection drops, run `sudo roadie` again: it continues where it stopped.

<details>
<summary>Install by hand instead</summary>

```sh
V=$(curl -fsSLI -o /dev/null -w '%{url_effective}' https://github.com/usenetroadie/roadie/releases/latest | sed 's|.*/||')
curl -fLO https://github.com/usenetroadie/roadie/releases/download/$V/roadie-$V.tar.gz
curl -fLO https://github.com/usenetroadie/roadie/releases/download/$V/roadie-$V.tar.gz.sha256
sha256sum -c roadie-$V.tar.gz.sha256          # must print "OK"
tar xzf roadie-$V.tar.gz && cd roadie-$V
sudo bash install.sh && sudo roadie
```

</details>

![A real setup from start to finish (sped up)](assets/setup.gif)

More to look at: the [passkey login](assets/login.gif), the [AIOStreams configuration](assets/aiostreams.gif)
and the [AIOMetadata catalogs](assets/aiometadata.gif).

## Documentation

| Guide | For |
|---|---|
| [Getting started](docs/getting-started.md) | renting a VPS, connecting, what to prepare |
| [Commands](docs/commands.md) | everyday commands, the menu, several people |
| [Backups](docs/backups.md) | backups, downloading them, moving to a new server |
| [Security](docs/security.md) | what is protected and how |
| [Troubleshooting](docs/troubleshooting.md) | no streams, login problems, getting help |
| [Build it yourself](docs/manual-setup.md) | the same server by hand, step by step |
| [Advanced](docs/advanced.md) | answers files, changing AIOStreams and Prowlarr yourself, uninstall |

## Limits

Usenet only. It needs a VPS with a public IPv4 address; hosting at home behind a router is not
supported yet. It is an early release used by few people so far.

## Help

Questions are welcome, also small ones: [open an issue](https://github.com/usenetroadie/roadie/issues/new/choose)
and attach the output of `sudo roadie diagnose` (it leaves out passwords, keys, IP addresses and
your domain). Please ask here and not in the AIOStreams, Prowlarr, Authelia or Traefik
communities: they do not know this setup. roadie is a hobby project, so an answer may take a few
days. Security problems: see [SECURITY.md](SECURITY.md).

Changes are listed in the [changelog](CHANGELOG.md); contributing is described in
[CONTRIBUTING.md](CONTRIBUTING.md).

Built on [AIOStreams](https://github.com/Viren070/AIOStreams) by Viren070, [Traefik](https://traefik.io),
[Authelia](https://www.authelia.com), [Prowlarr](https://prowlarr.com), [age](https://age-encryption.org)
and [ntfy](https://ntfy.sh). roadie is not affiliated with any of them, nor with Stremio or Nuvio.

roadie only installs and configures software. It does not provide, list or recommend any content
or sources of content. You are responsible for how you use it and for following the laws of your
country and the terms of the services you use.

MIT license, see [LICENSE](LICENSE). The installed programs keep their own licenses.
