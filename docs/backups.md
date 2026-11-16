# Backups

[← back to the README](../README.md)

## How backups work

- Every Sunday early morning roadie makes a backup; `sudo roadie backup` makes one right away.
- Backups are encrypted with [age](https://age-encryption.org). Only the **public** key is on the
  server; the **private** key stays with you. A stolen server or backup cannot be opened without it.
- The apps stop for a few seconds during the backup; your address stays reachable.
- The two newest backups are kept in `~/backups` of your admin user.
- The backup contains roadie's answers and secrets and the data of all services – not the program
  itself and not the stream cache.

If you had no backup key, roadie created one during the setup and showed the private key **once**.
Keep it in your password manager.

## Download your backups

Now and then copy the newest backup to your phone or computer, for example once a month.

- **Termius:** connect to your server, open SFTP and download the newest file ending in `.tar.age`.
- **Terminal on a computer:** `scp ADMIN@YOUR-ADDRESS:backups/*.tar.age .`

## Move to a new server

1. Install roadie on the new server ([Quick start](../README.md#quick-start)).
2. Copy the backup and your backup key file to it.
3. Run `sudo roadie restore BACKUP-FILE --identity KEY-FILE`.
4. Run `sudo roadie` → *Continue / check everything again*.

Afterwards everything works as before, with the same addon addresses. Point your DuckDNS name at
the new server (roadie does this itself once it runs there).

`restore` refuses to overwrite a running installation unless you add `--overwrite`, and it only
restores files that belong to roadie.
