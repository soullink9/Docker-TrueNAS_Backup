"""
Settings for the TrueNAS backup cycle, read from environment variables.

Every variable is described in docs/setup.md. load() never raises for a bad
value: it returns the settings plus a list of problems, so the scheduler can
log them all at once and wait instead of crash-looping.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Mapping

import cronspec


DEFAULT_SCHEDULE = "0 1 * * mon"

MAC_RE = re.compile(r"^[0-9A-Fa-f]{2}([:-]?[0-9A-Fa-f]{2}){5}$")

TRUE_WORDS = {"1", "true", "yes", "on"}
FALSE_WORDS = {"0", "false", "no", "off", ""}


@dataclass
class Settings:

    # When the cycle runs (cron syntax, container time zone)
    schedule: str = DEFAULT_SCHEDULE
    run_on_start: bool = False

    # The backup box
    backup_host: str = ""
    backup_name: str = ""
    check_port: int = 22

    # Power control
    ipmi_host: str = ""
    ipmi_user: str = ""
    ipmi_password: str = ""
    ipmi_interface: str = "lanplus"
    wol_mac: str = ""
    wol_broadcast: str = "255.255.255.255"

    # The primary's TrueNAS API (runs the replication)
    truenas_url: str = ""
    truenas_api_key: str = ""
    truenas_verify_tls: bool = False

    # Replication
    replication_tasks: list[str] = field(default_factory=list)
    replication_retries: int = 1
    retry_wait: int = 300
    replication_timeout: int = 43200

    # Timing
    boot_timeout: int = 900
    settle_seconds: int = 120
    shutdown_timeout: int = 900

    # Behaviour
    shutdown_on_failure: bool = False
    shutdown_if_already_on: bool = False

    # Home Assistant
    ha_webhook_url: str = ""
    ha_verify_tls: bool = True

    @property
    def has_ipmi(self) -> bool:
        return bool(self.ipmi_host and self.ipmi_user)

    @property
    def display_name(self) -> str:
        return self.backup_name or self.backup_host


def _bool(env: Mapping[str, str], name: str, default: bool, problems: list[str]) -> bool:

    raw = env.get(name)

    if raw is None:
        return default

    word = raw.strip().lower()

    if word in TRUE_WORDS:
        return True

    if word in FALSE_WORDS:
        return False

    problems.append(f"{name} must be true or false, not '{raw}'")
    return default


def _int(env: Mapping[str, str], name: str, default: int, problems: list[str], minimum: int = 0) -> int:

    raw = env.get(name)

    if raw is None or raw.strip() == "":
        return default

    try:
        value = int(raw.strip())
    except ValueError:
        problems.append(f"{name} must be a whole number, not '{raw}'")
        return default

    if value < minimum:
        problems.append(f"{name} must be at least {minimum}, not {value}")
        return default

    return value


def _str(env: Mapping[str, str], name: str, default: str = "") -> str:
    return (env.get(name) or default).strip()


def parse_tasks(raw: str) -> list[str]:
    """REPLICATION_TASKS: task names separated by commas, in run order."""

    names: list[str] = []

    for part in raw.split(","):
        name = part.strip()
        if name and name not in names:
            names.append(name)

    return names


def load(env: Mapping[str, str] | None = None) -> tuple[Settings, list[str]]:
    """Settings from the environment, and a list of problems ([] = usable)."""

    env = os.environ if env is None else env
    problems: list[str] = []

    s = Settings(
        schedule=_str(env, "BACKUP_SCHEDULE", DEFAULT_SCHEDULE),
        run_on_start=_bool(env, "RUN_ON_START", False, problems),
        backup_host=_str(env, "BACKUP_HOST"),
        backup_name=_str(env, "BACKUP_NAME"),
        check_port=_int(env, "CHECK_PORT", 22, problems, minimum=1),
        ipmi_host=_str(env, "IPMI_HOST"),
        ipmi_user=_str(env, "IPMI_USER"),
        ipmi_password=env.get("IPMI_PASSWORD", ""),
        ipmi_interface=_str(env, "IPMI_INTERFACE", "lanplus"),
        wol_mac=_str(env, "WOL_MAC"),
        wol_broadcast=_str(env, "WOL_BROADCAST", "255.255.255.255"),
        truenas_url=_str(env, "TRUENAS_URL"),
        truenas_api_key=_str(env, "TRUENAS_API_KEY"),
        truenas_verify_tls=_bool(env, "TRUENAS_VERIFY_TLS", False, problems),
        replication_tasks=parse_tasks(env.get("REPLICATION_TASKS", "")),
        replication_retries=_int(env, "REPLICATION_RETRIES", 1, problems),
        retry_wait=_int(env, "RETRY_WAIT", 300, problems),
        replication_timeout=_int(env, "REPLICATION_TIMEOUT", 43200, problems, minimum=60),
        boot_timeout=_int(env, "BOOT_TIMEOUT", 900, problems, minimum=10),
        settle_seconds=_int(env, "SETTLE_SECONDS", 120, problems),
        shutdown_timeout=_int(env, "SHUTDOWN_TIMEOUT", 900, problems, minimum=10),
        shutdown_on_failure=_bool(env, "SHUTDOWN_ON_FAILURE", False, problems),
        shutdown_if_already_on=_bool(env, "SHUTDOWN_IF_ALREADY_ON", False, problems),
        ha_webhook_url=_str(env, "HA_WEBHOOK_URL"),
        ha_verify_tls=_bool(env, "HA_VERIFY_TLS", True, problems),
    )

    try:
        cronspec.parse(s.schedule)
    except ValueError as exc:
        problems.append(f"BACKUP_SCHEDULE: {exc}")

    if not s.backup_host:
        problems.append("BACKUP_HOST is not set (the backup box's TrueNAS IP)")

    if not s.has_ipmi and not s.wol_mac:
        problems.append("set IPMI_HOST and IPMI_USER, or WOL_MAC, so the backup box can be powered on")

    if s.ipmi_host and not s.ipmi_user:
        problems.append("IPMI_USER is not set")

    if s.has_ipmi and not s.ipmi_password:
        problems.append("IPMI_PASSWORD is not set")

    if s.wol_mac and not MAC_RE.match(s.wol_mac):
        problems.append(f"WOL_MAC '{s.wol_mac}' is not a MAC address")

    if not s.truenas_url:
        problems.append("TRUENAS_URL is not set (e.g. wss://127.0.0.1/api/current)")
    elif not s.truenas_url.startswith(("wss://", "ws://")):
        problems.append("TRUENAS_URL must start with wss:// (ws:// only for testing)")

    if not s.truenas_api_key:
        problems.append("TRUENAS_API_KEY is not set (an API key from the primary TrueNAS)")

    if not s.replication_tasks:
        problems.append("REPLICATION_TASKS is not set (replication task names, comma-separated)")

    return s, problems
