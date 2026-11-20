"""CSV API server. Run: python server.py  ->  http://127.0.0.1:8000/"""
import json
import shutil
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

DATA = Path(__file__).parent / "data"
ROUTES = {
    "/secom": DATA / "secom/secom.csv",
    "/infineon/equipment1": DATA / "infineon_equipment/equipment1.csv",
    "/infineon/equipment2": DATA / "infineon_equipment/equipment2.csv",
    "/infineon/response": DATA / "infineon_equipment/response.csv",
    "/wafer-fault": DATA / "wafer_fault/wafer_23012020_041211.csv",
}
missing = [str(p) for p in ROUTES.values() if not p.is_file()]
assert not missing, f"missing CSV files: {missing}"


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = self.path.split("?")[0].rstrip("/") or "/"
        if path == "/":
            body = json.dumps(sorted(ROUTES)).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        file = ROUTES.get(path)
        if file is None:
            self.send_error(404, "unknown path, see / for the list")
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/csv; charset=utf-8")
        self.send_header("Content-Length", str(file.stat().st_size))
        self.end_headers()
        with file.open("rb") as src:
            shutil.copyfileobj(src, self.wfile)


if __name__ == "__main__":
    # shortcut: whole-file responses only (no paging/filtering), add query params when a client needs partial reads
    # 127.0.0.1 only: not reachable from other machines
    ThreadingHTTPServer(("127.0.0.1", 8000), Handler).serve_forever()
