#!/usr/bin/env python3
"""Look for newer images of the pinned components (roadie/data/versions.json).

Asks the image registries for their tags, picks the highest plain release tag per component
and, with --write, puts it into versions.json as not yet tested. Prints one line per change
("name: old -> new"); no output means everything is current. Standard library only.

Used by the weekly workflow .github/workflows/versions.yml, which then runs the end-to-end
tests with the new versions and opens a pull request. Nothing is ever installed untested."""

import json
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

VERSIONS = Path(__file__).resolve().parents[2] / "roadie/data/versions.json"
# Only tags of exactly this shape count: no rc/beta/nightly, no moving tags like "latest".
PATTERNS = {
    "traefik": r"v(\d+)\.(\d+)\.(\d+)",
    "authelia": r"(\d+)\.(\d+)\.(\d+)",
    "aiostreams": r"v(\d+)\.(\d+)\.(\d+)",
    "prowlarr": r"release-(\d+)\.(\d+)\.(\d+)\.(\d+)",
    "aiometadata": r"(\d+)\.(\d+)\.(\d+)",
    "aiometadata-redis": r"(\d+)\.(\d+)\.(\d+)-alpine",
    "status": r"1\.36\.(\d+)",  # busybox: the stable line (odd minor versions are unstable)
}
MAX_PAGES = 200


def _get(url: str, headers: dict | None = None):
    req = urllib.request.Request(url, headers={"User-Agent": "roadie-version-check", **(headers or {})})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read()), resp.headers


def registry_of(image: str) -> tuple[str, str, str]:
    """(registry API, token URL, repository) for an image name as compose takes it."""
    if image.startswith("ghcr.io/"):
        repo = image.split("/", 1)[1]
        return "https://ghcr.io", f"https://ghcr.io/token?scope=repository:{repo}:pull", repo
    repo = image if "/" in image else f"library/{image}"
    return ("https://registry-1.docker.io",
            f"https://auth.docker.io/token?service=registry.docker.io&scope=repository:{repo}:pull", repo)


def tags(image: str) -> list[str]:
    """Every tag of the image (anonymous pull token, paged)."""
    api, token_url, repo = registry_of(image)
    auth = {"Authorization": "Bearer " + _get(token_url)[0]["token"]}
    out, url = [], f"{api}/v2/{repo}/tags/list?n=1000"
    for _ in range(MAX_PAGES):
        data, headers = _get(url, auth)
        out += data.get("tags") or []
        m = re.search(r'<([^>]+)>;\s*rel="next"', headers.get("Link") or "")
        if not m:
            break
        url = urllib.parse.urljoin(api, m.group(1))
    return out


def newest(found: list[str], pattern: str) -> str | None:
    """The tag with the highest version among those of exactly the pattern's shape."""
    best = None
    for tag in found:
        m = re.fullmatch(pattern, tag)
        if m:
            key = tuple(int(g) for g in m.groups())
            if best is None or key > best[0]:
                best = (key, tag)
    return best[1] if best else None


def is_newer(candidate: str, current: str, pattern: str) -> bool:
    new, old = re.fullmatch(pattern, candidate), re.fullmatch(pattern, current)
    if not new:
        return False
    return not old or tuple(map(int, new.groups())) > tuple(map(int, old.groups()))


def changes(components: dict, list_tags=tags) -> list[tuple[str, str, str]]:
    """(component, pinned tag, newer tag). A registry that does not answer is skipped with a
    note on stderr: the next weekly run tries again."""
    out = []
    for name, entry in components.items():
        pattern = PATTERNS.get(name)
        if not pattern:
            print(f"{name}: no tag pattern, skipped", file=sys.stderr)
            continue
        try:
            best = newest(list_tags(entry["image"]), pattern)
        except Exception as exc:  # network, registry format: never fail the whole check
            print(f"{name}: registry not readable ({type(exc).__name__})", file=sys.stderr)
            continue
        if best and is_newer(best, entry["tag"], pattern):
            out.append((name, entry["tag"], best))
    return out


def apply(text: str, found: list[tuple[str, str, str]]) -> str:
    """versions.json with the new tags, marked as not yet tested; layout untouched."""
    for name, old, new in found:
        line = re.compile(r'("%s":\s*\{[^}]*"tag":\s*)"%s"([^}]*"tested":\s*)(?:true|false)'
                          % (re.escape(name), re.escape(old)))
        text, count = line.subn(lambda m: f'{m.group(1)}"{new}"{m.group(2)}false', text)
        if count != 1:
            raise ValueError(f"{name}: entry not found in versions.json")
    return text


def main(argv: list[str]) -> int:
    text = VERSIONS.read_text()
    found = changes(json.loads(text)["components"])
    for name, old, new in found:
        print(f"{name}: {old} -> {new}")
    if found and "--write" in argv:
        VERSIONS.write_text(apply(text, found))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
