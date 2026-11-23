import os, sys
os.environ.setdefault("TELEGRAM_TOKEN","x"); os.environ.setdefault("TELEGRAM_CHAT_ID","1")
import monitor, email as _e
from email.message import EmailMessage
class FakeIMAP:
    def __init__(self):
        self.msgs={}
        for i in range(1,16):
            m=EmailMessage(); m["Subject"]=f"S{i}"; m["From"]=f"Name{i} <n{i}@x.com>"; m["Date"]="Mon, 23 Nov 2026 10:00:00 +0100"; m.set_content("body"); self.msgs[str(i)]=m.as_bytes()
    def select(self,*a): return "OK",[b"1"]
    def uid(self,cmd,_,*a):
        if cmd=="search":
            q=a[0] if a else ""
            if "is:unread" in q: return "OK",[b"14 15"]
            if "category:primary" in q: return "OK",[" ".join(self.msgs).encode()]
            return "OK",[b""]
        if cmd=="fetch":
            return "OK",[(b"x",self.msgs[_.decode()])]
    def logout(self): pass
monitor._imap_connect=lambda: FakeIMAP()
monitor._classify_email=lambda s,t:"ok"
import storage; storage.save=lambda *a,**k:True
r=monitor.get_emails()
assert isinstance(r,dict), r
assert len(r["items"])==2, r["items"]
assert len(r["recent"])==10, len(r["recent"])
assert [x["uid"] for x in r["recent"]][:3]==["15","14","13"]
assert r["recent"][0]["unread"] and not r["recent"][2]["unread"]
print("OK")
