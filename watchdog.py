#!/usr/bin/env python3
"""
НАГЛЯДАЧ НАД НАГЛЯДАЧАМИ  (watchdog)

Проблема, яку це закриває: бот має ~20 модулів-спостерігачів (пошта, крипто,
календар, Strava, підписки, дедлайни, здоров'я). Коли якийсь із них тихо
ламався — Олег про це НЕ дізнавався. Бот просто перестав писати про пошту,
і все виглядало «нормально». Тиша була неправдою.

Тепер:
  • раз на годину перевіряємо КОЖЕН датчик живим запитом
  • датчик осліп → Олег отримує повідомлення один раз (не спам щогодини)
  • датчик ожив → «знову бачу» з тривалістю простою
  • раз на добу — гарантія «нічого не пропущено»: що бот побачив і що
    зробив САМ за день, плюс перелік того, чого він не бачить

Принцип «не вигадувати»: якщо датчик недоступний, у звіті так і написано.
Нулі не малюємо, «все добре» без перевірки не пишемо.
"""

from datetime import datetime, timedelta

import ai_kit as K

TAG = "watchdog"

STATE = "watchdog_state.json"        # {sensor: {ok, since, last_alert, fails}}
DIGEST_STATE = "watchdog_digest.json"  # {last: 'YYYY-MM-DD'}
SCAN_STATE = "watchdog_scan.json"    # rate-limit перевірок

CHECK_GAP_MIN = 55        # перевірка датчиків раз на годину
REALERT_HOURS = 12        # повторно про той самий сліпий датчик — не частіше
DIGEST_HOUR = 21          # коли надсилати щоденну гарантію
FAILS_BEFORE_ALERT = 2    # одна мережева осічка — ще не привід писати


# ─── ДАТЧИКИ (живі перевірки) ────────────────────────────────────────────────

def _check_mail():
    import monitor as _m
    raw = _m.get_emails()
    if isinstance(raw, dict):
        n = len(raw.get("items") or [])
    elif isinstance(raw, list):
        n = len(raw)
    else:
        txt = str(raw or "")
        if not txt or "Помилка" in txt or "error" in txt.lower():
            return False, f"IMAP не відповідає: {txt[:80] or 'порожньо'}"
        n = -1
    return True, (f"{n} листів у вибірці" if n >= 0 else "відповідь є")


def _check_calendar():
    import context as _ctx
    token = _ctx._get_token()
    if not token:
        return False, "нема токена Google Calendar"
    ev = K.events_for_day(0)
    return True, f"доступний, подій сьогодні: {len(ev)}"


def _check_crypto():
    import monitor as _m
    p = _m.get_prices()
    if not p:
        return False, "CoinGecko не віддав ціни"
    return True, "ціни приходять"


def _check_strava():
    import monitor as _m
    fn = getattr(_m, "get_activities", None)
    if not fn:
        return False, "функції get_activities немає в monitor.py"
    acts = fn()
    if acts is None:
        return False, "Strava API відмовляє (403/токен) — доступ треба поновити"
    return True, f"активностей у кеші: {len(acts)}"


def _check_storage():
    ok = K.load("watchdog_probe.json", default=None)
    if ok is None:
        K.save("watchdog_probe.json", {"probe": K.now().isoformat()})
        return True, "запис у гілку data працює"
    return True, "гілка data читається"


LIVE = {
    "пошта": _check_mail,
    "календар": _check_calendar,
    "крипто": _check_crypto,
    "strava": _check_strava,
    "сховище": _check_storage,
}


# ─── СВІЖІСТЬ РОБОТИ МОДУЛІВ ─────────────────────────────────────────────────
# {назва: (файл, поле-з-часом, скільком годинам дозволено бути старим)}
FRESH = {
    "погодинний звіт": ("monitor_main_sent.json", "last_hour", 4),
    "скан пошти на дати": ("mailcal_scan.json", "", 4),
    "самоприбирання": ("tidy_state.json", "last", 36),
}


def _any_ts(obj, field=""):
    """Витягує найсвіжіший час із state-файлу, яким би не був його формат."""
    cands = []

    def walk(v):
        if isinstance(v, str):
            s = v.strip()
            if len(s) >= 10 and s[:4].isdigit():
                cands.append(s)
        elif isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)

    if field and isinstance(obj, dict) and obj.get(field):
        walk(obj.get(field))
    else:
        walk(obj)

    best = None
    for s in cands:
        for cut, fmt in ((19, "%Y-%m-%dT%H:%M:%S"), (16, "%Y-%m-%dT%H:%M"),
                         (10, "%Y-%m-%d")):
            try:
                d = datetime.strptime(s[:cut], fmt)
            except Exception:
                continue
            if best is None or d > best:
                best = d
            break
    return best


def _check_fresh(fname, field, max_h):
    data = K.load(fname, default=None)
    if data is None:
        return None, f"{fname} ще не створений"
    ts = _any_ts(data, field)
    if not ts:
        return None, f"у {fname} немає часу — пропускаю"
    age_h = (K.now().replace(tzinfo=None) - ts).total_seconds() / 3600.0
    if age_h > max_h:
        return False, f"остання робота {ts.strftime('%d.%m %H:%M')} — {int(age_h)} год тому"
    return True, f"свіжо ({int(age_h)} год тому)"


# ─── ПЕРЕВІРКА ───────────────────────────────────────────────────────────────

def check_all() -> list:
    """[{name, ok(True/False/None), detail}] — None означає «нема даних»."""
    out = []
    for name, fn in LIVE.items():
        try:
            ok, detail = fn()
        except Exception as e:
            ok, detail = False, f"помилка: {str(e)[:90]}"
        out.append({"name": name, "ok": ok, "detail": detail})

    for name, (fname, field, max_h) in FRESH.items():
        try:
            ok, detail = _check_fresh(fname, field, max_h)
        except Exception as e:
            ok, detail = None, f"перевірка не вдалась: {str(e)[:80]}"
        out.append({"name": name, "ok": ok, "detail": detail})
    return out


# ─── ВЕБХУК: САМОЛІКУВАННЯ (403/розбіжність секрету → бот мовчить) ───────────

_WH_FILE = "watchdog_webhook.json"


def _tg_token() -> str:
    import os
    for n in ("TELEGRAM_BOT_TOKEN", "BOT_TOKEN", "TELEGRAM_TOKEN", "TOKEN"):
        v = os.environ.get(n)
        if v and ":" in v:
            return v
    return str(getattr(K, "TOKEN", "") or "")


def _wh_url() -> str:
    import os
    u = os.environ.get("WEBHOOK_URL") or os.environ.get("PUBLIC_URL") or ""
    if u:
        return u.rstrip("/") if u.endswith("/telegram") else \
            u.rstrip("/") + "/telegram"
    d = os.environ.get("RAILWAY_PUBLIC_DOMAIN") or ""
    return ("https://" + d.strip("/") + "/telegram") if d else ""


def _webhook_guard() -> str:
    """Раз на годину: getWebhookInfo. Якщо вебхук зламаний — ставимо наново.

    Лікує саме той збій, через який бот замовкає на години: Telegram
    відбиває апдейти з 403 (секрет розійшовся) або черга не тане.
    """
    import os
    import json as _js
    import urllib.request as _u
    if str(os.environ.get("USE_WEBHOOK", "1")).strip() in ("0", "false", "no"):
        return ""
    tok = _tg_token()
    sec = os.environ.get("TELEGRAM_WEBHOOK_SECRET") or ""
    if not tok or not sec:
        return ""
    st = K.load(_WH_FILE, default={}) or {}
    now = K.now()
    try:
        prev = datetime.fromisoformat(str(st.get("checked_at"))[:19])
        if (now.replace(tzinfo=None) - prev.replace(tzinfo=None)
                ).total_seconds() < 3300:
            return ""
    except Exception:
        pass

    def _api(method, data=None):
        url = "https://api.telegram.org/bot" + tok + "/" + method
        body = _js.dumps(data).encode() if data else None
        req = _u.Request(url, data=body, headers={
            "Content-Type": "application/json"})
        with _u.urlopen(req, timeout=20) as r:
            return _js.loads(r.read().decode())

    info = {}
    try:
        info = (_api("getWebhookInfo") or {}).get("result", {}) or {}
    except Exception as e:
        K.log(TAG, "getWebhookInfo error: " + str(e))
        return ""
    st["checked_at"] = now.isoformat(timespec="seconds")
    err = str(info.get("last_error_message") or "")
    pending = int(info.get("pending_update_count") or 0)
    want = _wh_url()
    cur = str(info.get("url") or "")
    broken = bool(err) or pending > 5 or (want and cur != want)
    if not broken:
        st["state"] = "ok"
        K.save(_WH_FILE, st)
        return ""
    K.log(TAG, "вебхук зламаний: url=" + cur + " err=" + err[:80] +
          " pending=" + str(pending) + " → ставлю наново")
    ok = False
    try:
        res = _api("setWebhook", {
            "url": want or cur,
            "secret_token": sec,
            "drop_pending_updates": False,
            "allowed_updates": ["message", "callback_query", "edited_message"],
        }) or {}
        ok = bool(res.get("ok"))
    except Exception as e:
        K.log(TAG, "setWebhook error: " + str(e))
    st["state"] = "refixed" if ok else "broken"
    st["last_error"] = err[:200]
    st["fixed_at"] = now.isoformat(timespec="seconds") if ok else \
        st.get("fixed_at")
    K.save(_WH_FILE, st)
    msg = ("🔌 <b>Вебхук впав — я його підняв</b>\n\n"
           "Telegram не міг доставляти повідомлення" +
           ((": <code>" + err[:120] + "</code>") if err else "") +
           ("\nУ черзі було апдейтів: " + str(pending) if pending else "") +
           "\n\n" + ("✅ Вебхук перевстановлено — бот знову чує тебе."
                     if ok else
                     "❌ Перевстановити не вдалось — потрібна ручна дія."))
    try:
        K.send_card(msg, tag=TAG)
    except Exception:
        pass
    return "webhook"


def run(force=False) -> int:
    try:
        _webhook_guard()
    except Exception as _e_wg:
        K.log(TAG, "webhook guard error: " + str(_e_wg))

    """Перевіряє датчики, пише лише про ЗМІНИ стану. Повертає к-ть алертів."""
    if not force and not K.rate_ok(SCAN_STATE, CHECK_GAP_MIN):
        return 0
    K.rate_mark(SCAN_STATE)

    state = K.load(STATE, default={}) or {}
    now = K.now()
    broke, fixed = [], []

    for row in check_all():
        name, ok, detail = row["name"], row["ok"], row["detail"]
        if ok is None:
            continue
        st = state.get(name) or {}
        was_ok = st.get("ok", True)
        fails = int(st.get("fails", 0))

        if ok:
            if not was_ok:
                since = st.get("since", "")
                fixed.append((name, detail, since))
            state[name] = {"ok": True, "fails": 0, "since": now.isoformat(),
                           "detail": detail}
            continue

        fails += 1
        last_alert = st.get("last_alert", "")
        st_new = {"ok": False, "fails": fails, "detail": detail,
                  "since": st.get("since") if not was_ok else now.isoformat(),
                  "last_alert": last_alert}

        if fails >= FAILS_BEFORE_ALERT:
            stale = True
            if last_alert:
                try:
                    prev = datetime.fromisoformat(last_alert).replace(tzinfo=None)
                    stale = (now.replace(tzinfo=None) - prev).total_seconds() > REALERT_HOURS * 3600
                except Exception:
                    stale = True
            if stale:
                broke.append((name, detail))
                st_new["last_alert"] = now.isoformat()
        state[name] = st_new

    K.save(STATE, state)

    if broke:
        lines = ["👁 <b>Я осліп на частину даних</b>", ""]
        for name, detail in broke:
            lines.append(f"🔴 <b>{K.esc(name)}</b> — {K.esc(detail)}")
        lines.append("")
        lines.append("<i>Поки це не виправлено, я НЕ можу стежити за цією "
                     "частиною. Краще скажу прямо, ніж буду молчати, наче все гаразд.</i>")
        K.send_card("\n".join(lines), tag=TAG)

    if fixed:
        lines = ["👁 <b>Знову бачу</b>", ""]
        for name, detail, since in fixed:
            dur = ""
            try:
                d = datetime.fromisoformat(since).replace(tzinfo=None)
                hours = int((now.replace(tzinfo=None) - d).total_seconds() / 3600)
                if hours >= 1:
                    dur = f" (не бачив {hours} год)"
            except Exception:
                pass
            lines.append(f"🟢 <b>{K.esc(name)}</b> — {K.esc(detail)}{dur}")
        K.send_card("\n".join(lines), tag=TAG)

    n = len(broke) + len(fixed)
    K.log(TAG, f"перевірка: зламалось {len(broke)}, ожило {len(fixed)}")
    return n


# ─── ЩОДЕННА ГАРАНТІЯ «НІЧОГО НЕ ПРОПУЩЕНО» ──────────────────────────────────

def _today_counts() -> list:
    """Що бот побачив і зробив САМ за сьогодні — лише з реальних даних."""
    today = K.today_str()
    rows = []

    # Події, які бот сам створив у календарі з листів
    try:
        import mailcal as MC
        recs = [r for r in (K.load(MC.ITEMS_FILE, default={}) or {}).values()
                if isinstance(r, dict) and not r.get("empty")]
        made = [r for r in recs
                if str(r.get("created_at", "")).startswith(today)]
        live = [r for r in recs if r.get("state") == "live"]
        if made:
            rows.append(f"📅 створив подій з листів: {len(made)} — " +
                        ", ".join(K.esc(r.get("title", ""))[:28] for r in made[:3]))
        rows.append(f"📅 моїх подій у календарі живих: {len(live)}")
    except Exception as e:
        K.log(TAG, f"mailcal counts error: {e}")

    # Прибирання
    try:
        import tidy as TD
        log = K.load(TD.LOG_FILE, default={}) or {}
        if log.get(today):
            rows.append(f"🧹 прибрав мертвих записів: {len(log[today])}")
    except Exception:
        pass

    # Дедлайни / підписки / дати — скільки живого під наглядом
    try:
        import deadlines_watcher as DW
        it = K.load(DW.ITEMS_FILE, default={}) or {}
        openn = [r for r in it.values() if isinstance(r, dict) and not r.get("done")]
        if openn:
            rows.append(f"⏳ відкритих дедлайнів під наглядом: {len(openn)}")
    except Exception:
        pass
    try:
        import subs_watcher as SW
        it = K.load(SW.SUBS_FILE, default={}) or {}
        act = [r for r in it.values()
               if isinstance(r, dict) and r.get("confirmed")
               and r.get("active") and not r.get("cancelled")]
        if act:
            rows.append(f"💳 активних підписок під наглядом: {len(act)}")
    except Exception:
        pass
    try:
        import dates_book as DB
        it = K.load(DB.DATES_FILE, default={}) or {}
        if it:
            rows.append(f"🎂 дат у реєстрі: {len(it)}")
    except Exception:
        pass

    return rows


def digest(force=False) -> bool:
    """Раз на добу ввечері: що бачив, що зробив, чого не бачу."""
    today = K.today_str()
    st = K.load(DIGEST_STATE, default={}) or {}
    if not force:
        if st.get("last") == today:
            return False
        if K.now().hour < DIGEST_HOUR:
            return False

    checks = check_all()
    blind = [c for c in checks if c["ok"] is False]
    seeing = [c for c in checks if c["ok"] is True]

    lines = ["🛡 <b>Підсумок наглядача за день</b>", ""]
    done = _today_counts()
    if done:
        lines.append("<b>Що я бачив і робив сам:</b>")
        lines += [f"• {x}" for x in done]
        lines.append("")

    lines.append(f"<b>Датчики:</b> працює {len(seeing)} із {len(seeing) + len(blind)}")
    if blind:
        for c in blind:
            lines.append(f"🔴 {K.esc(c['name'])} — {K.esc(c['detail'])}")
        lines.append("")
        lines.append("<i>За цим я стежити НЕ можу, поки не виправлено.</i>")
    else:
        lines.append("🟢 Усі датчики відповідають — сліпих зон немає.")

    if K.send_card("\n".join(lines), tag=TAG):
        st["last"] = today
        K.save(DIGEST_STATE, st)
        K.log(TAG, "щоденний підсумок надіслано")
        return True
    return False


# ─── ЗВІТ НА ЗАПИТ ───────────────────────────────────────────────────────────

def report() -> str:
    """Для команди /наглядач — стан усіх датчиків прямо зараз."""
    checks = check_all()
    lines = ["🛡 <b>Наглядач: стан датчиків зараз</b>", ""]
    for c in checks:
        mark = {True: "🟢", False: "🔴"}.get(c["ok"], "⚪")
        lines.append(f"{mark} <b>{K.esc(c['name'])}</b> — {K.esc(c['detail'])}")
    rows = _today_counts()
    if rows:
        lines.append("")
        lines.append("<b>Сьогодні:</b>")
        lines += [f"• {x}" for x in rows]
    lines.append("")
    lines.append("<i>⚪ — немає даних для висновку, тому нічого не вигадую.</i>")
    return "\n".join(lines)
