#!/usr/bin/env bash
# roadie in one line:
#   curl -fsSL https://raw.githubusercontent.com/usenetroadie/roadie/main/get.sh | sudo bash
# Downloads the newest release, checks its checksum AND its signature (the same release key
# `sudo roadie update` trusts), installs it and starts the wizard. Nothing is installed if a
# check fails. Prefer to do it by hand? The README lists the single steps.
set -euo pipefail

REPO="usenetroadie/roadie"
RELEASE_KEY="ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIEF/ChmrVlDq7L5y3eDq/y5gOvKXCwadGrK/Nh0FmXZ8"

main() {  # called on the last line: a download that broke off half way runs nothing
    if [ "$(id -u)" -ne 0 ]; then echo "Please run with sudo: curl ... | sudo bash" >&2; exit 1; fi
    local missing=()
    command -v curl >/dev/null || missing+=(curl ca-certificates)
    command -v ssh-keygen >/dev/null || missing+=(openssh-client)
    if [ "${#missing[@]}" -gt 0 ]; then
        apt-get update -q && DEBIAN_FRONTEND=noninteractive apt-get install -y -q "${missing[@]}"
    fi

    local tag tmp name
    tag="$(curl -fsSL "https://api.github.com/repos/$REPO/releases/latest" \
           | sed -n 's/.*"tag_name": *"\([^"]*\)".*/\1/p' | head -n 1)"
    if ! printf '%s' "$tag" | grep -Eq '^v[0-9]+\.[0-9]+\.[0-9]+$'; then
        echo "Could not find the newest roadie release. Try again in a few minutes." >&2; exit 1
    fi
    name="roadie-$tag"
    tmp="$(mktemp -d)"
    trap "rm -rf '$tmp'" EXIT  # expanded now: tmp is local and gone when the trap runs
    cd "$tmp"
    echo "Downloading roadie $tag ..."
    for file in "$name.tar.gz" "$name.tar.gz.sha256" "$name.tar.gz.sig"; do
        curl -fsSLO "https://github.com/$REPO/releases/download/$tag/$file"
    done
    sha256sum -c --quiet "$name.tar.gz.sha256"
    printf 'roadie namespaces="roadie-release" %s\n' "$RELEASE_KEY" > allowed_signers
    if ! ssh-keygen -Y verify -f allowed_signers -I roadie -n roadie-release \
            -s "$name.tar.gz.sig" < "$name.tar.gz" >/dev/null 2>&1; then
        echo "The signature of $name.tar.gz is not from the roadie release key. Nothing was installed." >&2
        exit 1
    fi
    echo "Checksum and signature are fine."
    tar -xzf "$name.tar.gz"
    bash "$name/install.sh"
    cd /
    if [ -z "${ROADIE_NO_START:-}" ] && [ -t 1 ] && (exec < /dev/tty) 2>/dev/null; then
        exec roadie < /dev/tty  # the script came through a pipe: the wizard reads the terminal
    fi
}

main "$@"
