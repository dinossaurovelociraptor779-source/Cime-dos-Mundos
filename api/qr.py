from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import io
import qrcode

class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            p=urlparse(self.path)
            text=str(parse_qs(p.query).get("text",[""])[0]).strip()
            if not text or len(text)>2048 or not (text.startswith("http://") or text.startswith("https://")):
                raw=b'{"error":"Texto/URL invalido para QR."}'
                self.send_response(400)
                self.send_header("Content-Type","application/json; charset=utf-8")
                self.send_header("Content-Length",str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
                return
            img=qrcode.make(text)
            buf=io.BytesIO()
            img.save(buf,format="PNG")
            raw=buf.getvalue()
            self.send_response(200)
            self.send_header("Content-Type","image/png")
            self.send_header("Cache-Control","no-store")
            self.send_header("Content-Length",str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
        except Exception as e:
            raw=("{\"error\":\"QR_ERROR: "+str(e).replace("\\","/").replace('"',"\\\"")+"\"}").encode()
            self.send_response(500)
            self.send_header("Content-Type","application/json; charset=utf-8")
            self.send_header("Content-Length",str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
