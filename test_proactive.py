#!/usr/bin/env python3
"""Test proactive message dedup logic"""

import os
import json
import time
from datetime import datetime, timezone, timedelta

# Mock
class MockIntelligentAssistant:
    def __init__(self):
        self.dedup_file = "test_proactive_last_send.json"
    
    def should_send_proactive_message(self):
        """Test the dedup logic"""
        now_local = datetime.now(timezone.utc) + timedelta(hours=2)
        current_hour = now_local.strftime("%Y-%m-%d %H")
        
        last_send_ts = 0.0
        last_send_hour = ""
        
        if os.path.exists(self.dedup_file):
            with open(self.dedup_file, "r") as f:
                data = json.load(f) or {}
                last_send_ts = data.get("last_sent_timestamp", 0.0)
                last_send_hour = data.get("last_sent_hour", "")
        
        # Check if already sent this hour
        if last_send_hour == current_hour:
            print(f"❌ Already sent in hour {current_hour}")
            return False
        
        now_ts = now_local.timestamp()
        time_since_last_send = now_ts - last_send_ts
        hour = now_local.hour
        
        should_send = False
        reason = ""
        
        if last_send_ts > 0 and time_since_last_send > 3600:
            should_send = True
            reason = f"idle>{int(time_since_last_send/60)}m"
        elif last_send_ts == 0 and 6 <= hour <= 10:
            should_send = True
            reason = "first_time_morning"
        elif 6 <= hour < 10 and time_since_last_send > 3600:
            should_send = True
            reason = "morning_window"
        elif 17 <= hour <= 20 and time_since_last_send > 3600:
            should_send = True
            reason = "after_work_window"
        
        if should_send:
            print(f"✅ Should send: {reason} (hour={hour}, since_last={int(time_since_last_send/60) if last_send_ts else 'never'}m)")
        else:
            print(f"⚠️ Should NOT send (hour={hour}, reason: doesn't match any window)")
        
        return should_send
    
    def mark_sent(self):
        """Mark message as sent"""
        now_local = datetime.now(timezone.utc) + timedelta(hours=2)
        current_hour = now_local.strftime("%Y-%m-%d %H")
        
        dedup_data = {
            "last_sent_timestamp": now_local.timestamp(),
            "last_sent_hour": current_hour,
            "last_sent_iso": now_local.isoformat()
        }
        
        with open(self.dedup_file, "w") as f:
            json.dump(dedup_data, f, indent=2)
        
        print(f"💾 Marked sent at {current_hour}")

# Test
if __name__ == "__main__":
    print("\n🧪 Testing proactive message dedup logic...\n")
    
    ma = MockIntelligentAssistant()
    
    # Clean up
    if os.path.exists(ma.dedup_file):
        os.remove(ma.dedup_file)
    
    # Test 1: First time (should send)
    print("Test 1: First message ever")
    if ma.should_send_proactive_message():
        ma.mark_sent()
    print()
    
    # Test 2: Same hour (should NOT send)
    print("Test 2: Same hour, immediately after")
    if ma.should_send_proactive_message():
        ma.mark_sent()
    print()
    
    # Test 3: Simulate old timestamp
    print("Test 3: Simulate 2 hours ago")
    now_local = datetime.now(timezone.utc) + timedelta(hours=2)
    old_time = now_local - timedelta(hours=2)
    
    dedup_data = {
        "last_sent_timestamp": old_time.timestamp(),
        "last_sent_hour": (old_time - timedelta(hours=1)).strftime("%Y-%m-%d %H"),
        "last_sent_iso": old_time.isoformat()
    }
    
    with open(ma.dedup_file, "w") as f:
        json.dump(dedup_data, f, indent=2)
    
    print(f"Manually set last sent to {old_time.isoformat()}")
    
    if ma.should_send_proactive_message():
        ma.mark_sent()
    print()
    
    # Clean up
    if os.path.exists(ma.dedup_file):
        os.remove(ma.dedup_file)
    
    print("✅ Tests completed!")
