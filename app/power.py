"""
Powering the backup box on and off, and checking whether it is up.

IPMI (ipmitool, lanplus) is the main way. Wake-on-LAN is the fallback for
power-on; it needs the container on the host network so its broadcast
reaches the LAN. Power-off is an IPMI "soft" off: an ACPI power-button press,
which TrueNAS answers with a clean shutdown.
"""

from __future__ import annotations

import logging
import os
import socket
import subprocess

from settings import Settings


log = logging.getLogger("power")

IPMI_TIMEOUT = 30


def magic_packet(mac: str) -> bytes:

    digits = mac.replace(":", "").replace("-", "")
    raw = bytes.fromhex(digits)

    if len(raw) != 6:
        raise ValueError(f"'{mac}' is not a MAC address")

    return b"\xff" * 6 + raw * 16


class Power:

    def __init__(self, settings: Settings, run=subprocess.run):
        self.s = settings
        self._run = run

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

    def is_on(self) -> bool:
        """Whether the backup box is already running (IPMI, else its port)."""

        if self.s.has_ipmi and self.ipmi_state() == "on":
            return True

        return self.host_up()

    def power_on(self) -> bool:

        if self.s.has_ipmi:
            ok, output = self.ipmi("chassis", "power", "on")
            if ok:
                log.info("IPMI power-on sent to %s.", self.s.ipmi_host)
                return True
            log.warning("IPMI power-on failed: %s", output or "no output")

        if self.s.wol_mac:
            try:
                self.send_wol()
            except (OSError, ValueError) as exc:
                log.warning("Sending Wake-on-LAN failed: %s", exc)
            else:
                log.info("Wake-on-LAN sent to %s via %s.", self.s.wol_mac, self.s.wol_broadcast)
                return True

        return False

    def soft_off(self) -> bool:
        """Ask for a clean shutdown. True when the request was accepted."""

        if not self.s.has_ipmi:
            log.warning("IPMI is not configured, so the backup box cannot be powered off.")
            return False

        ok, output = self.ipmi("chassis", "power", "soft")

        if not ok:
            log.warning("IPMI soft power-off failed: %s", output or "no output")

        return ok

    def is_off(self) -> bool:
        return self.ipmi_state() == "off"
