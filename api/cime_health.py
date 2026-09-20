import json
from http.server import BaseHTTPRequestHandler

class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        raw=json.dumps({
            "ok":True,
            "version":"5.0",
            "service":"Cime dos Mundos",
            "deployment":"vercel",
            "online":True,
            "public_url":"https://cime-dos-mundos.vercel.app"
        },ensure_ascii=False,separators=(",",":")).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type","application/json; charset=utf-8")
        self.send_header("Cache-Control","no-store")
        self.send_header("Content-Length",str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)
