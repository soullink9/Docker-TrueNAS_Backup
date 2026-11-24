"""Tests for truenas_api.py against a fake WebSocket (no TrueNAS needed)."""

import json
import unittest

import support  # noqa: F401  (sets up the import path)

from truenas_api import ApiError, TrueNAS


class FakeSocket:
    """Answers JSON-RPC requests from a table of method -> result or error."""

    def __init__(self, answers, notify_first=True):
        self.answers = answers
        self.sent = []
        self.inbox = []
        self.closed = False
        self.notify_first = notify_first

    def send(self, text):
        request = json.loads(text)
        self.sent.append(request)
        if self.notify_first:
            # An unrelated notification arrives before the answer.
            self.inbox.append(json.dumps({"jsonrpc": "2.0", "method": "collection_update", "params": {}}))
        answer = self.answers[request["method"]]
        if isinstance(answer, Exception):
            self.inbox.append(json.dumps({"jsonrpc": "2.0", "id": request["id"],
                                          "error": {"code": -32001, "message": str(answer),
                                                    "data": {"reason": "detail here"}}}))
        else:
            self.inbox.append(json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": answer}))

    def recv(self):
        return self.inbox.pop(0)

    def close(self):
        self.closed = True


def client(answers):
    sockets = []

    def connect(url, timeout, sslopt):
        sock = FakeSocket(answers)
        sock.url, sock.sslopt = url, sslopt
        sockets.append(sock)
        return sock

    return TrueNAS("wss://nas/api/current", "1-key", connect=connect), sockets


class ApiTest(unittest.TestCase):

    def test_logs_in_and_runs_replication(self):
        api, sockets = client({"auth.login_with_api_key": True, "replication.run": 42})
        self.assertEqual(api.run_replication(7), 42)
        sent = sockets[0].sent
        self.assertEqual(sent[0]["method"], "auth.login_with_api_key")
        self.assertEqual(sent[0]["params"], ["1-key"])
        self.assertEqual(sent[1], {"jsonrpc": "2.0", "id": 2, "method": "replication.run", "params": [7]})
        self.assertTrue(sockets[0].closed)
        self.assertEqual(sockets[0].sslopt["check_hostname"], False)

    def test_find_and_state(self):
        rows = [{"id": 7, "name": "Primary to backup", "state": {"state": "FINISHED"}}]
        api, sockets = client({"auth.login_with_api_key": True, "replication.query": rows})
        self.assertEqual(api.find_replication("Primary to backup")["id"], 7)
        self.assertEqual(sockets[0].sent[1]["params"], [[["name", "=", "Primary to backup"]]])
        self.assertEqual(api.replication_state(7), {"state": "FINISHED"})

    def test_missing_task_is_none(self):
        api, _ = client({"auth.login_with_api_key": True, "replication.query": []})
        self.assertIsNone(api.find_replication("Nope"))

    def test_bad_key(self):
        api, sockets = client({"auth.login_with_api_key": False})
        with self.assertRaises(ApiError):
            api.job(1)
        self.assertTrue(sockets[0].closed)

    def test_error_answer(self):
        api, _ = client({"auth.login_with_api_key": True, "core.get_jobs": RuntimeError("Not authorized")})
        with self.assertRaises(ApiError) as ctx:
            api.job(1)
        self.assertIn("Not authorized: detail here", str(ctx.exception))

    def test_connect_failure(self):
        def connect(url, timeout, sslopt):
            raise ConnectionRefusedError("refused")
        api = TrueNAS("wss://nas/api/current", "k", connect=connect)
        with self.assertRaises(ApiError):
            api.job(1)


if __name__ == "__main__":
    unittest.main()
