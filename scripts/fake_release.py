"""Локальная «точка релизов» для проверки автообновления без GitHub.

Отдаёт ответ вида GitHub Releases API, сам архив и его контрольную сумму.
Апдейтер натравливается на неё переменной STREAM_DIRECTOR_UPDATE_URL.

    python scripts/fake_release.py build/StreamDirector-v0.5.0-win64.zip
    set STREAM_DIRECTOR_UPDATE_URL=http://127.0.0.1:8799/latest
"""

from __future__ import annotations

import argparse
import json
import re
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

PORT = 8799


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("zip", type=Path, help="архив релиза")
    args = parser.parse_args()

    zip_path = args.zip.resolve()
    sha_path = zip_path.with_name(zip_path.name + ".sha256")
    version = re.search(r"-v([0-9.]+)-", zip_path.name).group(1)
    base = f"http://127.0.0.1:{PORT}"
    payload = json.dumps({
        "tag_name": f"v{version}",
        "html_url": f"{base}/release",
        "assets": [
            {"name": zip_path.name, "browser_download_url": f"{base}/{zip_path.name}"},
            {"name": sha_path.name, "browser_download_url": f"{base}/{sha_path.name}"},
        ],
    }).encode()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 — имя задано базовым классом
            if self.path == "/latest":
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
                return
            name = self.path.lstrip("/")
            target = zip_path if name == zip_path.name else sha_path
            if name not in (zip_path.name, sha_path.name):
                self.send_error(404)
                return
            body = target.read_bytes()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    print(f"раздаю {version} на {base}/latest")
    HTTPServer(("127.0.0.1", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
