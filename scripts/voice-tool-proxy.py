"""Expose only authenticated Agora custom-tool calls through an HTTPS tunnel.

Run locally on 127.0.0.1:8765, then tunnel that port. The main Welcome Wave site
and its other API endpoints remain on localhost. This proxy never logs request
bodies or Authorization headers.
"""
from __future__ import annotations

import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

UPSTREAM = ("127.0.0.1", 8000)
MAX_BODY = 64 * 1024


class Handler(BaseHTTPRequestHandler):
    def log_message(self, _format, *_args):
        pass

    def do_POST(self):
        path = urlsplit(self.path).path
        if not path.startswith("/api/voice/tools/") or path.count("/") != 4:
            return self.send_error(404)
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0 or length > MAX_BODY:
            return self.send_error(413)
        auth = self.headers.get("Authorization", "")
        if not auth.startswith("Bearer ") or len(auth) > 2048:
            return self.send_error(401)
        body = self.rfile.read(length)
        connection = http.client.HTTPConnection(*UPSTREAM, timeout=110)
        try:
            connection.request("POST", path, body=body, headers={"Authorization": auth, "Content-Type": "application/json"})
            response = connection.getresponse()
            data = response.read()
            self.send_response(response.status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)
        except (OSError, TimeoutError):
            self.send_error(502)
        finally:
            connection.close()


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", 8765), Handler).serve_forever()
