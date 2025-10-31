#!/usr/bin/env python3
import os
import time
import json
import requests
from collections import deque
from datetime import datetime

# Configuration from environment variables
SLACK_WEBHOOK_URL = os.getenv('SLACK_WEBHOOK_URL')
ERROR_RATE_THRESHOLD = float(os.getenv('ERROR_RATE_THRESHOLD', 2.0))
WINDOW_SIZE = int(os.getenv('WINDOW_SIZE', 200))
ALERT_COOLDOWN_SEC = int(os.getenv('ALERT_COOLDOWN_SEC', 300))

# State tracking
last_pool = None  # Start with None to detect first pool
error_window = deque(maxlen=WINDOW_SIZE)
last_alert_time = {}  # Separate cooldowns per alert type
failover_count = 0

def send_slack_alert(message, alert_type='default'):
    """Send alert to Slack with per-type cooldown"""
    global last_alert_time
    
    # Check cooldown for this alert type
    current_time = time.time()
    last_time = last_alert_time.get(alert_type, 0)
    
    if current_time - last_time < ALERT_COOLDOWN_SEC:
        print(f"Alert suppressed (cooldown): {message}")
        return False
    
    payload = {
        "text": f"🚨 *Blue/Green Alert*\n{message}",
        "username": "Blue/Green Monitor",
        "icon_emoji": ":warning:"
    }
    
    try:
        response = requests.post(SLACK_WEBHOOK_URL, json=payload)
        if response.status_code == 200:
            print(f"✅ Alert sent: {message}")
            last_alert_time[alert_type] = current_time
            return True
        else:
            print(f"❌ Failed to send alert: {response.status_code}")
            return False
    except Exception as e:
        print(f"❌ Error sending to Slack: {e}")
        return False

def detect_failover(log_data):
    """Detect failover by checking upstream_status and upstream_addr patterns"""
    upstream_status = log_data.get('upstream_status', '')
    upstream_addr = log_data.get('upstream_addr', '')
    
    # Failover detected if:
    # 1. Multiple upstream addresses (tried multiple servers)
    # 2. Multiple statuses with first one being an error
    if ',' in upstream_addr and ',' in upstream_status:
        statuses = [s.strip() for s in upstream_status.split(',')]
        addrs = [a.strip() for a in upstream_addr.split(',')]
        
        # Check if first attempt failed (5xx or timeout)
        if statuses[0].startswith('5') or statuses[0] == '':
            return True, addrs[0], addrs[-1]
    
    return False, None, None

def process_log_line(line):
    """Process a single log line and check for alerts"""
    global last_pool, failover_count
    
    try:
        log_data = json.loads(line)
        
        # Extract fields
        pool = log_data.get('pool', 'unknown')
        upstream_status = log_data.get('upstream_status', '')
        status = log_data.get('status', '')
        timestamp = log_data.get('time', datetime.now().isoformat())
        
        # Initialize last_pool if first log
        if last_pool is None:
            last_pool = pool
            print(f"📊 Initial pool detected: {pool}")
        
        # Track errors for rate calculation
        is_error = upstream_status.startswith('5') or status.startswith('5')
        error_window.append(1 if is_error else 0)
        
        # Check for failover pattern in upstream responses
        is_failover, primary_addr, backup_addr = detect_failover(log_data)
        
        if is_failover:
            failover_count += 1
            message = (
                f"🔄 *Failover Detected* (#{failover_count})\n"
                f"• Time: {timestamp}\n"
                f"• Primary failed: {primary_addr}\n"
                f"• Backup used: {backup_addr}\n"
                f"• Pool: {pool}\n"
                f"• Statuses: {log_data.get('upstream_status')}"
            )
            send_slack_alert(message, alert_type='failover')
            print(f"🔄 Failover #{failover_count}: {primary_addr} → {backup_addr}")
        
        # Check for pool change (different detection method)
        if pool != 'unknown' and pool != last_pool:
            message = (
                f"🔀 *Pool Change Detected*\n"
                f"• Changed: {last_pool} → {pool}\n"
                f"• Time: {timestamp}\n"
                f"• Total failovers so far: {failover_count}"
            )
            send_slack_alert(message, alert_type='pool_change')
            print(f"🔀 Pool changed: {last_pool} → {pool}")
            last_pool = pool
        
        # Check error rate
        if len(error_window) >= WINDOW_SIZE:
            error_rate = (sum(error_window) / len(error_window)) * 100
            if error_rate > ERROR_RATE_THRESHOLD:
                message = (
                    f"⚠️ *High Error Rate*\n"
                    f"• Error rate: {error_rate:.1f}% (threshold: {ERROR_RATE_THRESHOLD}%)\n"
                    f"• Window: Last {WINDOW_SIZE} requests\n"
                    f"• Current pool: {pool}"
                )
                send_slack_alert(message, alert_type='error_rate')
                print(f"⚠️ High error rate: {error_rate:.1f}%")
                # Clear window after alert to avoid spam
                error_window.clear()
        
        # Log status periodically (every 50 requests)
        if len(error_window) % 50 == 0 and len(error_window) > 0:
            error_rate = (sum(error_window) / len(error_window)) * 100
            print(f"📈 Status: pool={pool}, error_rate={error_rate:.1f}%, failovers={failover_count}")
                
    except json.JSONDecodeError:
        # Skip non-JSON lines (like nginx startup messages)
        pass
    except Exception as e:
        print(f"❌ Error processing log: {e}")
        print(f"   Line: {line[:100]}...")

def main():
    """Main loop to watch nginx logs"""
    log_file = "/var/log/nginx/access.log"
    
    print("=" * 60)
    print("🚀 Starting Blue/Green Log Watcher")
    print("=" * 60)
    print(f"📋 Configuration:")
    print(f"   • Error threshold: {ERROR_RATE_THRESHOLD}%")
    print(f"   • Window size: {WINDOW_SIZE} requests")
    print(f"   • Alert cooldown: {ALERT_COOLDOWN_SEC}s")
    print(f"   • Slack webhook: {'✅ Configured' if SLACK_WEBHOOK_URL else '❌ Not configured'}")
    print("=" * 60)
    
    # Wait for log file to exist
    while not os.path.exists(log_file):
        print("⏳ Waiting for nginx log file...")
        time.sleep(2)
    
    print(f"✅ Log file found: {log_file}")
    print("👀 Watching for events...\n")
    
    # Tail the log file
    with open(log_file, 'r') as file:
        # Go to end of file (start fresh, or use file.seek(0, 0) to read from beginning)
        file.seek(0, 2)
        
        while True:
            line = file.readline()
            if line:
                process_log_line(line.strip())
            else:
                time.sleep(0.1)

if __name__ == "__main__":
    main()