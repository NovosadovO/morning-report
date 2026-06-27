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
        """Отримати события за 1-2 години"""
        # TODO: Google Calendar API
        # Поки повертаємо порожній list
        return []
    
    def _check_crypto_moves(self) -> dict:
        """Перевірити BTC/ETH/AVAX/ONDO за 1 годину"""
        try:
            url = "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin,ethereum,avalanche-2,ondo&vs_currencies=usd&include_24h_change=true"
            req = urllib.request.Request(url, headers={"User-Agent": "OlegBot"})
            
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read())
                
                moves = {}
                for coin_id, coin_name in [("bitcoin", "BTC"), ("ethereum", "ETH"), 
                                          ("avalanche-2", "AVAX"), ("ondo", "ONDO")]:
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
                
                # 1. EMAIL (кожні 30 сек для DEBUG)
                if now - self.last_email_check > 30:
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
                
                # 2. CALENDAR (кожні 40 сек для DEBUG)
                if now - self.last_calendar_check > 40:
                    events = self._check_upcoming_events()
                    if events and self._should_send_trigger("event_soon", 2.0):
                        triggers.append(("event_soon", events))
                        self._log(f"TRIGGER: event_soon ({len(events)} подій)")
                    self.last_calendar_check = now
                
                # 3. CRYPTO (кожні 35 сек для DEBUG)
                if now - self.last_crypto_check > 35:
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
                
                # Процесувати тригери (генеруємо & надсилаємо messages)
                if triggers:
                    for ttype, tdata in triggers:
                        self._log(f"Processing trigger: {ttype}")
                        # Генеруємо та надсилаємо message через message_generator.py
                        try:
                            success = process_and_send_trigger(ttype, tdata)
                            if success:
                                self._mark_trigger_sent(ttype)
                                self._log(f"✅ Message sent for: {ttype}")
                            else:
                                self._log(f"⚠️ Failed to send for: {ttype}")
                        except Exception as e:
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
