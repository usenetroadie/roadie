# Changelog

All notable changes. Versions follow [semantic versioning](https://semver.org).

## 0.6.1 – 2026-11-22

- On an unsupported system (for example Ubuntu 22.04) the installer stopped with a confusing
  Python error ("Unknown option: -P"). It now says right away which systems roadie needs and
  installs nothing.

## 0.6.0 – 2026-11-20

- roadie speaks English only now. Servers set up in German keep working, the texts are English
  after the update. The languages of your streams do not change.
- The newest backup can be downloaded on your status page, in the browser. No SSH app needed.
- Testing the SSH key at the end of the setup is optional now. Do it later with `sudo roadie` →
  *Finish SSH setup*. Changing settings no longer waits for an SSH login.
- Stopped in the middle of the questions? The finished parts are kept, and `sudo roadie` goes on there.
- A new server with an uploaded roadie backup offers to restore it first. The private backup key
  can be pasted.
- An indexer that does not answer during the setup is no longer called a wrong address. You can
  try again or keep it. One indexer that fails no longer stops the whole setup.
- roadie asks how many connections your plan allows and uses nine tenths of them. On servers set
  up earlier this applies when you change the Usenet settings.
- The watcher checks the Usenet login once an hour and tells you when the provider is away.
- The end of the setup shows the addon link and your login first, the rest is in
  `sudo roadie access`. Long steps show a running clock.
- `sudo roadie diagnose` shows a short report. All log lines are in the report file.
- The login page is explained before you see it, including where the confirmation code comes from.
- A new roadie version shows up in red at every SSH login and at the top of the menu, with what it
  brings and the command to run. The push message names the main changes too.
- The release page on GitHub lists the changes of that version.

## 0.5.1 – 2026-11-18

- A server with a 20 GB disk, the size the guides recommend, was refused: the check wanted 20 GB
  free, and after the system about 16 GB are left. 15 GB free are enough now.
- The stream caches were sized from the free disk space at each run. That space shrinks after the
  setup, so an update could change the sizes and restart AIOStreams. They now follow the disk size.
- The login name is checked as you type it. A name with a space used to pass and only failed at the
  very end, so all questions had to be answered again.
- Two indexers with the same name are refused (roadie finds its indexers in Prowlarr by name).

## 0.5.0 – 2026-11-18

- More than one indexer: roadie asks which are your main ones. AIOStreams searches those first and
  asks the others only when the main ones found fewer than 5 streams in your languages. That saves
  the daily limits of small indexers. Already set up with several indexers? Nothing changes by
  itself: run `sudo roadie`, choose *Change settings* → *Usenet provider and indexer* and pick
  your main indexers.
- Releases with forced subtitles in your languages rank a little higher, for scenes in another
  language.
- Before you add a backup provider, roadie lists the backbones you do not have yet, each with the
  brands that sell access to it.
- A push message when the newest backup was not downloaded within 36 hours (not on Debian 12).
  On servers set up earlier it starts after the next `sudo roadie update`.
- Your own AIOMetadata now also on servers with 2 GB RAM.
- When a setup step fails, roadie points to `sudo roadie diagnose`. The report now names the
  failed step and is also saved as `~/roadie-report.txt`. Nothing is sent automatically.
- After the first setup, one line asks for feedback, a short "worked for me" included. The help
  page *Feedback and problems* has the link, and there is a new issue template *Setup worked!*.

## 0.4.2 – 2026-11-18

- The setup asked for the long TMDB "API Read Access Token". Your own AIOMetadata only works with
  the short "API Key" (32 characters), so it was never set up for anyone who followed the steps.
  The setup now asks for the short key, explains how to get it, and says so when the long token
  is pasted. Already set up with the token? Run `sudo roadie`, choose *Change settings* and enter
  the short key.
- Help and explain are one menu entry now: *Help and how your server works*. `sudo roadie help`
  and `sudo roadie explain` open the same pages. The two pages about getting help are merged into
  *When something does not work*.

## 0.4.1 – 2026-11-17

- `sudo roadie explain` explains the *why* as well: every service and every topic says why it is
  set up this way.
- New topic *When you need help*: check with `status` and `doctor` first, then ask at roadie –
  not in the communities of AIOStreams, Prowlarr, Authelia or Traefik, which do not know this
  setup. With what to include and what never to post.
- New issue template *Question* – no question is too small.

## 0.4.0 – 2026-11-17

- **`sudo roadie explain`** (menu: *Understand your server*): what roadie installed and how to run
  it yourself. Seven topics – how it fits together, the services (page, files, log of each one),
  ports and firewall with the fail2ban rules, where every file lives and which ones roadie writes
  again, the scheduled jobs, working with the containers, and how the server is protected. Uses
  the real names, paths and ports of your server. `sudo roadie explain all > explained.txt`
  saves everything.
- `--lang` now also wins over the setup language for commands such as `status` or `explain`.

## 0.3.2 – 2026-11-17

- The green of the start screen runs through the setup: headings, the bars between the parts,
  check marks and the marker in front of every question.
- The start screen also shows when you run `sudo roadie setup --answers …` in a terminal.
- New GIFs in the README.

## 0.3.1 – 2026-11-17

- A start screen: the roadie roadcase as pixel art and the name in green, shown when you start
  `sudo roadie` or `roadie demo` in a terminal. Narrow windows (phones) show the name only;
  logs and automatic jobs stay plain.

## 0.3.0 – 2026-11-16

New:
- **One-line install:** `curl -fsSL https://raw.githubusercontent.com/usenetroadie/roadie/main/get.sh | sudo bash`
  downloads the newest release, checks checksum and signature, installs it and starts the wizard.
- **`sudo roadie doctor`** tests every Usenet provider and every indexer on its own and adds the
  last 7 days in numbers: streamed data, unplayable releases, whether the backup provider helped.
- **Weekly report** as a push message on Sunday morning.
- **Status page** `status.<your address>` behind your login: services, problems and their fix,
  last backup, updates. Own domain: add a DNS entry `status` (the setup works without it).
- Newer versions of the installed programs are found every week, tested end to end and proposed
  in an issue; an update from the latest release is part of the end-to-end tests.

Fixed:
- "Manage people" replaced the catalogs of your own AIOMetadata with the simple TMDB addon for
  every additional person, until the next `sudo roadie update`.
- The watcher kept reporting "aiometadata is not running" after AIOMetadata was taken down again
  (TMDB key removed, or a restore on a smaller server). "My access" no longer lists it then, and
  its file with the TMDB key is removed.
- Own domain: roadie asks the name servers of your domain directly, so new DNS entries count at
  once instead of up to an hour later. A second service tells the public address behind NAT; if
  none answers, roadie warns instead of blocking. A missing `metadata` entry is an open point,
  no longer a failed setup.
- An outage of DuckDNS or ntfy.sh no longer stops the setup.
- `restart` and `people` wait for a running backup or update.
- "My access" shows the addon password of your own AIOMetadata; the warning before an address
  change mentions passkeys.

## 0.2.0 – 2026-11-16

- **ARM64 servers**, for example the Oracle Cloud *Always Free* tier.
- **Debian 12 and Debian 13** besides Ubuntu 24.04.
- **Your own AIOMetadata** with a TMDB API key (servers with 4 GB RAM or more): catalogs and details
  in your language, settings page behind your login.
- **Your own domain** as an alternative to DuckDNS. roadie shows which DNS entries to create and
  checks them – also behind NAT (Oracle Cloud).
- NZB downloads and indexer searches identify as a normal browser (`REQUEST_HEADER_OVERRIDES`).
- End-to-end tests on every supported system and architecture, with DuckDNS and with an own domain.
- Docs: firewall of the server provider, own domain, changelog.

## 0.1.1 – 2026-11-16

- AIOStreams 2.38.1.
- "1 stream found" instead of "1 streams found".

## 0.1.0 – 2026-11-16

- First release: guided setup of AIOStreams with Usenet (Prowlarr, providers, indexers), Traefik,
  Authelia with passkey, DuckDNS, firewall, fail2ban, automatic updates, push messages,
  encrypted backups, signed self-updates.
