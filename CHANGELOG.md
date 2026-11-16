# Changelog

All notable changes. Versions follow [semantic versioning](https://semver.org).

## 0.2.0 – unreleased

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
