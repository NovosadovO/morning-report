"""outbox: sendPhoto (urllib multipart) і requests проходять той самий фільтр."""
import json, outbox as O
fails = []
def chk(c, m):
    print(("ok   " if c else "FAIL ") + m)
    if not c: fails.append(m)

# 1. _extract
url = "https://api.telegram.org/botX/sendPhoto"
b = "----B"
body = (f"--{b}\r\nContent-Disposition: form-data; name=\"chat_id\"\r\n\r\n1\r\n"
        f"--{b}\r\nContent-Disposition: form-data; name=\"caption\"\r\n\r\nПідпис графіка HRV 60\r\n"
        f"--{b}\r\nContent-Disposition: form-data; name=\"photo\"; filename=\"a.png\"\r\nContent-Type: image/png\r\n\r\n").encode() + b"\x89PNG\r\n--" + f"{b}--\r\n".encode()
chk(O._extract(url, body)[0] == "Підпис графіка HRV 60", "multipart caption витягується")
chk(O._extract(url, None, {"caption": "abc"})[0] == "abc", "requests data=dict caption")
chk(O._extract("https://api.telegram.org/botX/sendMessage", None, {"text": "hi"})[0] == "hi", "requests json text")
chk(O._extract("https://example.com/x", None, {"text": "hi"})[0] is None, "чужий URL ігнорується")
chk(O._extract(url, None, {"chat_id": 1})[0] is None, "фото без підпису проходить")

# 2. requests-хук: дубль підпису глушиться, перший — ні
import requests
sent = []
def fake(self, method, url, *a, **k):
    if 'api.telegram.org' in str(url) or 'example.com' in str(url): sent.append(url)
    r = requests.Response(); r.status_code = 200; r._content = b"{}"; return r
O._orig_rq[0] = None
requests.sessions.Session.request = fake
import tempfile, os
O._install_requests()
O._is_user_context = lambda: False
O._state["data"] = {"sent": [], "qs": [], "drops": [], "stats": {}}
O._state["ts"] = 9e12
O._flush = lambda force=False: None
O._load = lambda: O._state["data"]
cap = "🔻 Пульс спокою: 58 уд/хв — вище за твою норму 52, найімовірніша причина нічна зміна і мало сну. Сьогодні легка прогулянка і рано лягти."
requests.post("https://api.telegram.org/botX/sendPhoto", data={"chat_id": 1, "caption": cap}, files={"photo": ("a.png", b"x")})
requests.post("https://api.telegram.org/botX/sendPhoto", data={"chat_id": 1, "caption": cap}, files={"photo": ("a.png", b"x")})
chk(len(sent) == 1, "другий однаковий підпис не відправлено (відправлено %d)" % len(sent))
requests.post("https://example.com/x", data={"caption": cap})
chk(len(sent) == 2, "чужі POST не чіпаються")
print("FAILS:", fails or "none")
