# Advanced

[← back to the README](../README.md)

## Adjust AIOStreams and Prowlarr yourself

roadie sets things up – you stay in charge.

- **AIOStreams:** open the settings page, load your configuration with UUID and config password
  (`sudo roadie access`), change what you like and save. roadie only sets languages, quality (with
  HDR), Usenet streaming, TMDB, sorting and its own addons. With *Change settings* and with updates
  it only sets these parts again; everything else stays, including your own addons.
- **Stream limit** (5 per resolution, 20 in total): *Filters → Result Limits*. roadie sets it only
  the first time.
- **Prowlarr:** open the indexer management (same login), add and test indexers under *Indexers*,
  then run `sudo roadie restart` so AIOStreams knows them. roadie deletes nothing it did not add.

## Your own AIOMetadata

With a TMDB API key (and 4 GB RAM or more) roadie runs its own
[AIOMetadata](https://github.com/cedya77/aiometadata): catalogs (trending, popular, top rated, by year
and language) and details in your first language, instead of the simple TMDB addon. roadie creates
its configuration once; after that it is yours. Open `https://metadata.<your address>/configure`
(same login), load the configuration with the UUID and password from `sudo roadie access`, and add
or remove catalogs, then *Save*. AIOStreams picks the change up within 15 minutes; Stremio and Nuvio
read the list of catalogs only when the addon is installed, so add your addon address once more in
the app afterwards. Posters load straight from TMDB, so your server is not slowed down by them.

## More catalogs

Catalogs are the rows on the start page of your app. roadie sets up trending, the big streaming
services and movie collections; show or hide rows under *Addons → Catalogs*. More catalog addons:

- **AIOMetadata:** many catalogs and better details (needs your own TMDB key).
- **Watchly, MyTrakt, Simkl:** rows from your own watch history.

Set the addon up on its website, copy its address (ends with `manifest.json`), load your
configuration in AIOStreams and add it under *Addons* with *Using an addon that isn't in the
Marketplace?*. roadie leaves it alone.

## Unattended setup

All answers can come from a file:

```sh
sudo roadie setup --answers answers.json      # see examples/answers.example.json
sudo roadie setup --answers answers.json --dry-run   # only check answers and server
```

For your own domain, replace the `duckdns` block with `"domain": "example.com"`.

`people` is optional in an answers file: without it the stored people stay; people left out of
the list are removed and their addon address stops working.

## Uninstall

`sudo roadie uninstall` removes what roadie set up: all services with their data, roadie's settings
and saved passwords, its schedules (watcher, backup, DuckDNS), its fail2ban rules, the firewall
openings for ports 80/443, the automatic restart after updates and the program itself. It asks
first and offers a last backup. The admin user, the SSH protection, your backups and Docker stay.

## Development

```sh
python3 -m unittest discover -s tests      # unit tests, standard library only
```

End-to-end tests run the whole wizard on a fresh machine: see `tests/e2e/README.md` and
`.github/workflows/e2e.yml`. A component version in `roadie/data/versions.json` is only raised
after the end-to-end run passed with it. See also [CONTRIBUTING.md](../CONTRIBUTING.md).
