import os, json
import ai_kit as K
store = {}
K.load = lambda f, default=None: store.get(f, default)
K.save = lambda f, d: store.__setitem__(f, d)
K.update_key = lambda f, k, v: store.setdefault(f, {}).__setitem__(k, v)
K.remove_key = lambda f, k: store.get(f, {}).pop(k, None)
K.rate_ok = lambda f, m: True
K.rate_mark = lambda f: None
import astro_view as V, astro_delta as D
D.K.load = K.load; D.K.save = K.save
fails = []
def chk(c, m):
    print(("ok   " if c else "FAIL ") + m)
    if not c: fails.append(m)

raw = ("🟢 Сатурн Трин ☌ натальний Юпітер <i>(0.3°)</i>\n"
       "🔴 Марс Квадрат □ натальний Сонце <i>(1.2°)</i>\n"
       "🟡 Місяць Секстиль ⚹ натальний Венера <i>(2.0°)</i>\n")
parsed = D.parse(raw)
print("parsed:", [p["key"] for p in parsed])
V.capture(raw)
pend = store.get("astro_view_pending.json") or {}
chk(len(pend.get("new", [])) >= 1, "capture зберіг нові аспекти: %d" % len(pend.get("new", [])))
chk(all(a["tp"] != "Місяць" for a in pend["new"]), "Місяць не в кнопках, коли є повільні")
# порожній capture не затирає
D.classify = lambda raw, *a, **k: ([], [])
V.capture(raw)
chk(len(store["astro_view_pending.json"]["new"]) >= 1, "порожній capture не затирає pending")

# send
import requests
calls = []
def fake_post(url, **k):
    calls.append((url, k)); r = requests.Response(); r.status_code = 200; return r
requests.post = fake_post
os.environ["TELEGRAM_TOKEN"] = "T"; os.environ["TELEGRAM_CHAT_ID"] = "1"
import quiet; quiet.blocked = lambda *a, **k: False
ok = V.send_after_report()
chk(ok, "send_after_report відправив карту")
if calls:
    kb = json.loads(calls[0][1]["data"]["reply_markup"])["inline_keyboard"]
    chk(len(kb) == len(pend["new"]) + 1, "кнопок = аспектів + пропуск (%d)" % len(kb))
    chk(all("av_" in b[0]["callback_data"] for b in kb), "callback-префікс av_")
    chk("Який аспект" in calls[0][1]["data"]["caption"], "підпис містить питання")
chk(store["astro_view_pending.json"]["new"] == [], "pending очищено після відправки")
chk(not V.send_after_report(), "повторно без нового — мовчить")

# handle
pid = list(store["astro_view_store.json"])[0]
K.gemini_text = lambda *a, **k: "**Сатурн** дає структуру. Дія: лягти раніше."
r = V.handle(f"av_{pid}_0")
chk(r.get("ok") and "Сатурн" in r.get("html", "") or "Марс" in r.get("html", ""), "кнопка → розбір аспекту")
r = V.handle(f"av_skip_{pid}")
chk(r["ok"] and store["astro_view_state.json"]["pause_until"], "skip ставить паузу")
print("FAILS:", fails or "none")
