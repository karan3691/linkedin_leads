import json
import os
import re
import sys
from http.server import BaseHTTPRequestHandler
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import leads_finder as finder


class handler(BaseHTTPRequestHandler):
    def _send(self, status, body):
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_POST(self):
        if self.path != "/api/email":
            return self._send(404, {"error": "Not found."})
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 2048:
                return self._send(400, {"error": "Invalid request size."})
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                return self._send(400, {"error": "Invalid request."})
        except (ValueError, json.JSONDecodeError):
            return self._send(400, {"error": "Request must contain valid JSON."})

        name = " ".join(str(data.get("name", "")).split())[:100]
        profile_url = str(data.get("profile_url", ""))[:500]
        try:
            parsed = finder.urlparse(profile_url)
        except ValueError:
            return self._send(400, {"error": "A valid LinkedIn profile URL is required."})
        if not name or not re.fullmatch(r"[\w .,'’\-]+", name, re.UNICODE) or parsed.netloc.lower() not in {
            "linkedin.com", "www.linkedin.com", "in.linkedin.com"
        } or not re.match(r"^/in/[^/?#]+", parsed.path):
            return self._send(400, {"error": "A name and LinkedIn profile URL are required."})

        api_key = os.environ.get("SERPAPI_KEY")
        if not api_key:
            return self._send(503, {"error": "Live email lookup is not configured."})
        try:
            email, source = finder.find_public_email(name, profile_url, api_key)
        except Exception:
            return self._send(502, {"error": "Public email lookup could not be completed. Please try again later."})
        if not email:
            return self._send(200, {
                "email": "",
                "email_source": "",
                "email_status": "No matching public contact email found.",
            })
        return self._send(200, {
            "email": email,
            "email_source": source,
            "email_status": "Found in a public search result. Review the source before use.",
        })

    def do_GET(self):
        self._send(405, {"error": "Use POST to look up a public email."})
