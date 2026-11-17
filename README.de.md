<p align="center">
  <img src="assets/logo.svg" width="112" alt="roadie-Logo">
</p>

<h1 align="center">roadie</h1>

<p align="center">
  <b>Tagelanges Basteln. Eine geführte Einrichtung.</b><br>
  Ein Terminal-Assistent, der aus einem frischen VPS deinen eigenen, abgesicherten
  <a href="https://github.com/Viren070/AIOStreams">AIOStreams</a>- und Usenet-Streaming-Server macht.
</p>

<p align="center">
  <a href="https://github.com/usenetroadie/roadie/releases"><img src="https://img.shields.io/github/v/release/usenetroadie/roadie?color=3fb950" alt="Release"></a>
  <a href="https://github.com/usenetroadie/roadie/actions/workflows/tests.yml"><img src="https://img.shields.io/github/actions/workflow/status/usenetroadie/roadie/tests.yml?branch=main&label=tests" alt="Tests"></a>
  <a href="https://github.com/usenetroadie/roadie/actions/workflows/e2e.yml"><img src="https://img.shields.io/github/actions/workflow/status/usenetroadie/roadie/e2e.yml?branch=main&label=end-to-end" alt="End-to-end tests"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue" alt="MIT-Lizenz"></a>
  <img src="https://img.shields.io/badge/Ubuntu-24.04-E95420?logo=ubuntu&logoColor=white" alt="Ubuntu 24.04">
  <img src="https://img.shields.io/badge/Debian-12%20%7C%2013-A81D33?logo=debian&logoColor=white" alt="Debian 12 und 13">
  <img src="https://img.shields.io/badge/arch-x86__64%20%7C%20arm64-555" alt="x86_64 und arm64">
</p>

<p align="center">
  <a href="#schnellstart">Schnellstart</a> ·
  <a href="#so-sieht-es-aus">Bilder</a> ·
  <a href="#funktionen">Funktionen</a> ·
  <a href="#anleitungen">Anleitungen</a> ·
  <a href="https://github.com/usenetroadie/roadie/issues">Issues</a> ·
  <a href="README.md">English</a>
</p>

Du beantwortest ein paar einfache Fragen – dein Usenet-Provider, dein Indexer, was du schauen
willst. roadie prüft jede Antwort sofort, richtet alles ein und gibt dir am Ende die
Addon-Adresse für Stremio oder Nuvio.

![Die Fragen im Demo-Modus (es wird nichts verändert)](assets/questions.gif)

<p align="center"><sub>Frühe Version · nur Usenet · braucht einen frischen VPS</sub></p>

## Warum es roadie gibt

Ich betreibe genau dieses Setup auf meinem eigenen kleinen VPS. Bis es lief, hat es Tage gedauert:
Reverse Proxy, ein echter Login vor jeder Einstellungsseite, Usenet-Provider auf verschiedenen
Backbones, eine Firewall, die mich nicht aussperrt, Backups, die sich wirklich zurückspielen lassen.
roadie ist alles, was ich dabei gelernt habe, in einer geführten Einrichtung – damit die Nächsten
eine halbe Stunde brauchen statt eines Wochenendes. Und es macht dich nicht abhängig: AIOStreams
und Prowlarr kannst du jederzeit selbst öffnen und anpassen.

## Funktionen

**Streaming**
- AIOStreams mit eingebautem Usenet-Streaming direkt von deinem Provider, Prowlarr für deine Indexer
- Deine Sprachen zuerst, Ranking zugunsten neuer und vollständiger Releases, Failover, nächste Folge vorgeladen
- Kataloge auf der Startseite ab Werk (Trends, Netflix / Prime / Disney+, Filmreihen) – mit einem
  TMDB-API-Schlüssel richtet roadie dazu dein **eigenes [AIOMetadata](https://github.com/cedya77/aiometadata)**
  ein, für mehr Kataloge und bessere Details in deiner Sprache
- Eine Addon-Adresse pro Person, auf Wunsch mit eigenen Sprachen und eigener Qualität

**Sicherheit**
- Eigene Adresse – kostenlos über DuckDNS oder deine eigene Domain – mit HTTPS von Let's Encrypt
- Login mit Passkey oder Code-App (Authelia) vor jeder Einstellungsseite – deine Player-Apps funktionieren weiter
- SSH-Login per Schlüssel; der Passwort-Login wird erst abgeschaltet, nachdem dein Schlüssel-Login gesehen wurde, damit du dich nicht versehentlich aussperrst
- Firewall, fail2ban (auch für Docker-Ports), automatische Sicherheitsupdates

**Wartung**
- Push-Meldungen aufs Handy (ntfy), wenn etwas nicht stimmt, dazu ein kurzer Wochenbericht
- Eine Status-Seite im Browser hinter deinem Login: läuft alles, letztes Backup, Updates
- `sudo roadie doctor` testet jeden Provider und jeden Indexer einzeln und sagt in einfachen Worten,
  was los ist – und ob dein Backup-Provider wirklich etwas bringt
- Verschlüsselte wöchentliche Backups (age), Wiederherstellung auf einem neuen Server mit einem Befehl
- `sudo roadie update` installiert nur getestete Versionen aus signierten Releases
- Ein Menü für den Alltag: Zugänge, Login-Code, Neustart, Personen, Hilfe
- `sudo roadie explain` zeigt, was installiert wurde und wie es zusammenhängt: jeden Dienst mit Seite, Dateien und Log, die offenen Ports, Firewall- und fail2ban-Regeln, die geplanten Aufgaben und die Docker-Befehle, um alles selbst zu pflegen – in einfachen Worten, mit den echten Pfaden deines Servers

Alles läuft auf deinem eigenen Server. roadie schickt nichts an seine Entwickler – keine Telemetrie.

## Schnellstart

**Du brauchst:**
- einen frischen VPS mit **Ubuntu 24.04, Debian 12 oder Debian 13** – x86_64 oder **ARM64** (z. B. Oracle Cloud kostenlos) –
  2 vCPU, 4 GB RAM empfohlen (mindestens 2 GB), 20 GB Speicher, sonst nichts darauf
- eine Web-Adresse: eine kostenlose [DuckDNS](https://www.duckdns.org)-Subdomain mit Token **oder deine eigene Domain**
  (roadie sagt dir, welche DNS-Einträge nötig sind)
- Zugangsdaten eines Usenet-Providers und mindestens einen Usenet-Indexer (API-Adresse + Schlüssel)
- optional: die App ntfy für Push-Meldungen, einen TMDB-API-Schlüssel

Noch nie einen Server gemietet? [Getting started](docs/getting-started.md) (englisch) führt dich durch.

**Installation** – melde dich auf deinem Server an und führe aus:

```sh
curl -fsSL https://raw.githubusercontent.com/usenetroadie/roadie/main/get.sh | sudo bash
```

Das lädt die neueste Version, prüft Prüfsumme und Signatur, installiert sie und startet den Assistenten.

<details>
<summary>Lieber Schritt für Schritt?</summary>

```sh
V=$(curl -fsSLI -o /dev/null -w '%{url_effective}' https://github.com/usenetroadie/roadie/releases/latest | sed 's|.*/||')
curl -fLO https://github.com/usenetroadie/roadie/releases/download/$V/roadie-$V.tar.gz
curl -fLO https://github.com/usenetroadie/roadie/releases/download/$V/roadie-$V.tar.gz.sha256
sha256sum -c roadie-$V.tar.gz.sha256          # muss "OK" ausgeben
tar xzf roadie-$V.tar.gz && cd roadie-$V
sudo bash install.sh && sudo roadie
```

</details>

Bricht die Verbindung ab oder geht etwas schief, starte einfach erneut `sudo roadie` – es macht
dort weiter, wo es aufgehört hat. Der Assistent spricht Deutsch, wenn du es am Anfang auswählst.

![Eine echte Einrichtung von Anfang bis Ende (im Zeitraffer)](assets/setup.gif)

## So sieht es aus

<table>
  <tr>
    <td width="50%"><a href="assets/login.gif"><img src="assets/screenshot-login.png" alt="Login-Seite mit Passkey"></a></td>
    <td width="50%"><a href="assets/aiostreams.gif"><img src="assets/screenshot-aiostreams.png" alt="AIOStreams-Konfiguration"></a></td>
  </tr>
  <tr>
    <td><b>Anmelden mit einem Tipp.</b> Die Einstellungsseiten liegen hinter deinem eigenen Login – mit Passkey per Face ID oder Fingerabdruck. <a href="assets/login.gif">Ansehen</a></td>
    <td><b>AIOStreams fertig eingerichtet.</b> Prowlarr, Kataloge, deine Sprachen und eine Stream-Grenze. Ändere auf der Website, was du willst; roadie behält deine Änderungen. <a href="assets/aiostreams.gif">Ansehen</a></td>
  </tr>
  <tr>
    <td width="50%"><a href="assets/aiometadata.gif"><img src="assets/screenshot-aiometadata.png" alt="AIOMetadata-Katalogeinstellungen"></a></td>
    <td><b>Deine Kataloge, deine Regeln.</b> Mit einem TMDB-Schlüssel bekommst du dein eigenes AIOMetadata: Trends, Beliebt, Top bewertet und mehr, in deiner Sprache. Reihen hinzufügen, ausblenden oder umsortieren geht jederzeit auf seiner Einstellungsseite. <a href="assets/aiometadata.gif">Ansehen</a></td>
  </tr>
</table>

## So funktioniert es

```mermaid
flowchart LR
    app["Stremio / Nuvio"] -->|HTTPS| traefik["Traefik<br/>Zertifikat"]
    traefik --> aio["AIOStreams"]
    traefik -. Einstellungsseiten .-> authelia["Authelia<br/>Passkey-Login"]
    aio --> prowlarr["Prowlarr"] --> indexers["deine Indexer"]
    aio -->|NNTP| provider["dein Usenet-Provider"]
    roadie["roadie<br/>Wächter · Backups · Updates"] -. Push .-> phone["dein Handy (ntfy)"]
```

Alle Dienste laufen in Docker auf deinem Server. Von außen erreichbar ist nur Traefik (Ports 80/443).
Dein Player fragt AIOStreams nach Streams; AIOStreams sucht über Prowlarr bei deinen Indexern und
streamt die Datei direkt von deinem Usenet-Provider. Mehr dazu: [Security](docs/security.md).

## Anleitungen

Die ausführlichen Anleitungen gibt es auf Englisch:

| Anleitung | Inhalt |
|---|---|
| [Getting started](docs/getting-started.md) | VPS mieten, verbinden, was du vorbereitest, erste Schritte nach der Einrichtung |
| [Commands](docs/commands.md) | Befehle im Alltag, das Menü, mehrere Personen |
| [Backups](docs/backups.md) | Backups, herunterladen, Umzug auf einen neuen Server |
| [Security](docs/security.md) | was wie geschützt ist, signierte Updates |
| [Troubleshooting](docs/troubleshooting.md) | keine Streams, Login-Probleme, Hilfe bekommen |
| [Advanced](docs/advanced.md) | Antwortdateien, AIOStreams und Prowlarr selbst anpassen, mehr Kataloge, Deinstallieren |

Die wichtigsten Befehle: `sudo roadie` (Menü), `sudo roadie status`, `sudo roadie backup`,
`sudo roadie update`, `sudo roadie diagnose`, `sudo roadie uninstall`. Die deutschen Kurzformen
`zugang`, `personen`, `neustart`, `hilfe` und `deinstallieren` funktionieren auch.

## Aktuelle Grenzen

- nur Usenet
- braucht einen Server mit öffentlicher IPv4-Adresse (einen VPS); Hosting zu Hause hinter dem Router geht nicht
- ein Server pro Setup

## Hilfe, Mitmachen, Dank

- **Fragen und Probleme:** frag [hier bei roadie](https://github.com/usenetroadie/roadie/issues/new/choose) – keine Frage ist zu klein, gern auch auf Deutsch. Bitte nicht in den Communitys von AIOStreams, Prowlarr, Authelia oder Traefik: Sie kennen roadies Setup nicht und betreiben ihre Projekte in ihrer Freizeit. Liegt die Ursache wirklich in einem ihrer Programme, meldet roadie es dort mit allem, was sie brauchen. Häng die Ausgabe von `sudo roadie diagnose` an; Passwörter, Schlüssel, IP-Adressen und deine Domain werden entfernt – lies sie trotzdem einmal, bevor du sie postest.
- **Sicherheitsprobleme:** bitte kein öffentliches Issue – siehe [SECURITY.md](SECURITY.md).
- **Was sich geändert hat:** siehe [CHANGELOG.md](CHANGELOG.md).
- **Mitmachen:** siehe [CONTRIBUTING.md](CONTRIBUTING.md).
- **Dank an:** [AIOStreams](https://github.com/Viren070/AIOStreams) von Viren070, [Traefik](https://traefik.io),
  [Authelia](https://www.authelia.com), [Prowlarr](https://prowlarr.com), [age](https://age-encryption.org),
  [ntfy](https://ntfy.sh). Usenet-Backbone-Daten: [usenetalmanac.com](https://usenetalmanac.com) /
  usenet.rexum.space Community-Karte. roadie gehört zu keinem dieser Projekte, auch nicht zu Stremio oder Nuvio.

**Verantwortung:** roadie installiert und konfiguriert nur Software. Es stellt keine Inhalte oder
Quellen für Inhalte bereit, listet oder empfiehlt sie nicht. Du bist selbst dafür verantwortlich,
wie du die Software nutzt, und für die Einhaltung der Gesetze deines Landes und der Bedingungen der
Dienste, die du nutzt.

**Lizenz:** MIT – siehe [LICENSE](LICENSE). Die installierten Programme behalten ihre eigenen Lizenzen.
