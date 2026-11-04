"""Front door for :8005 / :8006. Callers keep their URLs; requests wait while the
interactive pair is down and go through once it is back.

Tier comes from where a request arrives: loopback is a host caller (scripts,
pipelines), so batch; a Docker bridge address is a client container (via the gate,
or host.docker.internal), so interactive. Only interactive requests reset the hold.
"""

from __future__ import annotations

import http.client
import http.server
import ipaddress
import threading
import time
from typing import Dict

HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te",
       "trailers", "transfer-encoding", "upgrade", "content-length", "host"}


class Gate:
    def __init__(self, queue_wait_seconds: float):
        self.cond = threading.Condition()
        self.ok: Dict[int, bool] = {}
        self.closed = False
        self.in_flight = 0
        self.waiting_interactive = 0
        self.last_interactive_at = 0.0
        self.queue_wait_seconds = queue_wait_seconds

    def set_ok(self, port: int, ok: bool) -> None:
        with self.cond:
            self.ok[port] = ok
            self.cond.notify_all()

    def close_and_drain(self, seconds: float) -> int:
        with self.cond:
            self.closed = True
            deadline = time.time() + seconds
            while self.in_flight and time.time() < deadline:
                self.cond.wait(1)
            return self.in_flight

    def reopen(self) -> None:
        with self.cond:
            self.closed = False
            self.cond.notify_all()

    def admit(self, backend: int, interactive: bool) -> bool:
        with self.cond:
            if interactive:
                self.last_interactive_at = time.time()
                self.waiting_interactive += 1
            deadline = time.time() + self.queue_wait_seconds
            try:
                while self.closed or not self.ok.get(backend):
                    left = deadline - time.time()
                    if left <= 0:
                        return False
                    self.cond.wait(min(left, 5))
                self.in_flight += 1
                return True
            finally:
                if interactive:
                    self.waiting_interactive -= 1

    def done(self) -> None:
        with self.cond:
            self.in_flight -= 1
            self.cond.notify_all()


def is_interactive(addr: str) -> bool:
    try:
        return not ipaddress.ip_address(addr).is_loopback
    except ValueError:
        return False


def make_handler(gate: Gate, backend: int):
    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.0"

        def log_message(self, *args):
            pass

        def _body(self) -> bytes:
            n = self.headers.get("Content-Length")
            if n is not None:
                return self.rfile.read(int(n))
            if "chunked" in self.headers.get("Transfer-Encoding", "").lower():
                out = b""
                while True:
                    size = int(self.rfile.readline().split(b";")[0].strip() or b"0", 16)
                    if size == 0:
                        while self.rfile.readline() not in (b"\r\n", b"\n", b""):
                            pass
                        return out
                    out += self.rfile.read(size)
                    self.rfile.readline()
            return b""

        def _plain(self, code: int, text: str) -> None:
            data = text.encode()
            self.send_response(code)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _handle(self):
            body = self._body()
            if self.path.rstrip("/") == "/health" and not (gate.ok.get(backend) and not gate.closed):
                return self._plain(503, "gpu-manager: interactive pair is down; requests queue\n")
            if not gate.admit(backend, is_interactive(self.client_address[0])):
                return self._plain(503, "gpu-manager: timed out waiting for the GPU\n")
            try:
                conn = http.client.HTTPConnection("127.0.0.1", backend, timeout=None)
                headers = {k: v for k, v in self.headers.items() if k.lower() not in HOP}
                if body or self.command in ("POST", "PUT", "PATCH"):
                    headers["Content-Length"] = str(len(body))
                conn.request(self.command, self.path, body=body or None, headers=headers)
                resp = conn.getresponse()
                self.send_response(resp.status, resp.reason)
                for k, v in resp.getheaders():
                    if k.lower() not in HOP:
                        self.send_header(k, v)
                length = resp.getheader("Content-Length")
                if length is not None and "chunked" not in (resp.getheader("Transfer-Encoding") or ""):
                    self.send_header("Content-Length", length)
                self.send_header("Connection", "close")
                self.end_headers()
                while True:
                    chunk = resp.read1(65536)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    self.wfile.flush()
                conn.close()
            except (ConnectionError, http.client.HTTPException, OSError):
                try:
                    self._plain(502, "gpu-manager: backend went away mid-request\n")
                except OSError:
                    pass
            finally:
                gate.done()

        do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = do_OPTIONS = do_HEAD = _handle

    return Handler


def serve(gate: Gate, listen, port: int, backend: int):
    servers = []
    for addr in listen:
        srv = http.server.ThreadingHTTPServer((addr, port), make_handler(gate, backend))
        srv.daemon_threads = True
        threading.Thread(target=srv.serve_forever, daemon=True,
                         name=f"proxy-{addr}:{port}").start()
        servers.append(srv)
    return servers
