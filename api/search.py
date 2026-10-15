import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from http.server import BaseHTTPRequestHandler
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import leads_finder as finder


QUERY_GROUPS = {
    "all": finder.QUERIES,
    "bootcamp": finder.QUERIES[:4],
    "jobs": finder.QUERIES[4:8],
    "switchers": finder.QUERIES[8:],
}


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
        if self.path != "/api/search":
            return self._send(404, {"error": "Not found."})
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 4096:
                return self._send(400, {"error": "Invalid request size."})
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                return self._send(400, {"error": "Invalid request."})
        except (ValueError, json.JSONDecodeError):
            return self._send(400, {"error": "Request must contain valid JSON."})

        min_score = max(0, min(int(data.get("min_score", 3)), 20))
        pages = max(1, min(int(data.get("pages", 1)), 2))
        include_unknown = bool(data.get("include_unknown", False))
        seen = set()
        leads = []

        if data.get("demo") is True:
            leads = finder.build_leads(
                finder.DEMO_RESULTS, "demo", seen, min_score, include_unknown
            )
            return self._send(200, {"leads": [finder.asdict(lead) for lead in leads], "message": "Sample profiles shown for demonstration only."})

        api_key = os.environ.get("SERPAPI_KEY")
        if not api_key:
            return self._send(503, {"error": "Live search is not configured yet. Add SERPAPI_KEY in the Vercel project settings, then redeploy."})

        intent = data.get("intent", "all")
        queries = QUERY_GROUPS.get(intent)
        if queries is None:
            return self._send(400, {"error": "Choose a valid search intent."})

        jobs = [(query, page) for query in queries for page in range(pages)]

        def fetch(job):
            query, page = job
            try:
                return query, finder.search_serpapi(query, api_key, page), False
            except Exception:
                return query, [], True

        warnings = 0
        with ThreadPoolExecutor(max_workers=min(4, len(jobs))) as pool:
            futures = [pool.submit(fetch, job) for job in jobs]
            for future in as_completed(futures):
                query, results, failed = future.result()
                warnings += int(failed)
                leads.extend(finder.build_leads(results, query, seen, min_score, include_unknown))
        message = "Search complete. Review profiles before reaching out."
        if warnings:
            message += " Some searches could not be completed."
        self._send(200, {"leads": [finder.asdict(lead) for lead in leads], "message": message})

    def do_GET(self):
        if self.path == "/":
            html_path = Path(__file__).resolve().parent.parent / "index.html"
            try:
                payload = html_path.read_bytes()
            except OSError:
                return self._send(500, {"error": "The app page could not be loaded."})
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        self._send(405, {"error": "Use POST to run a search."})
