# roadie

*Dein Streaming-Roadie: baut die Bühne auf und sorgt dafür, dass die Show läuft.*

[English](README.md) · **Deutsch**

Ein Einrichtungs-Assistent im Terminal, der aus einem **frischen Ubuntu-24.04-Server** einen
sicheren, selbst betriebenen [AIOStreams](https://github.com/Viren070/AIOStreams)-Server für Stremio
und Nuvio macht, der aus dem **Usenet** streamt – aus deinen Antworten, nicht aus einer Vorlage, die
du von Hand anpassen musst.

Du beantwortest einfache Fragen (Sprache, Usenet-Provider und Indexer, Schlüssel, was du
schauen willst). Der Assistent prüft jede Antwort sofort, richtet alles ein und gibt dir am Ende die
Addon-Adresse für Stremio oder Nuvio.

> **Stand: frühe Testversion.** In automatischen Durchläufen auf frischen Ubuntu-24.04-Systemen
> vollständig getestet, aber noch nicht von vielen Leuten auf echten Servern. Bitte Probleme melden
> (siehe *Hilfe*).

**Grenzen heute:** nur Ubuntu 24.04 auf x86_64 (noch kein ARM) · nur DuckDNS-Adresse (noch keine
eigene Domain) · nur Usenet · ein Server pro Setup.

## Was du bekommst

- **Abgesicherter Server:** Admin-Benutzer mit SSH-Schlüssel; Passwort- und Root-Login werden erst
  abgeschaltet, wenn eine Anmeldung per Schlüssel bestätigt ist (du kannst dich nicht aussperren),
  Firewall, Schutz gegen Login-Angriffe und Web-Scanner (fail2ban, auch für Docker-Ports),
  automatische Sicherheitsupdates mit Neustart nachts und nur bei Bedarf.
- **Eigene Adresse** über DuckDNS mit HTTPS-Zertifikat (Let's Encrypt).
- **AIOStreams** mit Usenet (Prowlarr als Indexer-Verwaltung, eingebautes Usenet-Streaming
  direkt von deinem Provider), bewährte Voreinstellungen (Cache, Ranking zugunsten neuer und vollständiger
  Releases, Failover, nächste Folge vorladen), deine Sprachen in deiner Reihenfolge.
- **Login-Schutz** (Authelia: Passwort + Passkey oder Code-App) vor allen Einstellungsseiten –
  deine Player-Apps funktionieren trotzdem.
- **Betrieb:** Push-Meldungen aufs Handy (ntfy), ein Wächter für Dienste, Erreichbarkeit,
  Zertifikat, Festplatte, Speicher und SSH-Anmeldungen, **verschlüsselte wöchentliche Backups**
  (age) mit Wiederherstellungs-Befehl, Kurzstatus beim Login, getestete Updates.

Alles läuft auf deinem eigenen Server. roadie sendet nichts an seine Autoren – keine Telemetrie.

## Bevor du startest

Du brauchst einen kleinen Server, der nur dir gehört. Falls du noch nie einen gemietet hast:

1. Miete bei einem beliebigen Server-Anbieter einen **VPS** (einen virtuellen Server). Wähle
   **Ubuntu 24.04**, **x86_64**, mindestens **2 vCPU**, **4 GB RAM** (empfohlen) und **20 GB**
   Speicher. Sonst sollte nichts darauf laufen.
2. Der Anbieter zeigt dir eine **IP-Adresse** und einen **Benutzernamen** (oft `root` oder `ubuntu`)
   und entweder ein Passwort oder lässt dich einen SSH-Schlüssel hinterlegen.
3. **Erste Verbindung:** Am Handy nimmst du eine SSH-App wie Termius; am PC oder Mac öffnest du ein
   Terminal und gibst `ssh BENUTZER@IP-ADRESSE` ein (zum Beispiel `ssh root@203.0.113.10`) mit dem
   Benutzernamen deines Anbieters. Bestätige die Frage mit `yes` und gib das Passwort ein.
4. Jetzt bist du auf dem Server. Mach unten bei *Installation* weiter und führe dort den
   Installationsbefehl aus.

## Voraussetzungen

- Ein **frischer** VPS mit **Ubuntu 24.04 LTS (x86_64)**, mindestens 2 CPU-Kerne, 2 GB RAM
  (4 GB empfohlen), 20 GB Speicher. Sonst sollte nichts darauf laufen.
- Eine kostenlose [DuckDNS](https://www.duckdns.org)-Subdomain und ihr Token.
- Zugangsdaten eines Usenet-Providers und mindestens ein Usenet-Indexer (API-Adresse + Schlüssel).
- Optional: ein eigener SSH-Schlüssel – hast du keinen, erzeugt der Assistent einen und zeigt ihn einmal an.
- Optional: die App „ntfy“ für Push-Meldungen, ein TMDB-API-Schlüssel.

## Installation

Melde dich auf deinem neuen Server an und führe aus:

```sh
# 1. Release herunterladen (VERSION ersetzen) samt Prüfsumme
curl -fLO https://github.com/usenetroadie/roadie/releases/download/VERSION/roadie-VERSION.tar.gz
curl -fLO https://github.com/usenetroadie/roadie/releases/download/VERSION/roadie-VERSION.tar.gz.sha256
# 2. prüfen – muss "OK" ausgeben
sha256sum -c roadie-VERSION.tar.gz.sha256
# 3. entpacken, installieren, starten
tar xzf roadie-VERSION.tar.gz && cd roadie-VERSION
sudo bash install.sh
sudo roadie
```

Mit `git clone` geht es auch: `git clone https://github.com/usenetroadie/roadie && cd roadie && sudo bash install.sh`.

roadie braucht nur, was Ubuntu schon mitbringt (Python 3). Docker, die Dienste und einige
Systempakete installiert es selbst aus den offiziellen Quellen, in festen, getesteten Versionen.

Bricht die Verbindung ab oder geht etwas schief, starte einfach erneut `sudo roadie` – es
macht dort weiter, wo es aufgehört hat.

**So werden Updates geprüft:** `sudo roadie update` sucht zuerst nach einer neueren
roadie-Version. Sie wird nur installiert, wenn die Prüfsumme stimmt *und* das Release eine
gültige Signatur des Projekt-Schlüssels trägt (OpenSSH `ssh-keygen -Y verify`; der öffentliche
Schlüssel ist in roadie eingebaut). Die neue Version muss erst starten, bevor sie die alte
ersetzt; die alte bleibt in `/opt/roadie/app.prev`. Geht etwas schief, ändert sich nichts. Von Hand zurück: `sudo rm -rf /opt/roadie/app && sudo mv /opt/roadie/app.prev /opt/roadie/app`.

## Befehle im Alltag

| Befehl | Was er tut |
|---|---|
| `sudo roadie` | einrichten, weitermachen oder Einstellungen ändern |
| `sudo roadie status` | Kurzstatus (erscheint auch bei jeder SSH-Anmeldung) |
| `sudo roadie backup` | jetzt ein verschlüsseltes Backup (läuft auch jeden Sonntag nachts) |
| `sudo roadie restore DATEI --identity SCHLÜSSELDATEI` | Backup auf einem frischen Server zurückspielen, danach `sudo roadie` |
| `sudo roadie update` | roadie selbst aktualisieren, dann die getesteten Dienst-Versionen installieren (vorher Backup) |
| `sudo roadie diagnose` | Fehlerbericht ohne Passwörter, IP-Adressen und deine Domain |
| `sudo roadie deinstallieren` | alles entfernen, was der Assistent eingerichtet hat (fragt vorher, bietet ein letztes Backup an; Admin-Benutzer, SSH-Absicherung, Backups und Docker bleiben) |

Nach der Einrichtung öffnet `sudo roadie` ein Menü: *Meine Zugänge anzeigen* (alle Adressen und
Logins, die du später wieder brauchst), Login-Code, Status, Dienste neu starten, Personen,
Einstellungen ändern, Backup, Updates und Hilfe (mit *Selbst anpassen*: wie du Dinge in AIOStreams
änderst, ohne dass der Assistent sie überschreibt – er schreibt nur seine eigenen Abschnitte neu).
Kurzbefehle:

| Kurzbefehl | Öffnet |
|---|---|
| `sudo roadie zugang` | Meine Zugänge anzeigen |
| `sudo roadie code` | einen Login-Code |
| `sudo roadie personen` | Personen hinzufügen oder ändern |
| `sudo roadie neustart` | Dienste neu starten (startet gestoppte; AIOStreams liest neue Indexer ein) |
| `sudo roadie hilfe` | Hilfe-Seiten |

**Mehrere Personen:** Jede Person bekommt eine eigene Addon-Adresse. Sie erbt entweder die
Haupt-Einstellungen oder hat eigene Sprachen und Qualität. *Einstellungen ändern* lässt die Personen,
wie sie sind (ändern unter *Personen verwalten*). In einer Antwortdatei ist `people` freiwillig: Fehlt es, bleiben
die gespeicherten Personen; wer in der Liste fehlt, wird entfernt und seine Addon-Adresse geht nicht
mehr. Die Einrichtung endet mit einer
Testsuche, die zeigt, dass dein Setup wirklich Streams findet; die Meldung bei jeder
SSH-Anmeldung verweist auf `sudo roadie`.

## Backups

Backups werden mit [age](https://age-encryption.org) verschlüsselt. Auf dem Server liegt nur der
**öffentliche** Schlüssel; der **private** bleibt bei dir (hast du keinen, erzeugt der Assistent
einen und zeigt ihn genau einmal an). Die Dateien lädst du aus `~/backups` deines Admin-Benutzers
herunter, z. B. mit einer SFTP-App.

Umzug auf einen neuen Server: roadie dort installieren, `sudo roadie restore` mit
Backup und privatem Schlüssel, dann `sudo roadie` → *Weitermachen / alles erneut prüfen*. Deine Addon-Adresse
bleibt gleich.

## Einrichtung ohne Rückfragen

Für Automatisierung können alle Antworten aus einer Datei kommen:
`sudo roadie setup --answers answers.json` (siehe `examples/answers.example.json`).
`--dry-run` prüft nur Antworten und Server.

## Hilfe

`sudo roadie diagnose` ausführen und die Ausgabe an ein GitHub-Issue anhängen. Passwörter,
Schlüssel, IP-Adressen und deine Domain werden entfernt; trotzdem vor dem Posten einmal durchlesen.

## Sicherheitsprobleme

Sicherheitsproblem gefunden? Bitte kein öffentliches Issue – siehe [SECURITY.md](SECURITY.md).

## Verantwortung

roadie installiert und konfiguriert nur Software. Es stellt keine Inhalte oder Quellen für
Inhalte bereit, listet oder empfiehlt sie nicht. Du bist selbst dafür verantwortlich, wie du die
Software nutzt, und für die Einhaltung der Gesetze deines Landes und der Bedingungen der Dienste,
die du verwendest.

## Lizenz

MIT – siehe [LICENSE](LICENSE). AIOStreams, Traefik, Authelia, Prowlarr und die anderen installierten
Programme behalten ihre eigenen Lizenzen. Backbone-Daten: [usenetalmanac.com](https://usenetalmanac.com)
/ Community-Karte usenet.rexum.space.
