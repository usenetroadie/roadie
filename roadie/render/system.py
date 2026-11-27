"""Host hardening files (SSH, fail2ban, automatic security updates) and firewall commands."""

from roadie.render import HEADER, RenderConfig, RenderedFile

STACK_DIR = "/opt/roadie/stack"
SUDO_RULE_DIR = "/etc/sudoers.d"
KEY_FILE = "authorized_keys"  # inside the admin user's ~/.ssh
_HEAD = "".join(f"# {line}\n" for line in HEADER.splitlines())


SSHD_DROPIN = "/etc/ssh/sshd_config.d/01-roadie.conf"
# sftp-server logs every file it sends: the watcher sees when a backup was downloaded
SFTP_LOG_LINE = "Subsystem sftp /usr/lib/openssh/sftp-server -l INFO"


def sshd_dropin(cfg: RenderConfig, sftp_log: bool = False) -> RenderedFile:
    # sshd uses the FIRST value it reads; Ubuntu cloud images ship 50-cloudimg-settings.conf
    # with PasswordAuthentication yes, so this file must sort before it.
    lines = [
        "PasswordAuthentication no",
        "KbdInteractiveAuthentication no",
        "PermitRootLogin no",
        f"AllowUsers {cfg.admin_user}",
        "AllowTcpForwarding no",
        "X11Forwarding no",
        "MaxAuthTries 3",
    ]
    if sftp_log:
        lines.append(SFTP_LOG_LINE)
    return RenderedFile(SSHD_DROPIN, _HEAD + "\n".join(lines) + "\n", 0o644)


_JAIL = """\
[DEFAULT]
bantime.increment = true
# loopback and the addresses the admin used for the wizard are never banned
ignoreip = {ignoreip}

[sshd]
enabled = true
backend = systemd
port = {ssh_ports}
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
port = {ssh_ports}
filter = roadie-sshd-invalid
maxretry = 3
findtime = 10m
bantime = 1h
bantime.maxtime = 4w

# Docker-published ports bypass the INPUT chain, so web bans go into DOCKER-USER
[traefik-scanner]
enabled = true
backend = auto
filter = roadie-traefik-scanner
logpath = {stack}/data/traefik/access.log
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
"""

_SSHD_INVALID = r"""[INCLUDES]
before = common.conf

[Definition]
_daemon = sshd
failregex = ^%(__prefix_line)sInvalid user .* from <HOST> port \d+\s*$
            ^%(__prefix_line)sUser \S+ from <HOST> not allowed because .*$
            ^%(__prefix_line)sConnection closed by invalid user .* <HOST> port \d+ \[preauth\]\s*$
ignoreregex =
journalmatch = _SYSTEMD_UNIT=ssh.service + _COMM=sshd
"""

# Traefik common log format; fail2ban removes the date but keeps the brackets.
_SCANNER = r"""[Definition]
failregex = ^<HOST> \S+ \S+ \[[^]]*\] "[A-Z]+ (?:/\.env|/\.git|/\.aws|/wp-|/wordpress|/xmlrpc|/phpmyadmin|/pma|/vendor/|/cgi-bin/|/boaform|/actuator|/HNAP1|/owa/|[^ "?]*\.(?:php|asp|aspx|jsp|cgi)(?:[?/][^ "]*)?(?= ))[^ "]* [^"]*" 4\d\d 
ignoreregex =
"""


def fail2ban_files(cfg: RenderConfig, ignore_ips=(), ssh_ports=(22,)) -> list[RenderedFile]:
    jail = (_JAIL.replace("{stack}", STACK_DIR)
            .replace("{ignoreip}", " ".join(["127.0.0.1/8", "::1", *ignore_ips]))
            .replace("{ssh_ports}", ",".join(str(p) for p in ssh_ports)))
    return [
        RenderedFile("/etc/fail2ban/jail.d/roadie.conf", _HEAD + jail),
        RenderedFile("/etc/fail2ban/filter.d/roadie-sshd-invalid.conf", _HEAD + _SSHD_INVALID),
        RenderedFile("/etc/fail2ban/filter.d/roadie-traefik-scanner.conf", _HEAD + _SCANNER),
    ]


def unattended_upgrades(cfg: RenderConfig) -> RenderedFile:
    # "99zz" so these values win over Ubuntu's defaults (later files override earlier ones)
    text = _HEAD + (
        'APT::Periodic::Update-Package-Lists "1";\n'
        'APT::Periodic::Unattended-Upgrade "1";\n'
        'Unattended-Upgrade::Remove-Unused-Kernel-Packages "true";\n'
        'Unattended-Upgrade::Remove-Unused-Dependencies "true";\n'
        'Unattended-Upgrade::Automatic-Reboot "true";\n'
        'Unattended-Upgrade::Automatic-Reboot-WithUsers "true";\n'
        f'Unattended-Upgrade::Automatic-Reboot-Time "{cfg.reboot_time}";\n'
    )
    return RenderedFile("/etc/apt/apt.conf.d/99zz-roadie", text)


def ufw_commands(ssh_ports=(22,)) -> list[list[str]]:
    """SSH is allowed before the firewall is switched on, so the user is never locked out.
    Every port sshd listens on is allowed; without one the firewall is never switched on."""
    if not ssh_ports:
        raise ValueError("no SSH port known")
    return [
        ["ufw", "default", "deny", "incoming"],
        ["ufw", "default", "allow", "outgoing"],
        *(["ufw", "limit", f"{port}/tcp"] for port in ssh_ports),
        ["ufw", "allow", "80/tcp"],
        ["ufw", "allow", "443/tcp"],
        ["ufw", "--force", "enable"],
    ]

