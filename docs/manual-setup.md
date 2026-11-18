# Build it yourself: the roadie setup by hand

[← back to the README](../README.md)

roadie sets up a secure AIOStreams + Usenet server in about half an hour. This guide builds **the
same server by hand**, step by step: every command, every configuration file, and why each part
is there. The files below are generated from roadie itself, so they are exactly what roadie
writes – only with placeholders instead of your values.

Use it if you want to build your server yourself, or to understand what roadie did on yours.
Plan an afternoon (2–4 hours). You need no prior knowledge, only patience and care with copying.

**Conventions**

- `example.com` stands for your domain, `203.0.113.10` for your server's IP address,
  `streamadmin` for your admin user. Replace them everywhere.
- Values in capitals such as `YOUR_USENET_PASSWORD` are placeholders. The guide tells you how to
  fill in each one.
- Commands start with `sudo` where they need administrator rights. Run them one by one and read
  what they print.
- To create a file: `sudo nano PATH`, paste the content, save with **Ctrl+O**, **Enter**, close
  with **Ctrl+X**.

**Contents**

- [Part 1 – Prepare](#part-1--prepare): server, address, accounts
- [Part 2 – Secure the server](#part-2--secure-the-server): admin user, firewall, fail2ban, updates
- [Part 3 – The services](#part-3--the-services): Docker, Traefik, Authelia, AIOStreams, Prowlarr, AIOMetadata
- [Part 4 – Run it](#part-4--run-it): SSH lock-down, watching, backups, updates
- [Where to get help](#where-to-get-help)

---

## Part 1 – Prepare

### 1. Rent a server

A VPS (virtual private server) is a small server in a data center that is yours alone. Any
provider with a public IPv4 address works – for example OVH, Hetzner, Netcup or IONOS. Choose:

- **Ubuntu 24.04 LTS** (this guide; Debian 12/13 work the same with the noted differences)
- **2 vCPU, 4 GB RAM, 20 GB disk** or more. 2 GB RAM works too, with swap.
- nothing else installed on it

The provider gives you an IP address and a first login (often `root` or `ubuntu`, with a
password or an SSH key). Some providers put a firewall of their own in front of the server – if
yours has one, open the ports **22, 80 and 443** there.

> Oracle Cloud has a free tier that is big enough, but signing up and getting a server there is
> often harder than renting one for a few euros a month.

Log in from your computer's terminal (Windows, macOS and Linux all have `ssh`) or from an SSH
app such as Termius:

```sh
ssh root@203.0.113.10
```

**Why a VPS and not at home?** A VPS has a fixed public address and a fast connection in both
directions, and nothing at your home is exposed to the internet.

### 2. A web address

Your apps reach the server by name, and HTTPS needs a name. You need four of them:

| Name | For |
|---|---|
| `aiostreams.example.com` | your apps, and the AIOStreams settings |
| `auth.example.com` | the login page |
| `prowlarr.example.com` | the indexer manager |
| `metadata.example.com` | AIOMetadata's settings (only with a TMDB key) |

**Your own domain:** at your domain provider, create an **A record** for each name pointing to
your server's IP address – or one wildcard record `*.example.com`.

**Free DuckDNS name:** sign in at [duckdns.org](https://www.duckdns.org), create a name
(e.g. `myname`) and note the **token** at the top of the page. DuckDNS answers for every name
below yours, so `aiostreams.myname.duckdns.org` works without further records. Use
`myname.duckdns.org` wherever this guide says `example.com`. You keep the address up to date in
[step 18](#18-keep-a-duckdns-address-up-to-date).

Check that a name points to your server (it can take a few minutes after creating it):

```sh
getent hosts aiostreams.example.com
```

### 3. Accounts you need

- **A Usenet provider:** server address, port (563 for TLS), username, password, number of
  connections. Backup providers only help if they are on a *different backbone* than your main
  provider – otherwise they miss the same articles.
- **At least one indexer:** its API address and your API key (in your account, often under *API*).
- **Optional – a TMDB API key** (free at themoviedb.org, the short 32-character *API key*): for
  your own catalogs with AIOMetadata.
- **Optional – the ntfy app** on your phone for push messages ([ntfy.sh](https://ntfy.sh)).
- **A password manager** for the passwords and keys you create on the way.

---

## Part 2 – Secure the server

### 4. Basic packages and swap

```sh
sudo apt update && sudo apt full-upgrade -y
sudo apt install -y ufw fail2ban python3-systemd unattended-upgrades age curl ca-certificates gnupg sqlite3
```

What they are: `ufw` the firewall, `fail2ban` blocks attackers, `unattended-upgrades` installs
security updates, `age` encrypts backups, `sqlite3` reads small databases.

A **swap file** is disk space the system uses when memory runs short. Without it, a busy moment
can make the system stop a service. Create 2 GB:

```sh
sudo fallocate -l 2G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

### 5. Your admin user and SSH key

Do not work as `root`: a typo as root can break everything, and `root` is the first name every
attacker tries. Create your own user who may use `sudo`:

```sh
sudo adduser streamadmin
sudo usermod -aG sudo streamadmin
```

An **SSH key** replaces the password: a pair of files, the private one stays on your computer, the
public one goes to the server. It cannot be guessed. If you have none yet, create it **on your
computer** (not on the server):

```sh
ssh-keygen -t ed25519
```

Show the public part on your computer and copy it:

```sh
cat ~/.ssh/id_ed25519.pub
```

On the server, give it to your new user – paste your public key between the quotes:

```sh
sudo mkdir -p /home/streamadmin/.ssh
echo 'ssh-ed25519 AAAA... you@computer' | sudo tee /home/streamadmin/.ssh/authorized_keys
sudo chown -R streamadmin:streamadmin /home/streamadmin/.ssh
sudo chmod 700 /home/streamadmin/.ssh
sudo chmod 600 /home/streamadmin/.ssh/authorized_keys
```

**Test it in a second window before you go on** – keep the first one open:

```sh
ssh streamadmin@203.0.113.10
sudo whoami
```

It must log you in without a password and print `root`. From now on work as `streamadmin`.
Passwords are switched off later ([step 16](#16-ssh-keys-only)), once you are sure the key works.

### 6. Firewall

A firewall closes every door (port) of the server you did not open on purpose. Three are needed:
22 for SSH, 80 and 443 for the web. **Allow SSH before you switch the firewall on**, or you lock
yourself out:

```sh
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw limit 22/tcp
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw --force enable
sudo ufw status verbose
```

`limit` instead of `allow` slows down an address that opens many SSH connections in a row.

**Good to know:** Docker opens the ports of its containers *past* UFW – UFW never sees them. That
is why only one container in this setup publishes ports (Traefik, 80 and 443), and why fail2ban
blocks web attackers in Docker's own firewall chain (`DOCKER-USER`), see the next step.

### 7. fail2ban

Bots try to log in to every server on the internet, day and night. fail2ban reads the logs and
blocks an address after a few failed tries, a little longer each time. Four rules:

- **sshd:** 3 failed SSH logins in 10 minutes → 1 hour, up to 1 week
- **sshd-invalid:** 3 tries with a user name that does not exist → 1 hour, up to 4 weeks
- **traefik-scanner:** bots probing the web server for weak spots (`/.env`, `wp-login`, `.php`) →
  1 day, up to 4 weeks – blocked in `DOCKER-USER`, because these requests go to Docker
- **recidive:** blocked 3 times in a day → 1 week on every port

Create the rules:

<!-- file: /etc/fail2ban/jail.d/stack.conf -->
```ini
[DEFAULT]
bantime.increment = true
# never banned: the server itself (add your own IP address here if it never changes)
ignoreip = 127.0.0.1/8 ::1

[sshd]
enabled = true
backend = systemd
port = 22
mode = aggressive
maxretry = 3
findtime = 10m
bantime = 1h
bantime.maxtime = 1w

# unknown or not allowed user names (scanners try many): 3 within 10 minutes, so one
# typo or an old saved login profile does not lock the admin out
[sshd-invalid]
enabled = true
backend = systemd
port = 22
filter = stack-sshd-invalid
maxretry = 3
findtime = 10m
bantime = 1h
bantime.maxtime = 4w

# Docker-published ports bypass the INPUT chain, so web bans go into DOCKER-USER
[traefik-scanner]
enabled = true
backend = auto
filter = stack-traefik-scanner
logpath = /opt/stack/data/traefik/access.log
maxretry = 2
findtime = 10m
bantime = 1d
bantime.maxtime = 4w
action = iptables-allports[name=traefik-scanner, chain=DOCKER-USER]

[recidive]
enabled = true
maxretry = 3
findtime = 1d
bantime = 1w
```

The two filters that recognize the attempts:

<!-- file: /etc/fail2ban/filter.d/stack-sshd-invalid.conf -->
```ini
[INCLUDES]
before = common.conf

[Definition]
_daemon = sshd
failregex = ^%(__prefix_line)sInvalid user .* from <HOST> port \d+\s*$
            ^%(__prefix_line)sUser \S+ from <HOST> not allowed because .*$
            ^%(__prefix_line)sConnection closed by invalid user .* <HOST> port \d+ \[preauth\]\s*$
ignoreregex =
journalmatch = _SYSTEMD_UNIT=ssh.service + _COMM=sshd
```

<!-- file: /etc/fail2ban/filter.d/stack-traefik-scanner.conf -->
```ini
[Definition]
failregex = ^<HOST> \S+ \S+ \[[^]]*\] "[A-Z]+ (?:/\.env|/\.git|/\.aws|/wp-|/wordpress|/xmlrpc|/phpmyadmin|/pma|/vendor/|/cgi-bin/|/boaform|/actuator|/HNAP1|/owa/|[^ "?]*\.(?:php|asp|aspx|jsp|cgi)(?:[?/][^ "]*)?(?= ))[^ "]* [^"]*" 4\d\d 
ignoreregex =
```

The web rule reads Traefik's log, which exists only from step 13 on. Create it empty now, then
start fail2ban:

```sh
sudo mkdir -p /opt/stack/data/traefik
sudo touch /opt/stack/data/traefik/access.log
sudo systemctl enable --now fail2ban
sudo systemctl restart fail2ban
sudo fail2ban-client status
```

Who is blocked, and how to unblock an address (for example your own after typos):

```sh
sudo fail2ban-client status sshd
sudo fail2ban-client set sshd unbanip 203.0.113.99
```

### 8. Automatic security updates

Most break-ins use holes that were fixed long ago on servers nobody updated. This installs
security updates every day and restarts the server at night, only when an update needs it:

<!-- file: /etc/apt/apt.conf.d/99zz-auto-updates -->
```
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
Unattended-Upgrade::Remove-Unused-Kernel-Packages "true";
Unattended-Upgrade::Remove-Unused-Dependencies "true";
Unattended-Upgrade::Automatic-Reboot "true";
Unattended-Upgrade::Automatic-Reboot-WithUsers "true";
Unattended-Upgrade::Automatic-Reboot-Time "05:00";
```

The name starts with `99zz` so it is read last and wins over the system's defaults. Check:

```sh
sudo unattended-upgrade --dry-run --debug 2>&1 | tail -5
```

---

## Part 3 – The services

### How it fits together

Every program runs in its own **container** – a sealed box with the program and everything it
needs. **Docker** starts the boxes and connects them in a private network called `stack`.

When you press play, your app asks `https://aiostreams.example.com` for streams. **Traefik**, the
only container reachable from the internet, receives the request and passes it to **AIOStreams**.
AIOStreams asks **Prowlarr**, Prowlarr asks your indexers, and AIOStreams streams the file
straight from your Usenet provider. Every settings page is guarded by **Authelia**: before Traefik
lets you in, Authelia checks your login.

### 9. Install Docker

Install Docker from Docker's own package source, so you get current versions and updates. The
signing key proves the packages really come from Docker – check its fingerprint before you trust
it:

```sh
sudo install -m 0755 -d /etc/apt/keyrings
. /etc/os-release
sudo curl -fsSL "https://download.docker.com/linux/$ID/gpg" -o /etc/apt/keyrings/docker.asc
gpg --show-keys /etc/apt/keyrings/docker.asc
```

The output must contain `9DC858229FC7DD38854AE2D88D81803C0EBFCD88`. If not, stop and delete the
file. Then:

```sh
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/$ID $VERSION_CODENAME stable" | sudo tee /etc/apt/sources.list.d/docker.list
sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
sudo docker run --rm hello-world
```

**A few words about Docker.** An *image* is the packed program (e.g. `ghcr.io/viren070/aiostreams`
in version `v2.38.1`). A *container* is a running copy of an image. **Docker Compose** builds all
containers from one file, `compose.yaml`. Containers forget everything when they are rebuilt –
except folders that are *mounted* from the server into them (`volumes:`). That is where all data
lives, so it survives updates.

### 10. The stack folder and its secrets

Everything lives in `/opt/stack`, readable only by root, because it holds passwords:

```sh
sudo mkdir -p /opt/stack/{config/traefik,config/authelia,env,secrets/authelia}
sudo mkdir -p /opt/stack/data/{traefik,authelia,aiostreams,prowlarr,aiometadata,aiometadata-redis}
sudo chown 1000:1000 /opt/stack/data/prowlarr
sudo chmod 700 /opt/stack
cd /opt/stack
```

Prowlarr runs as user 1000 inside its container, so its folder belongs to that user.

### 11. compose.yaml

The list of containers: which image in which version, a memory limit, a health check (Docker
asks the program every 30 seconds whether it is fine), and which folders it gets. Only Traefik
publishes ports – and only on IPv4 (`0.0.0.0`), see the note at the end of this step.

<!-- file: compose.yaml -->
```yaml
name: "stack"
services:
  traefik:
    image: "traefik:v3.7.18"
    restart: "always"
    mem_limit: "256m"
    logging:
      driver: "json-file"
      options:
        max-size: "10m"
        max-file: "3"
    networks:
      - "stack"
    healthcheck:
      test:
        - "CMD"
        - "traefik"
        - "healthcheck"
        - "--ping"
      interval: "30s"
      timeout: "10s"
      retries: 3
      start_period: "60s"
    ports:
      - "0.0.0.0:80:80"
      - "0.0.0.0:443:443"
    environment:
      TZ: "UTC"
    volumes:
      - "./config/traefik:/etc/traefik:ro"
      - "./data/traefik:/data"
  authelia:
    image: "ghcr.io/authelia/authelia:4.39.28"
    restart: "always"
    mem_limit: "256m"
    logging:
      driver: "json-file"
      options:
        max-size: "10m"
        max-file: "3"
    networks:
      - "stack"
    healthcheck:
      test:
        - "CMD"
        - "/app/healthcheck.sh"
      interval: "30s"
      timeout: "10s"
      retries: 3
      start_period: "60s"
    environment:
      TZ: "UTC"
    env_file:
      - "env/authelia.env"
    volumes:
      - "./config/authelia:/config"
      - "./secrets/authelia:/secrets:ro"
      - "./data/authelia:/data"
  aiostreams:
    image: "ghcr.io/viren070/aiostreams:v2.38.1"
    restart: "always"
    mem_limit: "1536m"
    logging:
      driver: "json-file"
      options:
        max-size: "10m"
        max-file: "3"
    networks:
      - "stack"
    healthcheck:
      test:
        - "CMD"
        - "/nodejs/bin/node"
        - "-e"
        - "fetch('http://127.0.0.1:3000/api/v1/health').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"
      interval: "30s"
      timeout: "10s"
      retries: 3
      start_period: "60s"
    environment:
      TZ: "UTC"
    env_file:
      - "env/aiostreams.env"
    volumes:
      - "./data/aiostreams:/app/data"
  prowlarr:
    image: "ghcr.io/hotio/prowlarr:release-2.7.0.5723"
    restart: "always"
    mem_limit: "512m"
    logging:
      driver: "json-file"
      options:
        max-size: "10m"
        max-file: "3"
    networks:
      - "stack"
    healthcheck:
      test:
        - "CMD"
        - "curl"
        - "-fsS"
        - "http://127.0.0.1:9696/ping"
      interval: "30s"
      timeout: "10s"
      retries: 3
      start_period: "60s"
    environment:
      TZ: "UTC"
      PUID: "1000"
      PGID: "1000"
      UMASK: "002"
    env_file:
      - "env/prowlarr.env"
    volumes:
      - "./data/prowlarr:/config"
  aiometadata:
    image: "ghcr.io/cedya77/aiometadata:3.9.0"
    restart: "always"
    mem_limit: "768m"
    logging:
      driver: "json-file"
      options:
        max-size: "10m"
        max-file: "3"
    networks:
      - "stack"
    healthcheck:
      test:
        - "CMD"
        - "wget"
        - "--no-verbose"
        - "--tries=1"
        - "--spider"
        - "http://127.0.0.1:3232/health/ready"
      interval: "30s"
      timeout: "10s"
      retries: 3
      start_period: "60s"
    environment:
      TZ: "UTC"
    env_file:
      - "env/aiometadata.env"
    volumes:
      - "./data/aiometadata:/app/addon/data"
    init: true
    depends_on:
      aiometadata-redis:
        condition: "service_healthy"
  aiometadata-redis:
    image: "redis:8.10.3-alpine"
    restart: "always"
    mem_limit: "256m"
    logging:
      driver: "json-file"
      options:
        max-size: "10m"
        max-file: "3"
    networks:
      - "stack"
    healthcheck:
      test:
        - "CMD"
        - "redis-cli"
        - "ping"
      interval: "30s"
      timeout: "10s"
      retries: 3
      start_period: "60s"
    environment:
      TZ: "UTC"
    volumes:
      - "./data/aiometadata-redis:/data"
    command:
      - "redis-server"
      - "--appendonly"
      - "yes"
      - "--save"
      - "3600"
      - "1"
      - "--maxmemory"
      - "200mb"
      - "--maxmemory-policy"
      - "volatile-lru"
networks:
  stack:
    name: "stack"
```

**Without a TMDB key**, delete the two blocks `aiometadata:` and `aiometadata-redis:`.

**Why fixed versions?** A new version can change settings or behavior. Using `latest` or a tool
that updates containers on its own (Watchtower) means your server can change overnight without
you knowing why. Here you decide when to update ([step 20](#20-updates)).

**Why only IPv4?** If your domain has no IPv6 (AAAA) record, nobody reaches the server over IPv6
anyway. Publishing on IPv6 as well would make Docker pass IPv6 visitors on with an internal
address, and fail2ban could not block them.

### 12. Traefik – the front door

Traefik receives every request on ports 80 and 443, gets the HTTPS certificate from Let's Encrypt
and renews it, and sends each request on by its name.

How Traefik starts:

<!-- file: config/traefik/traefik.yml -->
```yaml
global:
  checkNewVersion: false
  sendAnonymousUsage: false
log:
  level: "WARN"
accessLog:
  filePath: "/data/access.log"
ping:
  entryPoint: "ping"
entryPoints:
  web:
    address: ":80"
    http:
      redirections:
        entryPoint:
          to: "websecure"
          scheme: "https"
  websecure:
    address: ":443"
    http:
      middlewares:
        - "security-headers@file"
      encodedCharacters:
        allowEncodedSlash: true
        allowEncodedBackSlash: true
        allowEncodedSemicolon: true
        allowEncodedPercent: true
        allowEncodedQuestionMark: true
        allowEncodedHash: true
  ping:
    address: "127.0.0.1:8082"
providers:
  file:
    filename: "/etc/traefik/routes.yml"
    watch: true
certificatesResolvers:
  letsencrypt:
    acme:
      storage: "/data/acme.json"
      tlsChallenge: {}
```

- Port 80 only redirects to HTTPS. Let's Encrypt checks your name over port 443 (`tlsChallenge`).
- The *access log* writes one line per request. fail2ban reads it (step 7).
- `encodedCharacters`: AIOStreams puts file names into its web addresses. Traefik blocks some
  encoded characters by default; these lines allow them. Authelia still guards the settings pages.
- No dashboard, no version check, no usage statistics.

Which name goes where, the login check and security headers:

<!-- file: config/traefik/routes.yml -->
```yaml
http:
  routers:
    aiostreams:
      rule: "Host(`aiostreams.example.com`)"
      entryPoints:
        - "websecure"
      service: "aiostreams"
      tls:
        certResolver: "letsencrypt"
      middlewares:
        - "authelia"
    authelia:
      rule: "Host(`auth.example.com`)"
      entryPoints:
        - "websecure"
      service: "authelia"
      tls:
        certResolver: "letsencrypt"
    prowlarr:
      rule: "Host(`prowlarr.example.com`)"
      entryPoints:
        - "websecure"
      service: "prowlarr"
      tls:
        certResolver: "letsencrypt"
      middlewares:
        - "authelia"
    metadata:
      rule: "Host(`metadata.example.com`)"
      entryPoints:
        - "websecure"
      service: "metadata"
      tls:
        certResolver: "letsencrypt"
      middlewares:
        - "authelia"
  middlewares:
    authelia:
      forwardAuth:
        address: "http://authelia:9091/api/authz/forward-auth"
        trustForwardHeader: true
        authResponseHeaders:
          - "Remote-User"
          - "Remote-Groups"
          - "Remote-Email"
          - "Remote-Name"
    security-headers:
      headers:
        stsSeconds: 31536000
        stsIncludeSubdomains: true
        contentTypeNosniff: true
        customFrameOptionsValue: "SAMEORIGIN"
        referrerPolicy: "strict-origin-when-cross-origin"
  services:
    aiostreams:
      loadBalancer:
        servers:
          - url: "http://aiostreams:3000"
    authelia:
      loadBalancer:
        servers:
          - url: "http://authelia:9091"
    prowlarr:
      loadBalancer:
        servers:
          - url: "http://prowlarr:9696"
    metadata:
      loadBalancer:
        servers:
          - url: "http://aiometadata:3232"
```

**Why a file instead of Docker labels?** Many guides let Traefik read its routes from Docker
itself, which needs access to Docker's control socket. Whoever controls that socket controls the
whole server. With a file, Traefik needs no such access. It notices changes to `routes.yml` by
itself.

### 13. Authelia – the login

Authelia asks for your password plus a second factor – a passkey (Face ID, fingerprint) or a code
app – before Traefik opens a settings page. Your player apps cannot log in, so the paths they need
are open – only for reading (`GET`, `HEAD`, `OPTIONS`), and the configuration pages are excluded
first.

<!-- file: config/authelia/configuration.yml -->
```yaml
theme: "auto"
server:
  address: "tcp://:9091/"
  endpoints:
    authz:
      forward-auth:
        implementation: "ForwardAuth"
        authn_strategies:
          - name: "CookieSession"
log:
  level: "info"
totp:
  issuer: "example.com"
webauthn:
  disable: false
  display_name: "roadie"
  enable_passkey_login: true
  experimental_enable_passkey_uv_two_factors: true
  selection_criteria:
    discoverability: "required"
    user_verification: "required"
authentication_backend:
  file:
    path: "/config/users.yml"
access_control:
  default_policy: "deny"
  rules:
    - domain:
        - "aiostreams.example.com"
      resources:
        - "^/stremio/configure"
        - "^/stremio/[^/]+/[^/]+/configure"
      policy: "two_factor"
    - domain:
        - "aiostreams.example.com"
      methods:
        - "GET"
        - "HEAD"
        - "OPTIONS"
      resources:
        - "^/stremio/"
        - "^/api/v1/(proxy|debrid|usenet|posters)/"
      policy: "bypass"
    - domain:
        - "aiostreams.example.com"
        - "prowlarr.example.com"
        - "metadata.example.com"
      policy: "two_factor"
session:
  inactivity: "1h"
  expiration: "12h"
  remember_me: "1M"
  cookies:
    - domain: "example.com"
      authelia_url: "https://auth.example.com"
      default_redirection_url: "https://aiostreams.example.com"
regulation:
  max_retries: 5
  find_time: "10m"
  ban_time: "1h"
storage:
  local:
    path: "/data/db.sqlite3"
notifier:
  filesystem:
    filename: "/data/notification.txt"
```

- `CookieSession` only: AIOStreams' own page sends a technical login header that Authelia would
  otherwise answer with a browser password window.
- Sessions end after 1 hour without use and after 12 hours at most; "remember me" keeps you 1 month.
- 5 wrong passwords in 10 minutes lock the account for 1 hour.
- Without an email server, Authelia writes its messages (e.g. the code to confirm a new passkey)
  to `data/authelia/notification.txt`.
- Data is stored in a small SQLite file – a few users need no database server.

Your user. First create the password's **hash** – a fingerprint from which the password cannot be
recovered. Use the Authelia version from `compose.yaml`; it asks for the password:

```sh
sudo docker run --rm -it ghcr.io/authelia/authelia:4.39.28 authelia crypto hash generate argon2
```

Copy the line after `Digest:` and put it in place of the `password` value:

<!-- file: config/authelia/users.yml -->
```yaml
users:
  you:
    displayname: "you"
    password: "$argon2id$v=19$m=65536,t=3,p=4$YOUR_PASSWORD_HASH"
    email: "you@roadie.invalid"
    groups:
      - "admins"
```

Replace `you` (twice) with your login name.

Three secrets that only Authelia uses, as files:

```sh
for f in jwt_secret session_secret storage_encryption_key; do
  openssl rand -hex 32 | tr -d '\n' | sudo tee secrets/authelia/$f > /dev/null
done
```

And where Authelia finds them:

<!-- file: env/authelia.env -->
```
AUTHELIA_IDENTITY_VALIDATION_RESET_PASSWORD_JWT_SECRET_FILE=/secrets/jwt_secret
AUTHELIA_SESSION_SECRET_FILE=/secrets/session_secret
AUTHELIA_STORAGE_ENCRYPTION_KEY_FILE=/secrets/storage_encryption_key
```

### 14. AIOStreams, Prowlarr and AIOMetadata settings

Settings that apply to the whole server go into *env files*, one per service. They hold
passwords, so only root may read them.

**AIOStreams:**

<!-- file: env/aiostreams.env -->
```
REQUEST_HEADER_OVERRIDES='[nzb_grabs]:{chrome},[newznab]:{chrome}'
USENET_PROVIDERS='[{"id":"provider1","name":"news.example.com","host":"news.example.com","port":563,"tls":true,"username":"YOUR_USENET_USER","password":"YOUR_USENET_PASSWORD","maxConnections":20,"priority":1}]'
BASE_URL=https://aiostreams.example.com
SECRET_KEY=YOUR_AIOSTREAMS_SECRET_KEY
DATABASE_URI=sqlite://./data/db.sqlite
AIOSTREAMS_AUTH=admin:YOUR_AIOSTREAMS_ADMIN_PASSWORD,playback:YOUR_AIOSTREAMS_PLAYBACK_PASSWORD
AIOSTREAMS_AUTH_PERMISSIONS=admin=admin,playback=service|proxy
CATALOG_CACHE_TTL=86400
META_CACHE_TTL=86400
USENET_SEGMENT_DISK_CACHE_BYTES=10737418240
BUILTIN_NZB_GRAB_DISK_CACHE_BYTES=2147483648
REGEX_FILTER_ACCESS=all
MANIFEST_CACHE_TTL=900
BUILTIN_PROWLARR_URL=http://prowlarr:9696
BUILTIN_PROWLARR_API_KEY=YOUR_PROWLARR_API_KEY
```

- `USENET_PROVIDERS`: your providers as a list. For a backup provider add a second entry with
  `"id":"provider2"`, `"priority":2` and `"isBackup":true`. Set `maxConnections` a little below what
  your plan allows. If a password contains `'`, `$`, `#` or `` ` ``, write them as `'`,
  `$`, `#`, ``` – Docker would read them differently.
- `AIOSTREAMS_AUTH`: two users. `admin` for the admin pages, `playback` for streaming – your
  configuration uses it to stream Usenet. Changing its password later breaks existing
  configurations.
- `REQUEST_HEADER_OVERRIDES`: NZB downloads and indexer searches identify as a normal browser.
- The cache sizes depend on your disk: 10 GB for streamed parts, 2 GB for NZB files suit 50 GB.
- `REGEX_FILTER_ACCESS=all` allows filter rules with regular expressions – fine on a private
  server behind a login.
- Without a TMDB key, delete the line `MANIFEST_CACHE_TTL=900`.

**Prowlarr** gets its API key from here, and leaves the login to Authelia:

<!-- file: env/prowlarr.env -->
```
PROWLARR__AUTH__APIKEY=YOUR_PROWLARR_API_KEY
PROWLARR__AUTH__METHOD=External
```

**AIOMetadata** (only with a TMDB key):

<!-- file: env/aiometadata.env -->
```
PORT=3232
HOST_NAME=https://metadata.example.com
NODE_ENV=production
LOG_LEVEL=info
DATABASE_URI=sqlite://addon/data/db.sqlite
REDIS_URL=redis://aiometadata-redis:6379
BUILT_IN_TMDB_API_KEY=YOUR_TMDB_API_KEY
ADDON_PASSWORD=YOUR_AIOMETADATA_ADDON_PASSWORD
ENABLE_CACHE_WARMING=false
CACHE_WARMUP_ON_STARTUP=false
TMDB_POPULAR_WARMING_ENABLED=false
MAL_WARMUP_ENABLED=false
```

The warm-up options are off: they would keep a small server busy for hours.

Now fill in the generated secrets – each command creates a random value and writes it into the
files:

```sh
cd /opt/stack
sudo sed -i "s/YOUR_AIOSTREAMS_SECRET_KEY/$(openssl rand -hex 32)/" env/aiostreams.env
sudo sed -i "s/YOUR_AIOSTREAMS_ADMIN_PASSWORD/$(openssl rand -hex 16)/" env/aiostreams.env
sudo sed -i "s/YOUR_AIOSTREAMS_PLAYBACK_PASSWORD/$(openssl rand -hex 16)/" env/aiostreams.env
key=$(openssl rand -hex 16); sudo sed -i "s/YOUR_PROWLARR_API_KEY/$key/" env/aiostreams.env env/prowlarr.env
sudo sed -i "s/YOUR_AIOMETADATA_ADDON_PASSWORD/$(openssl rand -hex 16)/" env/aiometadata.env
sudo chmod 600 env/* secrets/authelia/*
```

Put in your own values with `sudo nano`: `YOUR_USENET_USER`, `YOUR_USENET_PASSWORD`, the provider
address and `YOUR_TMDB_API_KEY`. Check that no placeholder is left:

```sh
sudo grep -rn "YOUR_" /opt/stack/env /opt/stack/config
```

### 15. Start and check

```sh
cd /opt/stack
sudo docker compose pull
sudo docker compose up -d
sudo docker compose ps
```

After about a minute every container should be `running` and `healthy`. If one is not, its log
says why:

```sh
sudo docker compose logs --tail 50 authelia
```

Open `https://aiostreams.example.com` in your browser. You land on the Authelia login. The very
first visit can take a minute while Traefik gets the certificates.

**Set up your second factor:** log in, choose *Register device* or a code app. Authelia asks you to
confirm with a one-time code that it "mailed" – it is in the file:

```sh
sudo tail -n 20 /opt/stack/data/authelia/notification.txt
```

**Passkey on an iPhone or iPad?** Safari does not tell Authelia that the passkey can be used to
log in by itself, so the login later fails with "Unable to find the credential". After
registering the passkey, run once:

```sh
sudo sqlite3 /opt/stack/data/authelia/db.sqlite3 "UPDATE webauthn_credentials SET discoverable = 1 WHERE discoverable = 0;"
```

**Your indexers in Prowlarr:** open `https://prowlarr.example.com` → *Indexers* → *Add Indexer* →
*Generic Newznab*. Enter a name, the API address (`https://indexer.example.com`), API path `/api`
and your API key, then *Test* and *Save*. Under *Show advanced* you can set the indexer's daily
*Query Limit* and *Grab Limit* – Prowlarr then pauses it before the indexer blocks you.
AIOStreams reads Prowlarr's indexer list only when it starts, so after every change:

```sh
sudo docker compose restart aiostreams
```

**Your AIOStreams configuration:** open `https://aiostreams.example.com/stremio/configure`.

1. Under **Services**, enable **AIOStreams** – the built-in Usenet streaming. In the field
   **AIOStreams Auth Token** enter `playback:` followed by the playback password from
   `env/aiostreams.env` (e.g. `playback:3f9a…`). It allows your configuration to stream.
2. Under **Addons**, open the **Marketplace** and add **Prowlarr**. Its address and API key come
   from the env file, so you only switch it on.
3. With a TMDB key, enter it under **Metadata** as *TMDB API Key* – it makes title matching more
   precise.
4. Set your languages and preferred quality under **Filters** and **Sorting**. Start simple: your
   languages first, then resolution, then size.
5. Open **Save & Install**, choose a password and save. AIOStreams shows your **UUID** – keep UUID
   and password in your password manager, you need them to change the configuration later.
6. Copy the **manifest address** and add it in Nuvio or Stremio under *Addons*.

Test that streams come: in the app, open any well-known movie – the sources should list results
from your indexer.

**AIOMetadata (with a TMDB key):** open `https://metadata.example.com/configure`, choose language
and catalogs and save with the addon password from `env/aiometadata.env` and a password of your
own. Add it in AIOStreams under **Addons** with *Using an addon that isn't in the Marketplace?*. Inside the server, AIOStreams
reaches it without the login as `http://aiometadata:3232/stremio/YOUR-UUID/manifest.json`.

---

## Part 4 – Run it

### 16. SSH keys only

Only now, after the key login worked in step 5, switch passwords off for SSH:

<!-- file: /etc/ssh/sshd_config.d/01-hardening.conf -->
```
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin no
AllowUsers streamadmin
AllowTcpForwarding no
X11Forwarding no
MaxAuthTries 3
```

Replace `streamadmin` with your user. The name starts with `01` on purpose: SSH uses the first
value it reads, and some cloud images bring a file that switches passwords on.

```sh
sudo sshd -t && sudo systemctl reload ssh
```

`sshd -t` checks the file first – a broken file could lock you out. If `reload` answers
"ssh.service is not active", that is fine: Ubuntu 24.04 starts SSH only when someone connects,
and the next login reads the new file. **Keep your current window open** and test a new login in
a second window before you close anything.

### 17. Push messages when something breaks

A server nobody watches breaks quietly. This small script checks every 5 minutes whether all
containers run and are healthy, whether your address answers from outside, and whether the disk
is filling up. It sends a push message through [ntfy](https://ntfy.sh) when something changes –
once when a problem appears, once when it is gone.

Choose a long, random topic name (anyone who knows it can read your messages), subscribe to it
in the ntfy app, and store it:

```sh
sudo mkdir -p /etc/stack
echo "stack-$(openssl rand -hex 8)" | sudo tee /etc/stack/ntfy-topic
echo "https://aiostreams.example.com/stremio/YOUR-UUID/YOUR-ENCRYPTED-PASSWORD/manifest.json" | sudo tee /etc/stack/addon-url
```

The second line is your manifest address from step 15. Create the script
`/usr/local/bin/stack-watch`:

```bash
#!/usr/bin/env bash
# Checks the stack and sends a push message when its state changes.
set -u
cd /opt/stack || exit 1
[ -e /run/stack-backup ] && exit 0   # a backup has the apps stopped on purpose
problems=""
for svc in $(docker compose config --services); do
  read -r state health <<< "$(docker compose ps -a --format '{{.State}} {{.Health}}' "$svc")"
  if [ "${state:-missing}" != "running" ]; then problems+="$svc is not running. "
  elif [ "${health:-}" = "unhealthy" ]; then problems+="$svc is not answering properly. "
  fi
done
curl -fsS -o /dev/null --max-time 20 "$(cat /etc/stack/addon-url)" || problems+="The addon is not reachable from outside. "
disk=$(df --output=pcent / | tail -1 | tr -dc '0-9')
[ "$disk" -gt 85 ] && problems+="The disk is $disk % full. "

state_file=/var/lib/stack-watch.state
last="$(cat "$state_file" 2>/dev/null || true)"
[ "$problems" = "$last" ] && exit 0
[ -e /run/stack-backup ] && exit 0   # a backup started while we were checking
if [ -n "$problems" ]; then title="Server problem"; text="$problems"; else title="Back to normal"; text="Everything is running again."; fi
# -4: ntfy.sh limits shared IPv6 networks; the state is saved only when the message got out
curl -4 -fsS -H "Title: $title" -d "$text" "https://ntfy.sh/$(cat /etc/stack/ntfy-topic)" > /dev/null \
  && printf '%s' "$problems" > "$state_file"
```

```sh
sudo chmod 755 /usr/local/bin/stack-watch
sudo /usr/local/bin/stack-watch && echo checked
```

Run it every 5 minutes with a systemd **timer** – the modern cron. `/etc/systemd/system/stack-watch.service`:

```ini
[Unit]
Description=Check the stack
After=network-online.target docker.service

[Service]
Type=oneshot
ExecStart=/usr/local/bin/stack-watch
```

`/etc/systemd/system/stack-watch.timer`:

```ini
[Unit]
Description=Check the stack every 5 minutes

[Timer]
OnBootSec=3min
OnUnitActiveSec=5min

[Install]
WantedBy=timers.target
```

```sh
sudo systemctl daemon-reload
sudo systemctl enable --now stack-watch.timer
systemctl list-timers 'stack-*'
```

Test it: stop a container, wait up to 5 minutes for the message, start it again:

```sh
sudo docker compose -f /opt/stack/compose.yaml stop prowlarr
sudo docker compose -f /opt/stack/compose.yaml start prowlarr
```

### 18. Keep a DuckDNS address up to date

Only with a DuckDNS name: tell DuckDNS your server's address every 5 minutes, in case it ever
changes. Store the token, readable only by root:

```sh
echo 'YOUR-DUCKDNS-TOKEN' | sudo tee /etc/stack/duckdns-token > /dev/null
sudo chmod 600 /etc/stack/duckdns-token
```

`/etc/systemd/system/stack-duckdns.service` (replace `myname`):

```ini
[Unit]
Description=Update the DuckDNS address
After=network-online.target

[Service]
Type=oneshot
ExecStart=/bin/sh -c 'curl -fsS "https://www.duckdns.org/update?domains=myname&token=$(cat /etc/stack/duckdns-token)&ip="'
```

`/etc/systemd/system/stack-duckdns.timer`:

```ini
[Unit]
Description=Update the DuckDNS address every 5 minutes

[Timer]
OnBootSec=1min
OnUnitActiveSec=5min

[Install]
WantedBy=timers.target
```

```sh
sudo systemctl daemon-reload
sudo systemctl enable --now stack-duckdns.timer
journalctl -u stack-duckdns -n 5
```

DuckDNS answers `OK` when it worked.

### 19. Backups

Everything that matters is in `/opt/stack`: your settings, logins, passkeys and configurations.
The backup is encrypted with **age**, so it is safe to keep on your computer or in the cloud.

Create a key pair. The **private key** opens the backups – it must not stay on the server, or a
thief would get it together with the backups:

```sh
sudo age-keygen -o /root/backup-key.txt
sudo cat /root/backup-key.txt
```

Save the line starting with `AGE-SECRET-KEY-` in your password manager. Then keep only the public
part on the server and delete the private key:

```sh
sudo age-keygen -y /root/backup-key.txt | sudo tee /etc/stack/backup-recipient
sudo shred -u /root/backup-key.txt
```

The script `/usr/local/bin/stack-backup` stops the apps for about a minute (a database must not
change while it is copied), packs and encrypts the folder, and keeps the two newest backups.
Traefik keeps running. The cache folders are left out – they fill up again by themselves.

```bash
#!/usr/bin/env bash
set -euo pipefail
dest=/home/streamadmin/backups
mkdir -p "$dest"
cd /opt/stack
touch /run/stack-backup                     # the watcher stays quiet meanwhile
trap 'docker compose start > /dev/null; rm -f /run/stack-backup' EXIT
docker compose stop $(docker compose config --services | grep -vx traefik)
tar -C /opt -czf - --exclude=stack/data/aiostreams/cache --exclude=stack/data/aiometadata-redis stack \
  | age -r "$(cat /etc/stack/backup-recipient)" > "$dest/stack-$(date +%F).tar.gz.age"
chown -R streamadmin: "$dest"
ls -1t "$dest"/stack-*.tar.gz.age | tail -n +3 | xargs -r rm --
echo "Backup ready: $(ls -1t "$dest"/stack-*.tar.gz.age | head -1)"
```

```sh
sudo chmod 755 /usr/local/bin/stack-backup
sudo /usr/local/bin/stack-backup
```

Every Sunday night – `/etc/systemd/system/stack-backup.service`:

```ini
[Unit]
Description=Encrypted backup of the stack
After=docker.service

[Service]
Type=oneshot
ExecStart=/usr/local/bin/stack-backup
```

`/etc/systemd/system/stack-backup.timer`:

```ini
[Unit]
Description=Weekly backup

[Timer]
OnCalendar=Sun *-*-* 04:07:00
Persistent=true

[Install]
WantedBy=timers.target
```

```sh
sudo systemctl daemon-reload
sudo systemctl enable --now stack-backup.timer
```

**A backup only helps if it is not on the server.** Download it now and then – on a computer:

```sh
scp 'streamadmin@203.0.113.10:backups/*.tar.gz.age' .
```

or with the SFTP function of an app such as Termius.

**Restore on a new server:** go through steps 4–9 and 16–19, point your names to the new address,
copy the backup and your private key there, then:

```sh
age -d -i backup-key.txt stack-2026-11-15.tar.gz.age | sudo tar -xz -C /opt
cd /opt/stack && sudo docker compose up -d
```

Your addon address, logins and passkeys stay the same. Try a restore once on a spare server –
only then you know your backup works.

### 20. Updates

**The system** updates itself (step 8). **The containers** you update yourself, one at a time:

1. Read the release notes of the new version (on its GitHub page). Look for changed settings.
2. Make a backup: `sudo /usr/local/bin/stack-backup`
3. Change the version behind `image:` in `compose.yaml`, e.g. `aiostreams:v2.38.1` → the new one.
4. Pull and recreate only what changed, then check:

```sh
cd /opt/stack
sudo docker compose pull
sudo docker compose up -d
sudo docker compose ps
sudo docker image prune -f
```

`up -d` rebuilds only the containers whose image or settings changed. Your data in `data/` stays.
If the new version does not work, put the old version back in `compose.yaml` and run `up -d` again.

### Everyday commands

```sh
cd /opt/stack
sudo docker compose ps                         # what runs, healthy or not
sudo docker compose logs --tail 50 aiostreams  # the last lines of a log
sudo docker compose logs -f aiostreams         # follow it live, end with Ctrl+C
sudo docker compose restart prowlarr           # restart one service
sudo docker stats --no-stream                  # memory and CPU per container
sudo fail2ban-client status                    # who is blocked
systemctl list-timers 'stack-*'                # your scheduled jobs
```

---

## Where to get help

This guide is part of roadie. Questions about it – also small ones – are welcome
[here](https://github.com/usenetroadie/roadie/issues/new/choose). Please do not take questions
about this setup to the communities of AIOStreams, Prowlarr, Authelia or Traefik first: they do
not know this setup and run their projects in their free time. If the cause really lies in one of
their programs, we report it there with everything they need.

Never post your addon address, passwords, API keys or your backup key.

**Too much work?** [roadie](../README.md) does all of the above in about half an hour – and
`sudo roadie explain` shows you afterwards what it built.
