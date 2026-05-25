import http.server, urllib.parse, threading

code_holder = []

class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        code = params.get('code', [''])[0]
        if code:
            code_holder.append(code)
            self.send_response(200)
            self.send_header('Content-Type', 'text/html')
            self.end_headers()
            self.wfile.write(b'<h1>Got it! You can close this tab.</h1>')
            with open('/tmp/oauth_code.txt', 'w') as f:
                f.write(code)
            print(f'CODE: {code}', flush=True)
        else:
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'Waiting...')
    def log_message(self, *a): pass

srv = http.server.HTTPServer(('', 8080), Handler)
print('Listening on :8080...', flush=True)
srv.serve_forever()
