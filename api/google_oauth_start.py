import os,secrets,hmac,hashlib
from urllib.parse import urlencode
from http.server import BaseHTTPRequestHandler

CLIENT_ID="826707020754-58ogodcs5h007tt3nqqlhig5k9tlgsvb.apps.googleusercontent.com"
REDIRECT_URI="https://cime-dos-mundos.vercel.app/oauth/google/callback"
SECRET=os.getenv("CIME_SESSION_SECRET") or os.getenv("GOOGLE_CLIENT_SECRET") or "CimeDosMundos5_0_oauth_state_fallback"

def sign(value):
    return hmac.new(SECRET.encode(),value.encode(),hashlib.sha256).hexdigest()

class handler(BaseHTTPRequestHandler):
    protocol_version="HTTP/1.1"
    def do_GET(self):
        state=secrets.token_urlsafe(32)
        signed=state+"."+sign(state)
        query=urlencode({
            "client_id":CLIENT_ID,
            "redirect_uri":REDIRECT_URI,
            "response_type":"code",
            "scope":"openid email profile",
            "state":state,
            "prompt":"select_account"
        })
        self.send_response(302)
        self.send_header("Location","https://accounts.google.com/o/oauth2/v2/auth?"+query)
        self.send_header("Set-Cookie",f"cime_google_state={signed}; Path=/; Max-Age=600; HttpOnly; Secure; SameSite=Lax")
        self.send_header("Cache-Control","no-store")
        self.send_header("Content-Length","0")
        self.end_headers()
