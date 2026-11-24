"""
Powering the backup box on and off, and checking whether it is up.

Every method is optional; the ones that are configured are tried in order
until one works.

Power on (POWER_ON_ORDER, default "ipmi,wol"):
  ipmi  an IPMI "chassis power on" through the BMC (ipmitool, lanplus)
  wol   a Wake-on-LAN magic packet. Needs the container on the host network
        so its broadcast reaches the LAN.

Shut down (POWER_OFF_ORDER, default "api,ipmi"):
  api   the backup box's own TrueNAS API (system.shutdown): a normal,
        clean TrueNAS shutdown, with the reason in its log
  ipmi  an IPMI "soft" off: an ACPI power-button press, which TrueNAS also
        answers with a clean shutdown

Whether the box is off is read from IPMI when it is configured. Without
IPMI, the box counts as off once it stops answering on CHECK_PORT.
"""

from __future__ import annotations

import logging
import os
import socket
import subprocess

from settings import Settings
from truenas_api import ApiError, TrueNAS


log = logging.getLogger("power")

IPMI_TIMEOUT = 30

SHUTDOWN_REASON = "Weekly backup finished (Docker-TrueNAS_Backup)"


def magic_packet(mac: str) -> bytes:

    digits = mac.replace(":", "").replace("-", "")
    raw = bytes.fromhex(digits)

    if len(raw) != 6:
        raise ValueError(f"'{mac}' is not a MAC address")

    return b"\xff" * 6 + raw * 16


class Power:

    def __init__(self, settings: Settings, run=subprocess.run, backup_api=None):
        self.s = settings
        self._run = run

        if backup_api is None and settings.has_backup_api:
            backup_api = TrueNAS(
                settings.backup_truenas_url,
                settings.backup_truenas_api_key,
                verify_tls=settings.backup_truenas_verify_tls,
            )

        self.backup_api = backup_api

    # ---- IPMI -------------------------------------------------------------

    def ipmi(self, *args: str) -> tuple[bool, str]:
        """Run one ipmitool command; (ok, output)."""

        if not self.s.has_ipmi:
            return False, "IPMI is not configured"

        cmd = [
            "ipmitool", "-I", self.s.ipmi_interface,
            "-H", self.s.ipmi_host, "-U", self.s.ipmi_user,
            "-E",                                # password from IPMI_PASSWORD, not argv
            *args,
        ]
        env = dict(os.environ, IPMI_PASSWORD=self.s.ipmi_password)

        try:
            done = self._run(cmd, env=env, capture_output=True, text=True, timeout=IPMI_TIMEOUT)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return False, str(exc)

        output = ((done.stdout or "") + (done.stderr or "")).strip()
        return done.returncode == 0, output

    def ipmi_state(self) -> str:
        """'on', 'off' or 'unknown'."""

        ok, output = self.ipmi("chassis", "power", "status")

        if not ok:
            return "unknown"

        text = output.lower()

        if text.endswith(" on") or "power is on" in text:
            return "on"

        if text.endswith(" off") or "power is off" in text:
            return "off"

        return "unknown"

    # ---- network ------------------------------------------------------------

    def host_up(self) -> bool:
        """True when the backup box answers on CHECK_PORT (SSH by default)."""

        try:
            with socket.create_connection((self.s.backup_host, self.s.check_port), timeout=3):
                return True
        except OSError:
            return False

    def send_wol(self) -> None:

        packet = magic_packet(self.s.wol_mac)

        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            for port in (9, 7):
                for _ in range(3):
                    sock.sendto(packet, (self.s.wol_broadcast, port))

    # ---- what the backup cycle uses ----------------------------------------

    @property
    def confirms_power_state(self) -> bool:
        """True when IPMI can say the box is really powered off."""
        return self.s.has_ipmi

    def is_on(self) -> bool:
        """Whether the backup box is already running (IPMI, else its port)."""

        if self.s.has_ipmi and self.ipmi_state() == "on":
            return True

        return self.host_up()

    def power_on(self) -> str | None:
        """Tries each power-on method in order; returns the one that worked."""

        for method in self.s.power_on_methods:

            if method == "ipmi":
                ok, output = self.ipmi("chassis", "power", "on")
                if ok:
                    log.info("IPMI power-on sent to %s.", self.s.ipmi_host)
                    return method
                log.warning("IPMI power-on failed: %s", output or "no output")

            elif method == "wol":
                try:
                    self.send_wol()
                except (OSError, ValueError) as exc:
                    log.warning("Sending Wake-on-LAN failed: %s", exc)
                else:
                    log.info("Wake-on-LAN sent to %s via %s.", self.s.wol_mac, self.s.wol_broadcast)
                    return method

        return None

    def shut_down(self) -> str | None:
        """Tries each shutdown method in order; returns the one that was accepted."""

        for method in self.s.power_off_methods:

            if method == "api":
                try:
                    self.backup_api.shutdown(SHUTDOWN_REASON)
                except ApiError as exc:
                    log.warning("Shutdown through the backup box's API failed: %s", exc)
                else:
                    log.info("Shutdown requested through the backup box's TrueNAS API.")
                    return method

            elif method == "ipmi":
                ok, output = self.ipmi("chassis", "power", "soft")
                if ok:
                    log.info("IPMI soft power-off (clean shutdown) sent to %s.", self.s.ipmi_host)
                    return method
                log.warning("IPMI soft power-off failed: %s", output or "no output")

        return None

    def is_off(self) -> bool:
        """Off by IPMI's power status; without IPMI, no longer answering."""

        if self.s.has_ipmi:
            return self.ipmi_state() == "off"

        return not self.host_up()
