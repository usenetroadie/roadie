# Commands

[← back to the README](../README.md)

## Everyday commands

| Command | What it does |
|---|---|
| `sudo roadie` | set up, continue, or (after the setup) open the menu |
| `sudo roadie status` | short status (also shown at every SSH login) |
| `sudo roadie access` | all addresses and logins you need again |
| `sudo roadie code` | the login confirmation code (for passkey or code app setup) |
| `sudo roadie people` | manage people (each gets their own addon address) |
| `sudo roadie doctor` | test every Usenet provider and indexer on its own, plus the last 7 days in numbers |
| `sudo roadie restart` | restart services (starts stopped ones; AIOStreams reads new indexers) |
| `sudo roadie explain` | how your server works: services, ports, files, scheduled jobs, and why (`sudo roadie explain all > explained.txt` saves it all) |
| `sudo roadie help` | help pages, including *Adjust it yourself* |
| `sudo roadie backup` | encrypted backup now (also runs every Sunday night) |
| `sudo roadie restore FILE --identity KEYFILE` | restore a backup on a fresh server, then run `sudo roadie` |
| `sudo roadie update` | update roadie itself, then install the tested service versions (backup first) |
| `sudo roadie diagnose` | problem report without secrets, IP addresses or your domain |
| `sudo roadie uninstall` | remove everything roadie set up (asks first, offers a last backup) |
| `roadie demo` | try the questions without changing anything |

German shortcuts work as well: `zugang` (access), `personen` (people), `neustart` (restart),
`hilfe` (help), `erklaeren` (explain), `deinstallieren` (uninstall).

## The menu

After the setup, `sudo roadie` opens a menu: *Show my access details*, the login confirmation
code, status, *Restart services*, *Manage people*, *Change settings*, backup, updates and help.

*Change settings* shows all your answers first and asks only the part you pick again, prefilled
with the current values. The admin user stays; a new SSH key is added, old ones are kept.

## Status page and weekly report

`https://status.<your address>` shows, behind the same login, whether everything runs, the last
backup and available updates. Every Sunday morning a push message sums up the week. What runs when:
`sudo roadie explain jobs`.

## Several people

Everyone gets their own addon address. A person either inherits the main settings or has their
own languages and quality. *Change settings* keeps the people as they are – change them under
*Manage people*. Removing a person stops their addon address.
