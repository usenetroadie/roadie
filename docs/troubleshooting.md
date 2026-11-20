# Troubleshooting

[← back to the README](../README.md)

First run `sudo roadie status` (or *Check status* in the menu). It shows which part has a problem.
For streaming problems run `sudo roadie doctor`: it tests every Usenet provider and every indexer
on its own and says which one fails and why.

## The setup stopped

Run `sudo roadie` again. It picks up where it left off and skips what is already done. The error
message names the step; most problems are a wrong key or a service that was briefly unreachable.

## The address does not work

- **Own domain:** roadie names the DNS entry that is missing or points elsewhere. Fix it at your
  domain provider and run `sudo roadie` again. roadie asks your domain's own name servers, so a new
  entry counts as soon as your provider has published it.
- **No HTTPS certificate:** ports 80 and 443 must be open in your provider's firewall – see
  [Getting started](getting-started.md#open-the-ports-in-your-providers-firewall).
- **DuckDNS:** check name and token on duckdns.org. roadie keeps the address up to date every 5 minutes.

## No streams

- An indexer's **daily limit** may be reached. It resets the next day.
- The **languages** you chose may be too narrow. Add English or *original language* under
  *Change settings*.
- A **new indexer** added in Prowlarr is only used after *Restart services*.
- Many failed or incomplete releases: a backup provider on a **different backbone** helps.

## An indexer does not answer during the setup

Indexers are down now and then for a few minutes. roadie asks whether to try again or keep the
indexer anyway. A kept indexer is added to Prowlarr as soon as it answers, at the next
`sudo roadie update` or *Change settings*. roadie needs at least one indexer that answers.

## The setup cannot download the programs

Docker Hub or GitHub did not answer or are busy. Wait a few minutes and run `sudo roadie` again;
it goes on where it stopped.

## A service is not running

Choose *Restart services* (`sudo roadie restart`). If that does not help, restart the server – it
is offline for about two minutes: `sudo reboot`.

## Login problems

- **Confirmation code:** `sudo roadie code` shows the newest one. Authelia says it sent it by
  email, but it only lands on your server. After a few tries Authelia makes no new codes for some
  minutes: wait 5 minutes, then choose *Add* again.
- **Passkey not working:** wait 5 minutes – roadie's watcher fixes a known Safari issue on its own.
  If it still fails, delete the old passkey in your password app and create it again.
- **Locked out of SSH:** log in through your provider's web console, then check
  `sudo fail2ban-client status sshd`.

## No push messages

Check that you subscribed to the right topic (`sudo roadie access`) in the ntfy app and that
notifications for the app are allowed on your phone.

## Getting help

Ask [here at roadie](https://github.com/usenetroadie/roadie/issues/new/choose) – choose *Feedback or
question* for ideas and anything unclear, *Bug report* when something is broken, and *Setup
worked!* when it simply worked. No question is too small.

Please do not ask in the communities of AIOStreams, Prowlarr, Authelia or Traefik first. Your
server is set up the roadie way – its own login in front, its own firewall rules, fixed versions,
settings that roadie writes. The people there do not know this setup and run their projects in
their free time. If the cause really lies in one of their programs, roadie reports it there with
everything they need to fix it.

Attach the problem report:

```sh
sudo roadie diagnose
```

It shows the report and also saves it as `~/roadie-report.txt` of your admin user, to download or
copy. It leaves out passwords, keys, IP addresses and your domain. Read it once before posting
anyway. Nothing is sent automatically.
Never post your addon address, passwords, API keys or your backup key.

To understand what roadie set up and how to look after it yourself: `sudo roadie explain`.
