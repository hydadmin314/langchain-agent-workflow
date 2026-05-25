from __future__ import annotations

import argparse
import json
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]
WEB_DIR = ROOT / "web"
SCHEMA_FILE = ROOT / "data" / "product_doc_agent" / "output" / "all_documents_business_schema.json"


class ReviewHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WEB_DIR), **kwargs)

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path in {"/", "/review"}:
            self.path = "/product_doc_review.html"
            return super().do_GET()
        if path == "/api/schema":
            return self._send_schema()
        return super().do_GET()

    def _send_schema(self) -> None:
        if not SCHEMA_FILE.exists():
            self.send_response(404)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            payload = {"error": f"Schema file not found: {SCHEMA_FILE}"}
            self.wfile.write(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
            return

        content = SCHEMA_FILE.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve product document review UI.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), ReviewHandler)
    print(f"Product document review UI: http://{args.host}:{args.port}/")
    server.serve_forever()


if __name__ == "__main__":
    main()
