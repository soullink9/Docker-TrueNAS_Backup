"""
Sends the result of a run to a Home Assistant webhook.

The JSON body is {"status", "title", "message", "source", "target"}, where
status is success, failure, warning or test. The Home Assistant blueprint in
homeassistant/truenas-backup.yaml pushes everything except success to a
phone, and records successes so a missed run can be noticed.
"""

from __future__ import annotations

import json
import logging
import socket
import ssl
import time
import urllib.error
import urllib.request

from settings import Settings


log = logging.getLogger("notify")


class Notifier:

    def __init__(self, settings: Settings, opener=urllib.request.urlopen, sleep=time.sleep):
        self.s = settings
        self._open = opener
        self._sleep = sleep

    def payload(self, status: str, title: str, message: str) -> dict:
        return {
            "status": status,
            "title": title,
            "message": message,
            "source": socket.gethostname(),
            "target": self.s.display_name,
        }

    def send(self, status: str, title: str, message: str) -> bool:

        if not self.s.ha_webhook_url:
            log.info("HA_WEBHOOK_URL is not set, so no notification was sent.")
            return False

        body = json.dumps(self.payload(status, title, message)).encode()

        request = urllib.request.Request(
            self.s.ha_webhook_url,
            data=body,
            method="POST",
            headers={"Content-Type": "application/json"},
        )

        context = None
        if not self.s.ha_verify_tls:
            context = ssl.create_default_context()
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE

        for attempt in range(1, 4):
            try:
                with self._open(request, timeout=20, context=context) as response:
                    response.read()
                log.info("Sent '%s' notification to Home Assistant.", status)
                return True
            except (urllib.error.URLError, OSError) as exc:
                log.warning("Home Assistant not reached (try %d of 3): %s", attempt, exc)
                if attempt < 3:
                    self._sleep(5)

        return False
