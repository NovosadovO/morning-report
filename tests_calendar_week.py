import os, sys, json, types
from datetime import datetime, timedelta, timezone
sys.path.insert(0, "/home/user/bot")
os.environ.setdefault("TELEGRAM_TOKEN","x"); os.environ.setdefault("TELEGRAM_CHAT_ID","1")

STORE={}
import ai_kit as K
K.load = lambda f, default=None: STORE.get(f, default if default is not None else {})
def _upd(f,k,v):
    STORE.setdefault(f,{})[k]=v
K.update_key=_upd
K.remove_key=lambda f,k: STORE.get(f,{}).pop(k,None)
SENT=[]
K.send_card=lambda text,kb=None,tag=None: (SENT.append((text,kb)) or True)
GEM=[]
K.gemini_text=lambda *a,**k: GEM.append(1)
K.gemini_json=lambda *a,**k: GEM.append(1)

import calendar_watch as C
C._store._data = {}
C._store.load = lambda: {}
# patch payload store to memory
class PS:
    def __init__(s): s.d={}
    def put(s,p):
        pid=K.Dedup.key(json.dumps(p,default=str)); s.d[pid]=p; return pid
    def get(s,pid): return s.d.get(pid)
C._store = PS()

N = K.now().replace(tzinfo=None, microsecond=0)
def ev(title, delta_min, dur=60, loc=""):
    st = N + timedelta(minutes=delta_min)
    return {"id":"e_"+title[:6]+str(delta_min), "summary":title,
            "start":{"dateTime":(st.replace(tzinfo=timezone(timedelta(hours=2)))).isoformat()},
            "end":{"dateTime":(st+timedelta(minutes=dur)).replace(tzinfo=timezone(timedelta(hours=2))).isoformat()},
            "location":loc}

RAW=[ev("Лікар Košice", 3*24*60, loc="Košice"),          # 3 дні -> t3d
     ev("Зустріч з Maroš", 24*60, loc="Bratislava"),      # 24 год -> t24h
     ev("Тренування", 5*24*60),                           # 5 днів -> тільки в тижні
     ev("🍵 Трав'яний чай", 26*60),                        # рутина
     ev("🌙 Нічна зміна", 30*60)]                          # зміна

C._raw_events.__wrapped__=None
def fake_raw(hours_ahead=C.DEFAULT_HOURS):
    out=[]
    for e in RAW:
        it=C._norm(e)
        if it: out.append(it)
    out.sort(key=lambda x:x["start"])
    return out
C._raw_events = fake_raw

fails=[]
def ck(cond,msg):
    print(("  ✅ " if cond else "  ❌ ")+msg)
    if not cond: fails.append(msg)

print("=== 1. tick: завчасні стадії ===")
n=C.tick()
txts="\n---\n".join(t for t,_ in SENT)
ck(n>=2, f"надіслано {n} нагадувань (>=2)")
ck("Лікар" in txts and "заздалегідь" in txts or "горизонт" in txts.lower(), "t3d про Лікаря")
ck("Maroš" in txts and ("завтра" in txts.lower()), "t24h про зустріч")
ck("чай" not in txts.lower(), "рутина не нагадується")
ck("зміна" not in txts.lower(), "зміна не нагадується")

print("=== 2. дедуп ===")
SENT.clear(); n2=C.tick()
ck(n2==0, f"повторний прохід нічого не надіслав (got {n2})")

print("=== 3. тижневий текст ===")
w=C.week_text(7)
print(w[:700])
ck("ТИЖДЕНЬ ВПЕРЕД" in w, "заголовок є")
ck("Лікар" in w and "Тренування" in w, "події з майбутніх днів у тижні")
ck(w.count("порожньо")>=1, "порожні дні позначені")

print("=== 4. week() 1x/тиждень ===")
SENT.clear()
ok=C.week(force=True); ck(ok, "week(force) надіслав")
ck(any("cw_ack_" in json.dumps(kb) for _,kb in SENT), "кнопка cw_ack є")

print("=== 5. upcoming_text для AI ===")
u=C.upcoming_text(7)
print("  ", u)
ck("Лікар" in u and "Тренування" in u, "AI бачить майбутні дні")
ck("чай" not in u.lower(), "рутина не в AI-контексті")

print("=== 6. нуль AI ===")
ck(len(GEM)==0, f"Gemini не викликався, got {len(GEM)}")

print("\n"+("❌ FAIL: "+str(fails) if fails else "✅ ALL PASS"))
