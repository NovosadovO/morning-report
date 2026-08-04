"""
Intelligent Assistant v2.0 — Event-Driven Listener
Постійно слідкує за тригерами та САМ пише коли потребує
"""

import os
import json
import time
import imaplib
import email as email_lib
import urllib.request
import threading
from datetime import datetime, timedelta, timezone
from time import monotonic as _t_mono

# останній запуск модулів автоматизації (in-process, ключ -> monotonic)
_AUTO_LAST = {}
from zoneinfo import ZoneInfo
from email.header import decode_header

# ============ CONFIG ============

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GMAIL_USER = os.getenv("GMAIL_USER", "novosadovoleg@gmail.com")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD", "")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "2100366814")

_DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
_TZ = ZoneInfo("Europe/Bratislava")

VIP_KEYWORDS = {
    "boss": ["minebea", "mitsumi", "director", "ceo", "manager"],
    "investors": ["interfin", "maros", "sivak", "invest"],
    "hr": ["hr", "recruit", "job", "interview"],
}

# ============ STATE ============

_LISTENER_RUNNING = False
_LISTENER_THREAD = None

class IntelligentListener:
    """Event listener — постійно перевіряє тригери"""
    
    def __init__(self):
        self.last_email_check = 0
        self.last_calendar_check = 0
        self.last_crypto_check = 0
        self.last_user_activity = time.time()
        self.user_location = "doma"
        self.last_message_time = {}  # {trigger_type: timestamp}
        self.running = False
        
        self._load_state()
        self._log("Initialized")
    
    def _log(self, msg):
        """Log з timestamp"""
        ts = datetime.now(tz=_TZ).strftime("%H:%M:%S")
        print(f"[LISTENER {ts}] {msg}", flush=True)
    
    def _load_state(self):
        """Завантажити saved state"""
        try:
            state_file = os.path.join(_DATA_DIR, "listener_state.json")
            if os.path.exists(state_file):
                with open(state_file, "r") as f:
                    state = json.load(f)
                    self.user_location = state.get("location", "doma")
                    self.last_message_time = state.get("last_messages", {})
        except:
            pass
    
    def _save_state(self):
        """Зберегти state"""
        try:
            os.makedirs(_DATA_DIR, exist_ok=True)
            state_file = os.path.join(_DATA_DIR, "listener_state.json")
            with open(state_file, "w") as f:
                json.dump({
                    "location": self.user_location,
                    "last_messages": self.last_message_time,
                    "updated": datetime.now(tz=_TZ).isoformat(),
                }, f, indent=2)
        except Exception as e:
            self._log(f"Save state error: {e}")
    
    def mark_user_active(self):
        """Позначити що юзер активний (при команді)"""
        self.last_user_activity = time.time()
    
    def set_location(self, location: str):
        """Встановити location вручну (if /location command)"""
        if location in ["doma", "robota"]:
            self.user_location = location
            self._save_state()
            self._log(f"Location set to: {location}")
    
    # ========== TRIGGER CHECKS ==========
    
    def _check_vip_emails(self) -> list:
        """Отримати нові VIP листи за останні 2 хвилини"""
        try:
            mail = imaplib.IMAP4_SSL("imap.gmail.com", 993, timeout=10)
            mail.login(GMAIL_USER, GMAIL_APP_PASSWORD)
            mail.select("INBOX")
            
            # Листи за останні 2 хвилини
            since_time = (datetime.now() - timedelta(minutes=2)).strftime("%d-%b-%Y")
            status, messages = mail.search(None, f"SINCE {since_time}")
            
            if status != "OK" or not messages[0]:
                mail.close()
                return []
            
            email_ids = messages[0].split()[-5:]  # Last 5
            vip_emails = []
            
            for email_id in email_ids:
                status, msg_data = mail.fetch(email_id, "(RFC822)")
                if status != "OK":
                    continue
                
                msg = email_lib.message_from_bytes(msg_data[0][1])
                sender = self._decode_header(msg.get("From", ""))
                subject = self._decode_header(msg.get("Subject", ""))
                
                # Перевірити VIP
                is_vip = False
                for category, keywords in VIP_KEYWORDS.items():
                    if any(kw.lower() in (sender + subject).lower() for kw in keywords):
                        is_vip = True
                        break
                
                if is_vip:
                    vip_emails.append({
                        "from": sender,
                        "subject": subject,
                        "date": msg.get("Date", ""),
                    })
            
            mail.close()
            return vip_emails
        except Exception as e:
            self._log(f"Email check error: {e}")
            return []
    
    def _check_upcoming_events(self) -> list:
        """Реальні події у наступні 2 години (Google Calendar через monitor)."""
        try:
            import sys as _sys
            _sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            from monitor import get_calendar_events_upcoming
            events = get_calendar_events_upcoming(minutes_ahead=120)
            routine = ["shower", "water", "tea", "чай", "душ", "вода", "сауна",
                       "armolopid", "армолопід", "run", "біг"]
            return [e for e in events if not any(r in str(e).lower() for r in routine)]
        except Exception as e:
            self._log(f"Calendar check error: {e}")
            return []
    
    def _check_crypto_moves(self) -> dict:
        """Перевірити BTC/ETH/AVAX/ONDO/SOL/BNB/XRP за 1 годину (розширений watchlist).
        Використовує спільний TTL-кеш monitor.fetch_json_cached (60с) — цей чек
        іде кожні ~35с, тому без кешу швидко впирається в CoinGecko rate-limit (30/хв)."""
        try:
            ids = "bitcoin,ethereum,avalanche-2,ondo-finance,solana,binancecoin,ripple"
            url = f"https://api.coingecko.com/api/v3/simple/price?ids={ids}&vs_currencies=usd&include_24h_change=true"

            import sys as _sys
            _sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            from monitor import fetch_json_cached
            data = fetch_json_cached(url, ttl=60)
            if not data:
                return {}

            moves = {}
            for coin_id, coin_name in [("bitcoin", "BTC"), ("ethereum", "ETH"),
                                      ("avalanche-2", "AVAX"), ("ondo-finance", "ONDO"),
                                      ("solana", "SOL"), ("binancecoin", "BNB"), ("ripple", "XRP")]:
                if coin_id in data:
                    change = data[coin_id].get("usd_24h_change", 0)
                    if abs(change) >= 5:  # 5% move
                        moves[coin_name] = change

            return moves
        except Exception as e:
            self._log(f"Crypto check error: {e}")
            return {}
    
    def _check_idle_timeout(self) -> float:
        """Скільки годин неактивності"""
        return (time.time() - self.last_user_activity) / 3600

    def _check_weekly_run_compare(self) -> bool:
        """Понеділок вранці (7-10) — час порівняти минулий тиждень з попереднім"""
        now = datetime.now(tz=_TZ)
        return now.weekday() == 0 and 7 <= now.hour < 10

    def _check_habit_checkin(self) -> bool:
        """Ввечері (21-22) — нагадати відмітити звички/настрій, якщо ще не робив"""
        now = datetime.now(tz=_TZ)
        return 21 <= now.hour < 22

    def _get_shift_status(self):
        """Реальний статус (working_early/working_night/home/sleeping/...) за календарем."""
        try:
            import context as _ctx_mod
            return _ctx_mod.get_status()
        except Exception:
            return None

    def _check_day_plan_window(self) -> bool:
        """Час для плану дня, залежно від зміни: рання ~04:30-05:30, нічна ~15:30-16:30, вихідний ~08:00-09:00."""
        now = datetime.now(tz=_TZ)
        status = self._get_shift_status()
        if status == "working_early" or status == "pre_shift":
            return 4 <= now.hour < 6
        if status == "working_night":
            return 15 <= now.hour < 17
        return 8 <= now.hour < 10

    def _check_nutrition_window(self) -> bool:
        """Час для поради по харчуванню, зсунутий відносно day_plan щоб не збігались."""
        now = datetime.now(tz=_TZ)
        status = self._get_shift_status()
        if status == "working_early" or status == "pre_shift":
            return 5 <= now.hour < 7
        if status == "working_night":
            return 16 <= now.hour < 18
        return 12 <= now.hour < 14

    def _check_daily_astro_window(self) -> bool:
        """Раз на день окреме астро-повідомлення (не в звіті) — вранці 08:00-09:00."""
        now = datetime.now(tz=_TZ)
        return 8 <= now.hour < 9

    def _check_micro_checkin_window(self) -> bool:
        """Широке 'мікро-опитування' (настрій/ціль-вага/крипто-рішення/сон/усе особисте) —
        адаптивно, коли є трохи неактивності (не заважати посеред задачі), не в нічну зміну
        і не вночі (00:00-07:00). Часте, але не спамить (dedup у _should_send_trigger)."""
        now = datetime.now(tz=_TZ)
        status = self._get_shift_status()
        if status == "working_night":
            return False
        if not (7 <= now.hour < 23):
            return False
        idle = self._check_idle_timeout()
        return idle >= 0.75  # трохи неактивності — гарний момент запитати, не переривати

    def _check_health_pulse_window(self) -> bool:
        """2x/день короткий health-пульс: ~11:00-12:00 і ~16:00-17:00, не в нічну зміну (тоді на роботі)."""
        now = datetime.now(tz=_TZ)
        status = self._get_shift_status()
        if status == "working_night":
            return False
        return (11 <= now.hour < 12) or (16 <= now.hour < 17)

    def _check_interview_window(self) -> bool:
        """Вечірнє вікно для практики співбесіди (не в нічну зміну — тоді він на роботі)."""
        now = datetime.now(tz=_TZ)
        status = self._get_shift_status()
        if status == "working_night":
            return False
        return 19 <= now.hour < 22

    def _check_workout_window(self):
        """Пн/Ср/Пт — силове, Вт/Чт — розтяжка, вихідні — активне відновлення.
        Не в нічну зміну (тоді відпочиває вдень, тренування може завадити сну).
        Повертає тип тренування (str) або None якщо не час."""
        now = datetime.now(tz=_TZ)
        status = self._get_shift_status()
        if status == "working_night":
            return None
        # Вікно: після ранньої зміни (18-20) або на вихідному (17-19)
        if status == "working_early" or status == "post_shift":
            window_ok = 18 <= now.hour < 20
        else:
            window_ok = 17 <= now.hour < 19
        if not window_ok:
            return None
        wd = now.weekday()  # 0=Пн
        if wd in (0, 2, 4):
            return "strength"
        elif wd in (1, 3):
            return "stretch"
        else:
            return "rest"

    def _check_event_prep(self) -> list:
        """Реальні (не рутинні) события за 30-90 хв — підготовчий брифінг"""
        try:
            import sys as _sys
            _sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            from monitor import get_calendar_events_upcoming
            events = get_calendar_events_upcoming(minutes_ahead=90)
            routine = ["shower", "water", "tea", "чай", "душ", "вода", "сауна",
                       "armolopid", "армолопід", "run", "біг"]
            real_events = [e for e in events if not any(r in str(e).lower() for r in routine)]
            return real_events
        except Exception:
            return []
    
    def _check_time_based(self) -> str or None:
        """Ранок (6-7am) чи Вечір (20-21)?"""
        now = datetime.now(tz=_TZ)
        hour = now.hour
        
        if 6 <= hour < 7:
            return "morning"
        elif 20 <= hour < 21:
            return "evening"
        
        return None
    
    def _decode_header(self, header_str):
        """Декодує заголовок email"""
        if not header_str:
            return ""
        try:
            decoded_parts = []
            for part, charset in decode_header(header_str):
                if isinstance(part, bytes):
                    decoded_parts.append(part.decode(charset or 'utf-8', errors='ignore'))
                else:
                    decoded_parts.append(str(part))
            return "".join(decoded_parts)
        except:
            return str(header_str)
    
    # ========== DEDUP ==========
    
    def _should_send_trigger(self, trigger_type: str, min_hours: float = 1.0) -> bool:
        """Перевірити чи вже надсилали цей тригер нещодавно"""
        if trigger_type not in self.last_message_time:
            return True
        
        last_sent = self.last_message_time[trigger_type]
        hours_passed = (time.time() - last_sent) / 3600
        
        return hours_passed >= min_hours
    
    def _mark_trigger_sent(self, trigger_type: str):
        """Позначити що надіслали цей тригер"""
        self.last_message_time[trigger_type] = time.time()
        self._save_state()
    
    # ========== MAIN LOOP ==========
    
    def run(self):
        """Main worker loop"""
        self.running = True
        self._log("Worker started")
        
        while self.running:
            try:
                now = time.time()
                triggers = []
                
                # 1. EMAIL (раз на 5 хв — раніше було 30с "для DEBUG" і лишилось у проді,
                # через що Gmail API дьоргався сотні разів/год і кожен VIP-хіт тягнув Gemini)
                if now - self.last_email_check > 300:
                    self._log("[EMAIL CHECK]")
                    vip_emails = self._check_vip_emails()
                    if vip_emails:
                        self._log(f"  Found {len(vip_emails)} VIP листів")
                        if self._should_send_trigger("vip_email"):
                            triggers.append(("vip_email", vip_emails))
                            self._log(f"✅ TRIGGER: vip_email")
                        else:
                            self._log(f"  Skip: already sent recently")
                    else:
                        self._log(f"  No VIP emails")
                    self.last_email_check = now
                
                # 2. CALENDAR — реальні події за 30-90 хв (event_prep, підготовчий брифінг)
                if now - self.last_calendar_check > 60:
                    events = self._check_event_prep()
                    if events and self._should_send_trigger("event_soon", 1.5):
                        triggers.append(("event_soon", events))
                        self._log(f"TRIGGER: event_soon ({len(events)} подій)")
                    self.last_calendar_check = now
                
                # 3. CRYPTO (раз на 10 хв — узгоджено з TTL кешу CoinGecko,
                # раніше 35с "для DEBUG" лишилось у проді й дьоргало API дарма)
                if now - self.last_crypto_check > 600:
                    self._log("[CRYPTO CHECK]")
                    moves = self._check_crypto_moves()
                    if moves:
                        self._log(f"  Found moves: {moves}")
                        if self._should_send_trigger("crypto_move"):
                            triggers.append(("crypto_move", moves))
                            self._log(f"✅ TRIGGER: crypto_move")
                        else:
                            self._log(f"  Skip: already sent recently")
                    else:
                        self._log(f"  No big moves (±5%+)")
                    self.last_crypto_check = now
                
                # 4. IDLE TIMEOUT (кожну хвилину)
                idle = self._check_idle_timeout()
                if idle > 2 and self._should_send_trigger("idle_timeout", 2.0):
                    triggers.append(("idle_timeout", idle))
                    self._log(f"TRIGGER: idle_timeout ({idle:.1f}h)")
                
                # 5. TIME-BASED
                time_trigger = self._check_time_based()
                if time_trigger and self._should_send_trigger(time_trigger, 24.0):  # 1x per day
                    triggers.append((time_trigger, None))
                    self._log(f"TRIGGER: {time_trigger}")
                
                # 6. DEEP ANALYSIS (динамічна актуальність з local fallback)
                idle = self._check_idle_timeout()
                if self._should_send_trigger("deep_analysis", 3.0):  # Макс 1x на 3h (було 4h — Олег просив частіше)
                    triggers.append(("deep_analysis", idle))
                    self._log(f"TRIGGER: deep_analysis (idle={idle:.1f}h)")

                # 7. WEEKLY RUN COMPARE (понеділок вранці, 1x/тиждень)
                if self._check_weekly_run_compare() and self._should_send_trigger("weekly_run_compare", 24.0 * 6):
                    triggers.append(("weekly_run_compare", None))
                    self._log("TRIGGER: weekly_run_compare")

                # 8. HABIT/MOOD CHECKIN (ввечері, 1x/день)
                if self._check_habit_checkin() and self._should_send_trigger("habit_checkin", 20.0):
                    triggers.append(("habit_checkin", None))
                    self._log("TRIGGER: habit_checkin")

                # 9. DAY PLAN (1x/день, вікно залежить від зміни)
                if self._check_day_plan_window() and self._should_send_trigger("day_plan", 20.0):
                    triggers.append(("day_plan", None))
                    self._log("TRIGGER: day_plan")

                # 10. NUTRITION TIP (1x/день, вікно залежить від зміни)
                if self._check_nutrition_window() and self._should_send_trigger("nutrition_tip", 20.0):
                    triggers.append(("nutrition_tip", None))
                    self._log("TRIGGER: nutrition_tip")

                # 11. INTERVIEW PRACTICE (2-3x/тиждень, вечірнє вікно, не в нічну зміну)
                if self._check_interview_window() and self._should_send_trigger("interview_practice", 60.0):
                    triggers.append(("interview_practice", None))
                    self._log("TRIGGER: interview_practice")

                # 12. WORKOUT PLAN (1x/день, тип ротується по дню тижня, не в нічну зміну)
                _workout_type = self._check_workout_window()
                if _workout_type and self._should_send_trigger("workout_plan", 20.0):
                    triggers.append(("workout_plan", _workout_type))
                    self._log(f"TRIGGER: workout_plan ({_workout_type})")

                # 13. DAILY ASTRO (1x/день, окреме повідомлення, не тільки в звіті)
                if self._check_daily_astro_window() and self._should_send_trigger("daily_astro", 20.0):
                    try:
                        import sys as _sys3
                        _sys3.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
                        from astro import get_natal_transits_short
                        _astro_txt = get_natal_transits_short(max_aspects=4) or ""
                    except Exception as _ae:
                        _astro_txt = ""
                    if _astro_txt:
                        triggers.append(("daily_astro", _astro_txt))
                        self._log("TRIGGER: daily_astro")

                # 14. HEALTH PULSE (2x/день, короткий чек-ін)
                if self._check_health_pulse_window() and self._should_send_trigger("health_pulse", 4.0):
                    triggers.append(("health_pulse", None))
                    self._log("TRIGGER: health_pulse")

                # 15. MICRO CHECKIN (широке опитування — настрій/ціль/крипто/сон/усе особисте,
                # адаптивно на idle, кілька разів на день, тема ротується)
                if self._check_micro_checkin_window() and self._should_send_trigger("micro_checkin", 5.0):
                    triggers.append(("micro_checkin", None))
                    self._log("TRIGGER: micro_checkin")

                # 16. ПРОАКТИВНІ ПРОПОЗИЦІЇ ДІЙ (AI сам ініціює:
                # "Олеже, пропоную додати це в календар / занотувати / нагадати").
                # Модуль сам тримає свій rate-limit (90 хв між сканами, max 8/добу)
                # і сам вирішує, чи є реальна причина писати.
                try:
                    import proactive_actions as _pa_scan
                    if _pa_scan.should_scan():
                        self._log("[PROACTIVE ACTIONS] scan")
                        _n_pa = _pa_scan.scan_and_offer()
                        if _n_pa:
                            self._log(f"✅ Надіслано {_n_pa} пропозицій з кнопками")
                except Exception as _e_pa:
                    self._log(f"proactive_actions error: {_e_pa}")

                # 17. АВТОМАТИЗАЦІЯ ЖИТТЯ (кожен модуль сам тримає свій rate-limit
                # і сам вирішує, чи є реальна причина писати; без даних — молчить).
                #   💸 рахунки з пошти (кожні 2 год) + дедлайни оплати (раз на день)
                #   📭 залиплі листи без відповіді 3+ дні (2 рази на добу)
                #   🏃 план бігу під зміни (раз на ~20 год, зазвичай нд/пн)
                #   📊 тижневий огляд + 3 цілі (нд 19:00-22:00)
                #   📄 дедлайни з пошти (кожні 3 год) + попередження 14/7/3/1/0 днів
                #   🧠 картки людей + «хтось чекає на твій крок»
                #   ⚡ «як ти сьогодні» → план дня під енергію (вікно під зміну)
                # In-process throttle: loop крутиться раз на секунду, а ці модулі
                # читають storage/GitHub — без цього був би шквал запитів щосекунди.
                _mono = _t_mono()
                def _due(_key, _gap, _m=_mono):
                    if _m - _AUTO_LAST.get(_key, 0) < _gap:
                        return False
                    _AUTO_LAST[_key] = _m
                    return True

                try:
                    import bills_watcher as _bw_l
                    if _due("bills_scan", 900) and _bw_l.should_scan():
                        self._log("[BILLS] scan пошти на рахунки")
                        _n = _bw_l.scan()
                        if _n:
                            self._log(f"✅ Рахунків знайдено: {_n}")
                    _nd = _bw_l.check_due_soon() if _due("bills_due", 3600) else 0
                    if _nd:
                        self._log(f"✅ Нагадувань про оплату: {_nd}")
                except Exception as _e_bw:
                    self._log(f"bills_watcher error: {_e_bw}")

                try:
                    import followup_watcher as _fw_l
                    _nf = _fw_l.check() if _due("followup", 3600) else 0
                    if _nf:
                        self._log(f"✅ Follow-up карточок: {_nf}")
                except Exception as _e_fw:
                    self._log(f"followup_watcher error: {_e_fw}")

                try:
                    import run_planner as _rp_l
                    _hh = (datetime.now(timezone.utc) + timedelta(hours=2))
                    # план бігу пропонуємо ввечері (18-21), коли він точно не на зміні
                    if _hh.weekday() in (0, 6) and 18 <= _hh.hour < 22 and _due("runplan", 3600):
                        if _rp_l.offer():
                            self._log("✅ План бігу запропоновано")
                except Exception as _e_rp:
                    self._log(f"run_planner error: {_e_rp}")

                try:
                    import weekly_review as _wr_l
                    if _wr_l.is_time() and _due("review", 1800) and _wr_l.offer():
                        self._log("✅ Тижневий огляд надіслано")
                except Exception as _e_wr:
                    self._log(f"weekly_review error: {_e_wr}")

                # 17b. ДОКУМЕНТИ / ЛЮДИ / ЕНЕРГІЯ ДНЯ
                #   📄 дедлайни з пошти: скан кожні 30 хв (модуль сам тримає 3 год),
                #      нагадування про терміни — раз на годину
                #   🧠 картки людей: оновлення раз на 4 год (модуль сам — 8 год),
                #      карточка «хтось чекає на крок» — раз на 2 год (модуль — 20 год)
                #   ⚡ питання про енергію дня: перевірка вікна раз на 5 хв
                try:
                    import deadlines_watcher as _dl_l
                    if _due("dl_scan", 1800) and _dl_l.should_scan():
                        self._log("[DEADLINES] scan пошти на терміни")
                        _n_dl = _dl_l.scan()
                        if _n_dl:
                            self._log(f"✅ Дедлайнів знайдено: {_n_dl}")
                    _ndd = _dl_l.check_due_soon() if _due("dl_due", 3600) else 0
                    if _ndd:
                        self._log(f"✅ Нагадувань про дедлайни: {_ndd}")
                except Exception as _e_dl:
                    self._log(f"deadlines_watcher error: {_e_dl}")

                try:
                    import people_memory as _pm_l
                    if _due("pm_refresh", 14400):
                        _n_pm = _pm_l.refresh()
                        if _n_pm:
                            self._log(f"✅ Карток людей оновлено: {_n_pm}")
                    if _due("pm_offer", 7200) and _pm_l.offer():
                        self._log("✅ Картка «людина чекає на крок» надіслана")
                except Exception as _e_pm:
                    self._log(f"people_memory error: {_e_pm}")

                try:
                    import day_mode as _dm_l
                    if _due("day_mode", 300) and _dm_l.ask():
                        self._log("✅ Питання про енергію дня надіслано")
                except Exception as _e_dm:
                    self._log(f"day_mode error: {_e_dm}")

                # 17c. КАЛЕНДАРНИЙ ВАРТОВИЙ (0 AI-кредитів, локальні шаблони).
                #   Раніше нагадувань з календаря було майже нуль: єдиний тригер
                #   event_soon мав дедуп ПО ТИПУ (1 раз на 1.5 год), а не по
                #   кожній події, а monitor._check_event_reminders() взагалі
                #   закоментований. Тут дедуп per-event|stage.
                try:
                    import calendar_watch as _cw_l
                    if _due("cw_tick", 60):
                        _n_cw = _cw_l.tick()
                        if _n_cw:
                            self._log(f"✅ Календарних нагадувань: {_n_cw}")
                    if _due("cw_agenda", 900) and _cw_l.agenda():
                        self._log("✅ Ранкова агенда дня надіслана")
                    if _due("cw_tomorrow", 900) and _cw_l.tomorrow():
                        self._log("✅ Прев'ю на завтра надіслано")
                    # Огляд тижня вперед (нд ввечері / пн зранку, 1 раз на тиждень)
                    if _due("cw_week", 1800) and _cw_l.week():
                        self._log("✅ Огляд тижня надіслано")
                    # Огляд місяця (1-е число зранку, 1 раз на місяць)
                    if _due("cw_month", 3600) and _cw_l.month():
                        self._log("✅ Огляд місяця надіслано")
                    if _due("cw_gc", 86400):
                        _cw_l.gc_sent()
                except Exception as _e_cw:
                    self._log(f"calendar_watch error: {_e_cw}")

                # ─── 17d. ВІДКЛАДЕНІ КНОПКОЮ «🔔 Нагадай пізніше» ──────────
                try:
                    import ai_buttons as _gx_l
                    if _due("gx_tick", 60):
                        _n_gx = _gx_l.tick()
                        if _n_gx:
                            self._log(f"🔔 Відкладених повідомлень надіслано: {_n_gx}")
                    if _due("gx_gc", 86400):
                        _gx_l.gc()
                except Exception as _e_gx:
                    self._log(f"ai_buttons error: {_e_gx}")

                # Процесувати тригери (генеруємо & надсилаємо messages)
                if triggers:
                    for ttype, tdata in triggers:
                        self._log(f"Processing trigger: {ttype}")
                        # Генеруємо та надсилаємо message через message_generator.py
                        try:
                            success = process_and_send_trigger(ttype, tdata)
                            # ВАЖЛИВО: позначаємо спробу НЕЗАЛЕЖНО від успіху для
                            # часо-залежних тригерів (idle_timeout, day_plan тощо).
                            # Раніше mark_trigger_sent викликався тільки при success=True —
                            # якщо _should_send_message legit блокував (напр. idle_timeout
                            # дозволений лише о 6-9/19-23, а зараз інша година), тригер
                            # спрацьовував ЩОСЕКУНДИ нескінченно (лічильник ніколи не скидався),
                            # забиваючи логи і довбаючи GitHub API щосекунди (ризик гонки
                            # даних у draft_store.json/кнопках). Виняток — crypto_move: там
                            # skip може означати "рух є, але <5%", і ми НЕ хочемо глушити
                            # виявлення реального різкого руху протягом cooldown.
                            if ttype != "crypto_move":
                                self._mark_trigger_sent(ttype)
                            elif success:
                                self._mark_trigger_sent(ttype)
                            if success:
                                self._log(f"✅ Message sent for: {ttype}")
                            else:
                                self._log(f"⚠️ Failed to send for: {ttype}")
                        except Exception as e:
                            if ttype != "crypto_move":
                                self._mark_trigger_sent(ttype)
                            self._log(f"❌ Exception in process_and_send_trigger: {e}")
                else:
                    # DEBUG: покажемо що трігери НЕ активні
                    pass
                
                time.sleep(1)
                
            except Exception as e:
                self._log(f"Worker error: {e}")
                time.sleep(5)
    
    def stop(self):
        """Зупинити worker"""
        self.running = False
        self._save_state()
        self._log("Stopped")

# ============ SINGLETON ============

_LISTENER_INSTANCE = None

def get_listener() -> IntelligentListener:
    """Отримати або створити singleton listener"""
    global _LISTENER_INSTANCE
    if _LISTENER_INSTANCE is None:
        _LISTENER_INSTANCE = IntelligentListener()
    return _LISTENER_INSTANCE

def start_listener():
    """Запустити listener в background thread"""
    global _LISTENER_THREAD, _LISTENER_RUNNING
    
    if _LISTENER_RUNNING:
        return
    
    listener = get_listener()
    _LISTENER_RUNNING = True
    
    _LISTENER_THREAD = threading.Thread(target=listener.run, daemon=True)
    _LISTENER_THREAD.start()
    
    print("[LISTENER] Started in background", flush=True)

def stop_listener():
    """Зупинити listener"""
    global _LISTENER_RUNNING
    if _LISTENER_RUNNING:
        listener = get_listener()
        listener.stop()
        _LISTENER_RUNNING = False

def is_listener_running() -> bool:
    """Перевірити чи listener запущений"""
    return _LISTENER_RUNNING

def mark_user_active():
    """Викликається при команді юзера"""
    listener = get_listener()
    listener.mark_user_active()

def set_user_location(location: str):
    """Встановити location вручну"""
    listener = get_listener()
    listener.set_location(location)

def get_listener_status() -> dict:
    """Отримати status для /diag"""
    listener = get_listener()
    return {
        "running": _LISTENER_RUNNING,
        "location": listener.user_location,
        "idle_hours": (time.time() - listener.last_user_activity) / 3600,
        "last_messages": {k: datetime.fromtimestamp(v, _TZ).strftime("%H:%M") 
                         for k, v in listener.last_message_time.items()},
    }

def process_and_send_trigger(trigger_type: str, trigger_data):
    """
    Обробити тригер через message_generator
    Викликається з intelligent_listener при активному тригері
    """
    try:
        from message_generator import process_trigger
        
        listener = get_listener()
        idle = listener._check_idle_timeout()
        location = listener.user_location

        # Перевіряємо реальний статус за Google Calendar (рання/нічна зміна) —
        # manual /set_location більше НЕ є єдиним джерелом правди, лише fallback.
        try:
            import context as _ctx_mod
            _status = _ctx_mod.get_status()
            if _status in ("working_early", "working_night"):
                location = "robota"
            elif _status in ("home", "sleeping", "post_shift"):
                location = "doma"
        except Exception as e:
            print(f"[LISTENER] calendar status check failed, using manual location: {e}", flush=True)
        
        return process_trigger(trigger_type, trigger_data, location, idle)
    except ImportError:
        print("[LISTENER] message_generator not available", flush=True)
        return False
    except Exception as e:
        print(f"[LISTENER] process_and_send_trigger error: {e}", flush=True)
        return False

if __name__ == "__main__":
    # TEST
    listener = get_listener()
    
    print("Testing listener...")
    print(f"VIP emails: {listener._check_vip_emails()}")
    print(f"Crypto moves: {listener._check_crypto_moves()}")
    print(f"Idle: {listener._check_idle_timeout():.1f}h")
    print(f"Time trigger: {listener._check_time_based()}")
    
    print("\nStarting worker for 30 seconds...")
    start_listener()
    time.sleep(30)
    stop_listener()
    
    print("✅ Test complete")

# ========== LIFE OS INTEGRATION ==========
try:
    from life_os_tracker import LifeOSTracker
    _LIFE_OS_AVAILABLE = True
except (ImportError, ModuleNotFoundError):
    _LIFE_OS_AVAILABLE = False

def _check_life_os_sphere(sphere: str) -> bool:
    """
    Перевіряє одну сферу життя.
    Повертає True якщо потребує оповіщення
    """
    if not _LIFE_OS_AVAILABLE:
        return False
    
    try:
        tracker = LifeOSTracker()
        status = tracker.get_life_status()
        sphere_status = status.get(sphere)
        
        if sphere_status:
            msg, should_alert = sphere_status
            return should_alert
    except Exception as e:
        print(f"[LISTENER] Life OS check error: {e}", flush=True)
    
    return False

def _get_life_os_message(sphere: str) -> str:
    """Повертає повідомлення про сферу"""
    if not _LIFE_OS_AVAILABLE:
        return ""
    
    try:
        tracker = LifeOSTracker()
        status = tracker.get_life_status()
        sphere_status = status.get(sphere)
        
        if sphere_status:
            msg, _ = sphere_status
            return msg
    except:
        pass
    
    return ""
