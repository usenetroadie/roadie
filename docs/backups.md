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

Now and then copy the newest backup to your phone or computer, for example once a month. The file
is useless without your private backup key, so a cloud drive is a fine place for it.

### In the browser (easiest)

Open your status page `https://status.<your address>` and log in. Under *Download your backup*
tap the file name. On an iPhone choose *Save to Files*, on a computer it lands in Downloads.

### With Termius on the phone

1. In Termius open *Keychain*, tap **+** → *Key* and paste the private SSH key roadie showed you.
2. Open your host, set the username to your admin user and choose that key instead of a password.
3. Connect once. Then open *SFTP*, go to `backups` and download the newest file ending in `.tar.age`.

### With a terminal on a computer

Save the private SSH key as `~/.ssh/roadie` (`chmod 600 ~/.ssh/roadie`), then:

```sh
scp -i ~/.ssh/roadie ADMIN@YOUR-ADDRESS:backups/*.tar.age .
```

roadie notices a complete download (status page or SFTP) and sends a push message when the newest
backup was not downloaded within 36 hours, once per backup. SFTP downloads are only seen where sshd
can log them (not on Debian 12); downloads from the status page are always seen.

## Move to a new server

1. Copy the backup file to the new server, into the home folder of the user you log in with
   (or `/root`), for example with the SFTP view of Termius.
2. Install roadie ([Quick start](../README.md#quick-start)). It finds the backup and asks
   *Restore this backup?*. Say yes and paste your private backup key (it starts with
   `AGE-SECRET-KEY-`). The key is only kept while the backup is unpacked.
3. roadie then sets everything up again from the backup. No questions are asked.

By hand instead: `sudo roadie restore BACKUP-FILE --identity KEY-FILE`, then `sudo roadie` →
*Continue / check everything again*.

Afterwards everything works as before, with the same addon addresses. Point your DuckDNS name at
the new server (roadie does this itself once it runs there).

`restore` refuses to overwrite a running installation unless you add `--overwrite`, and it only
restores files that belong to roadie.
