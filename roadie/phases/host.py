"""Host steps: packages, swap, admin user, firewall, fail2ban, updates, SSH hardening."""

import hashlib
import ipaddress
import os
import re
import tempfile
import time
from pathlib import Path

from roadie import guard, system
from roadie.engine import Context, Outcome, Step
from roadie.i18n import t
from roadie.paths import atomic_write
from roadie.render import system as render_system

# sudo and python3-systemd: not always there on Debian (fail2ban's systemd backend needs the latter)
PACKAGES = ["ufw", "fail2ban", "python3-systemd", "unattended-upgrades", "age", "curl", "ca-certificates", "gnupg",
            "sudo", "python3-yaml"]
SWAPFILE = "/swapfile"
SSH_WAIT_SECONDS = 300
MAX_TRUSTED_IPS = 5  # addresses the admin used for the wizard; fail2ban never bans them
_RUN_SSHD = Path("/run/sshd")  # `sshd -t/-T` need it; missing until ssh ran once (socket activation)


def _in_container() -> bool:
    return system.ok(["systemd-detect-virt", "--container", "--quiet"])


def packages(ctx: Context) -> None:
    system.run(["apt-get", "update", "-q"])
    system.run(["apt-get", "install", "-y", "-q", *PACKAGES])


def swap(ctx: Context) -> None:
    facts = ctx.extra["facts"]
    has_swap = len(Path("/proc/swaps").read_text().strip().splitlines()) > 1
    if facts.mem_mb >= 3800 or has_swap or _in_container():
        return
    system.run(["fallocate", "-l", "2G", SWAPFILE])
    Path(SWAPFILE).chmod(0o600)
    system.run(["mkswap", SWAPFILE])
    system.run(["swapon", SWAPFILE])
    fstab = Path("/etc/fstab")
    if SWAPFILE not in fstab.read_text():
        with fstab.open("a") as f:
            f.write(f"{SWAPFILE} none swap sw 0 0\n")
    atomic_write(Path("/etc/sysctl.d/60-roadie-swap.conf"), "vm.swappiness=10\n", 0o644)
    system.run(["sysctl", "-q", "--system"], check=False)


def admin_user(ctx: Context) -> None:
    """Create the admin account (or reuse it) and add the user's public SSH key. Runs every time
    (a key changed in "change settings" is added); keys already there are never removed."""
    user, key = ctx.answers.data["admin_user"], ctx.answers.data["ssh_public_key"]
    if not system.ok(["id", user]):
        system.run(["useradd", "--create-home", "--shell", "/bin/bash", user])
    system.run(["usermod", "-aG", "sudo", user])
    # key-only account without a password: allow sudo the way Ubuntu cloud images do
    rule = Path(render_system.SUDO_RULE_DIR) / f"90-roadie-{user}"
    check = rule.with_name(f".{rule.name}.check")  # sudo ignores names with a dot: checked first
    atomic_write(check, f"{user} ALL=(ALL) NOPASSWD:ALL\n", 0o440)
    try:
        system.run(["visudo", "-cf", str(check)])
        os.replace(check, rule)
    finally:
        check.unlink(missing_ok=True)
    home = Path(system.run(["getent", "passwd", user]).split(":")[5])
    ssh_dir = home / ".ssh"
    key_file = ssh_dir / render_system.KEY_FILE
    ssh_dir.mkdir(mode=0o700, exist_ok=True)
    existing = key_file.read_text() if key_file.exists() else ""
    if key.split()[1] not in existing:
        separator = "" if existing.endswith("\n") or not existing else "\n"
        atomic_write(key_file, existing + separator + key + "\n", 0o600)
    system.run(["chown", "-R", f"{user}:{user}", str(ssh_dir)])


def ssh_ports() -> list[int]:
    """Every port sshd really listens on: its config, and on Ubuntu 24.04 also ssh.socket."""
    _RUN_SSHD.mkdir(mode=0o755, exist_ok=True)
    config = system.run(["sshd", "-T"], check=False)
    ports = {int(p) for p in re.findall(r"^(?:port |listenaddress \S*:)(\d+)\s*$", config, re.M)}
    listen = system.run(["systemctl", "show", "ssh.socket", "-p", "Listen"], check=False)
    ports |= {int(p) for p in re.findall(r":(\d+) \(Stream\)", listen)}
    return sorted(p for p in ports if 0 < p < 65536)


def _parent_ssh_vars() -> list[str]:
    """SSH_CONNECTION/SSH_CLIENT of the parent processes: sudo removes them from our environment."""
    found, pid = [], os.getppid()
    for _ in range(12):
        if pid <= 1:
            break
        try:
            environ = Path(f"/proc/{pid}/environ").read_bytes().split(b"\0")
            stat = Path(f"/proc/{pid}/stat").read_text()
        except OSError:
            break
        found += [item.split(b"=", 1)[1].decode("ascii", "replace") for item in environ
                  if item.startswith((b"SSH_CONNECTION=", b"SSH_CLIENT="))]
        pid = int(stat.rsplit(")", 1)[1].split()[1])
    return found


def client_ip() -> str | None:
    """Address of the SSH session that runs the wizard, if there is one."""
    values = [os.environ.get("SSH_CONNECTION"), os.environ.get("SSH_CLIENT")]
    if not any(values):
        values = _parent_ssh_vars()
    for value in filter(None, values):
        try:
            return str(ipaddress.ip_address(value.split()[0]))
        except (ValueError, IndexError):
            continue
    return None


def trust_ip(ctx: Context, ip: str | None) -> bool:
    """Remember an address the admin used (newest last). True when the list changed."""
    try:
        ip = str(ipaddress.ip_address(ip or ""))
    except ValueError:
        return False
    old = list(ctx.state.answers.get("_trusted_ips") or [])
    new = ([i for i in old if i != ip] + [ip])[-MAX_TRUSTED_IPS:]
    ctx.state.answers["_trusted_ips"] = new
    return new != old


def firewall(ctx: Context) -> None:
    ports = ssh_ports()
    if not ports:  # never switch the firewall on without knowing how SSH comes in
        raise RuntimeError(t("firewall.no_ssh_port"))
    ctx.state.answers["_ssh_ports"] = ports
    for cmd in render_system.ufw_commands(ports):
        system.run(cmd)


def _fail2ban_files(ctx: Context):
    return render_system.fail2ban_files(ctx.extra["render"], ctx.state.answers.get("_trusted_ips") or [],
                                        ctx.state.answers.get("_ssh_ports") or [22])


def fail2ban(ctx: Context) -> None:
    log = ctx.paths.stack_dir / "data/traefik/access.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    ctx.paths.stack_dir.chmod(0o700)  # containers write files 0644 (codes, databases): root only
    log.touch()
    trust_ip(ctx, client_ip())
    guard.write_guarded(ctx, _fail2ban_files(ctx), ctx.paths.stack_dir)
    system.run(["systemctl", "enable", "fail2ban"], check=False)
    system.run(["systemctl", "restart", "fail2ban"])


def fail2ban_ok(ctx: Context) -> bool:
    for _ in range(10):
        if system.ok(["fail2ban-client", "status", "sshd"]):
            return True
        time.sleep(2)
    return False


def auto_updates(ctx: Context) -> None:
    guard.write_guarded(ctx, [render_system.unattended_upgrades(ctx.extra["render"])], ctx.paths.stack_dir)


def key_login_seen(user: str, since: str) -> str | None:
    """The address of the newest key login of `user` (None: no such login)."""
    out = system.run(["journalctl", "-u", "ssh", "--since", since, "-o", "cat", "--no-pager"], check=False)
    found = None
    for line in out.splitlines():
        m = re.match(rf"Accepted publickey for {re.escape(user)} from (\S+) port ", line)
        if m:
            found = m.group(1)
    return found


def login_command(ctx: Context) -> str:
    """The exact command for the test login (with the key file the wizard told the user to save)."""
    user = ctx.answers.data["admin_user"]
    from roadie.answers import base_domain
    host = base_domain(ctx.answers.data) or t("ssh.check.server_address")
    from roadie.access import ssh_command
    return ssh_command(user, host, bool(ctx.state.answers.get("_ssh_key_created")))


def wait_for_key_login(ui, user: str, command: str, seconds: int = SSH_WAIT_SECONDS) -> str | None:
    """Show how to log in with the key and wait for that login (Enter = later)."""
    since = time.strftime("%Y-%m-%d %H:%M:%S")
    ui.heading(t("ssh.check.heading"))
    ui.info(t("ssh.check.instructions"))
    ui.copyable(command)
    ui.info(t("ssh.check.instructions_after"))
    ui.hint(t("ssh.check.ban_hint", user=user))
    deadline = time.time() + seconds
    while time.time() < deadline:
        left = int(deadline - time.time())
        ui.status(t("ssh.check.waiting", time=f"{left // 60}:{left % 60:02d}"))
        if ui.enter_pressed(3):
            return None
        seen = key_login_seen(user, since)
        if seen:
            return seen
    return None


def ssh_hardening(ctx: Context) -> Outcome | None:
    """Password and root login are switched off only after a key login of the admin user
    has really been seen in the SSH log - nobody gets locked out."""
    if ctx.state.answers.get("_ssh_hardened"):  # keep the file current: a newer roadie may write more
        _write_sshd_dropin(ctx)
        return None
    user = ctx.answers.data["admin_user"]
    if ctx.extra.get("interactive") and not ctx.extra.get("change"):  # "change settings" never waits here
        seen = wait_for_key_login(ctx.ui, user, login_command(ctx))
    else:
        seen = key_login_seen(user, "today")
    if not seen:  # Enter, timeout or no login today: password login stays on, the menu offers it later
        return Outcome("ok", note=t("ssh.check.later"), open_key="open.ssh_hardening")
    if isinstance(seen, str) and trust_ip(ctx, seen):  # the address the admin logs in from
        guard.write_guarded(ctx, _fail2ban_files(ctx), ctx.paths.stack_dir)
        system.run(["fail2ban-client", "reload"], check=False)
    _write_sshd_dropin(ctx)
    ctx.state.answers["_ssh_hardened"] = True
    ctx.ui.ok(t("ssh.check.done"))


def _write_sshd_dropin(ctx: Context) -> None:
    """Write roadie's sshd file and reload sshd, only when its content changed. A file sshd
    does not accept is put back as it was (or removed), so logins keep working."""
    _RUN_SSHD.mkdir(mode=0o755, exist_ok=True)
    dropin = render_system.sshd_dropin(ctx.extra["render"], sftp_log=sftp_override_works())
    target = guard.target(ctx, dropin, ctx.paths.stack_dir)
    try:
        old = target.read_text()
    except OSError:
        old = None
    written = ctx.state.answers.setdefault("_written", {})
    if old == dropin.content:
        written[dropin.path] = hashlib.sha256(old.encode()).hexdigest()
        return
    record = written.get(dropin.path)
    copies = ctx.extra.setdefault("mine_copies", [])
    before = len(copies)
    guard.write_guarded(ctx, [dropin], ctx.paths.stack_dir)  # a hand-edited file is copied first
    try:
        system.run(["sshd", "-t"])
    except system.CommandFailed:
        if old is None:
            target.unlink()
        else:
            atomic_write(target, old, 0o644)
        if record is None:  # the old file is back: so is what roadie knew about it
            written.pop(dropin.path, None)
        else:
            written[dropin.path] = record
        for _, copy in copies[before:]:  # nothing was replaced after all: no copy to report
            Path(copy).unlink(missing_ok=True)
        del copies[before:]
        raise
    # Ubuntu 24.04 starts sshd on demand (ssh.socket): when it is not running, the next login reads the new file
    system.run(["systemctl", "try-reload-or-restart", "ssh"])


def sftp_override_works() -> bool:
    """Whether this sshd takes a second Subsystem line and uses the first one. Newer OpenSSH
    does; 9.2 (Debian 12) refuses to start. Tried on a throwaway config, never the real one."""
    with tempfile.TemporaryDirectory() as tmp:
        probe = Path(tmp) / "sshd_config"
        probe.write_text("HostKey /etc/ssh/ssh_host_ed25519_key\n"
                         f"{render_system.SFTP_LOG_LINE}\nSubsystem sftp /usr/lib/openssh/sftp-server\n")
        try:
            system.run(["sshd", "-t", "-f", str(probe)])
            return True
        except system.CommandFailed:
            return False


def steps() -> list[Step]:
    return [
        Step("packages", "step.packages", packages),
        Step("swap", "step.swap", swap),
        Step("admin_user", "step.admin_user", admin_user, always=True),
        Step("firewall", "step.firewall", firewall, always=True,  # a changed SSH port is allowed
             verify=lambda c: "Status: active" in system.run(["ufw", "status"])),
        Step("fail2ban", "step.fail2ban", fail2ban, verify=fail2ban_ok, always=True),  # adds this session's address
        Step("auto_updates", "step.auto_updates", auto_updates, always=True),  # reboot time may change
    ]


def final_steps() -> list[Step]:
    return [Step("ssh_hardening", "step.ssh_hardening", ssh_hardening, always=True)]
