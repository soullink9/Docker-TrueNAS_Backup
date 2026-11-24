"""
A minimal client for the TrueNAS SCALE API (JSON-RPC 2.0 over WebSocket).

TrueNAS SCALE 25.04 and later serve this API at wss://<host>/api/current.
Every call here opens its own connection, logs in with the API key, makes its
requests and closes it again. A replication can run for hours; short separate
connections survive a restarted middleware or a network blip far better than
one connection held open the whole time.

Use wss:// (TLS). TrueNAS revokes an API key that is sent over plain ws://.
"""

from __future__ import annotations

import itertools
import json
import ssl
from contextlib import contextmanager
from typing import Any, Callable, Iterator

import websocket


class ApiError(Exception):
    """The API answered with an error, or could not be reached."""


class Connection:
    """One logged-in WebSocket connection. Use TrueNAS.session() to get one."""

    def __init__(self, ws: Any, timeout: float):
        self._ws = ws
        self._ids = itertools.count(1)
        self._timeout = timeout

    def call(self, method: str, *params: Any) -> Any:

        request_id = next(self._ids)

        self._ws.send(json.dumps({
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": list(params),
        }))

        # The server can also send notifications (no id, or another id);
        # skip those until our answer arrives.
        while True:

            raw = self._ws.recv()

            if not raw:
                raise ApiError(f"{method}: connection closed")

            try:
                message = json.loads(raw)
            except ValueError:
                continue

            if message.get("id") != request_id:
                continue

            if "error" in message and message["error"] is not None:
                raise ApiError(f"{method}: {describe_error(message['error'])}")

            return message.get("result")


def describe_error(error: Any) -> str:

    if not isinstance(error, dict):
        return str(error)

    text = str(error.get("message") or "error")
    data = error.get("data")

    if isinstance(data, dict):
        reason = data.get("reason") or data.get("error")
        if reason and str(reason) not in text:
            text = f"{text}: {reason}"

    return text


class TrueNAS:

    def __init__(self, url: str, api_key: str, verify_tls: bool = False,
                 timeout: float = 30,
                 connect: Callable[..., Any] | None = None):
        self.url = url
        self.api_key = api_key
        self.verify_tls = verify_tls
        self.timeout = timeout
        self._connect = connect or websocket.create_connection

    @contextmanager
    def session(self) -> Iterator[Connection]:

        sslopt: dict[str, Any] = {}

        if not self.verify_tls:
            # TrueNAS ships with a self-signed certificate.
            sslopt = {"cert_reqs": ssl.CERT_NONE, "check_hostname": False}

        try:
            ws = self._connect(self.url, timeout=self.timeout, sslopt=sslopt)
        except Exception as exc:  # noqa: BLE001  (any connect failure is reported the same way)
            raise ApiError(f"cannot connect to {self.url}: {exc}") from exc

        try:
            conn = Connection(ws, self.timeout)

            try:
                ok = conn.call("auth.login_with_api_key", self.api_key)
            except ApiError:
                raise
            except Exception as exc:  # noqa: BLE001
                raise ApiError(f"login failed: {exc}") from exc

            if ok is not True:
                raise ApiError("login failed: the API key was not accepted")

            yield conn

        except ApiError:
            raise
        except Exception as exc:  # noqa: BLE001  (socket errors mid-call)
            raise ApiError(f"API connection failed: {exc}") from exc
        finally:
            try:
                ws.close()
            except Exception:  # noqa: BLE001
                pass

    # ---- what the backup cycle needs ---------------------------------------

    def find_replication(self, name: str) -> dict | None:
        """The replication task with exactly this name, or None."""

        with self.session() as conn:
            rows = conn.call("replication.query", [["name", "=", name]])

        return rows[0] if rows else None

    def run_replication(self, task_id: int) -> int:
        """Start a replication task now; returns the middleware job id."""

        with self.session() as conn:
            job_id = conn.call("replication.run", task_id)

        if not isinstance(job_id, int):
            raise ApiError(f"replication.run returned {job_id!r}, not a job id")

        return job_id

    def job(self, job_id: int) -> dict | None:

        with self.session() as conn:
            rows = conn.call("core.get_jobs", [["id", "=", job_id]])

        return rows[0] if rows else None

    def replication_state(self, task_id: int) -> dict:
        """The task's state block: {"state": "FINISHED" | "ERROR" | ..., ...}."""

        with self.session() as conn:
            rows = conn.call("replication.query", [["id", "=", task_id]])

        if not rows:
            return {}

        return rows[0].get("state") or {}
