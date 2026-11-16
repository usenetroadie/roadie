# Contributing

Thanks for wanting to help! roadie is small on purpose: Python standard library only, one guided
path, every change tested.

## Bugs and ideas

- **Bug:** open an [issue](https://github.com/usenetroadie/roadie/issues/new/choose) and attach the
  output of `sudo roadie diagnose` (it removes secrets – read it once anyway).
- **Security problem:** do not open a public issue, see [SECURITY.md](SECURITY.md).
- **Idea:** open an issue first, so we can agree on it before you write code.

## Pull requests

1. Keep the change small and focused on one thing.
2. No new dependencies – roadie must run on a fresh Ubuntu with nothing but Python 3.
3. Every text the user sees exists in English **and** German (`roadie/locales/en.json`, `de.json`).
4. Add or adapt a test, then run:

   ```sh
   python3 -m unittest discover -s tests
   ```

5. Changes to the installed services (`roadie/render/`, `roadie/phases/`, `roadie/data/versions.json`)
   need a green end-to-end run: *Actions → e2e → Run workflow* on your fork, or the steps in
   `tests/e2e/README.md`.

Never put real domains, IP addresses, keys or indexer names into code, tests or examples – use
placeholders such as `example.duckdns.org` and `203.0.113.10`.
