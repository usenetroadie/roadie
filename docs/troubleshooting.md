# Troubleshooting

[← back to the README](../README.md)

First run `sudo roadie status` (or *Check status* in the menu). It shows which part has a problem.

## The setup stopped

Run `sudo roadie` again. It picks up where it left off and skips what is already done. The error
message names the step; most problems are a wrong key or a service that was briefly unreachable.

## The address does not work

- **Own domain:** roadie names the DNS entry that is missing or points elsewhere. Fix it at your
  domain provider, wait a few minutes, run `sudo roadie` again.
- **No HTTPS certificate:** ports 80 and 443 must be open in your provider's firewall – see
  [Getting started](getting-started.md#open-the-ports-in-your-providers-firewall).
- **DuckDNS:** check name and token on duckdns.org. roadie keeps the address up to date every 5 minutes.

## No streams

- An indexer's **daily limit** may be reached. It resets the next day.
- The **languages** you chose may be too narrow. Add English or *original language* under
  *Change settings*.
- A **new indexer** added in Prowlarr is only used after *Restart services*.
- Many failed or incomplete releases: a backup provider on a **different backbone** helps.

## A service is not running

Choose *Restart services* (`sudo roadie restart`). If that does not help, restart the server – it
is offline for about two minutes: `sudo reboot`.

## Login problems

- **Confirmation code:** `sudo roadie code` shows the newest one.
- **Passkey not working:** wait 5 minutes – roadie's watcher fixes a known Safari issue on its own.
  If it still fails, delete the old passkey in your password app and create it again.
- **Locked out of SSH:** log in through your provider's web console, then check
  `sudo fail2ban-client status sshd`.

## No push messages

Check that you subscribed to the right topic (`sudo roadie access`) in the ntfy app and that
notifications for the app are allowed on your phone.

## Getting help

```sh
sudo roadie diagnose
```

This writes a report without passwords, keys, IP addresses and your domain. Read it once, then
attach it to an [issue](https://github.com/usenetroadie/roadie/issues/new/choose).
