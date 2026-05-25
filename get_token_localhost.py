#!/usr/bin/env python3
"""
Run this on YOUR LOCAL machine to get a new Google OAuth2 refresh token
with Gmail + Calendar scopes.

Requirements: pip install requests
"""

import http.server
import urllib.parse
import threading
import webbrowser
import requests
import json

CLIENT_ID = "878341164164-4qki4apv3mmo2s8006v9ks10q61sf5uk.apps.googleusercontent.com"
CLIENT_SECRET = "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"
REDIRECT_URI = "http://localhost:8080"
SCOPES = " ".join([
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/calendar.events",
])

code_holder = []

class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        code = params.get('code', [''])[0]
        if code:
            code_holder.append(code)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'<h1>Got it! Close this tab and check your terminal.</h1>')
            threading.Thread(target=self.server.shutdown).start()
        else:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b'No code found.')

    def log_message(self, *a):
        pass

auth_url = (
    f"https://accounts.google.com/o/oauth2/auth"
    f"?client_id={CLIENT_ID}"
    f"&redirect_uri={urllib.parse.quote(REDIRECT_URI)}"
    f"&response_type=code"
    f"&scope={urllib.parse.quote(SCOPES)}"
    f"&access_type=offline"
    f"&prompt=consent"
)

print("\n=== Google OAuth2 Token Generator ===")
print("\nStarting local server on port 8080...")
srv = http.server.HTTPServer(('', 8080), Handler)

print("Opening browser for Google login...")
print(f"\nIf browser doesn't open, go to:\n{auth_url}\n")
webbrowser.open(auth_url)

print("Waiting for Google callback...")
srv.serve_forever()

if not code_holder:
    print("No code received. Exiting.")
    exit(1)

code = code_holder[0]
print(f"\nGot authorization code: {code[:20]}...")

# Exchange code for tokens
print("\nExchanging code for tokens...")
resp = requests.post("https://oauth2.googleapis.com/token", data={
    "code": code,
    "client_id": CLIENT_ID,
    "client_secret": CLIENT_SECRET,
    "redirect_uri": REDIRECT_URI,
    "grant_type": "authorization_code",
})

data = resp.json()
if "refresh_token" not in data:
    print(f"\nERROR: {json.dumps(data, indent=2)}")
    exit(1)

refresh_token = data["refresh_token"]
print(f"\n{'='*50}")
print(f"SUCCESS! Your new refresh token:")
print(f"\n{refresh_token}\n")
print(f"{'='*50}")
print("\nNow set this in Railway:")
print(f"  Variable name:  GMAIL_REFRESH_TOKEN")
print(f"  Variable value: {refresh_token}")
print("\nDone!")
