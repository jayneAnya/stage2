
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
last_pool = os.getenv('ACTIVE_POOL', 'blue')
error_window = deque(maxlen=WINDOW_SIZE)
last_alert_time = 0

def send_slack_alert(message):
    """Send alert to Slack"""
    global last_alert_time
    
    # Check cooldown
    current_time = time.time()
    if current_time - last_alert_time < ALERT_COOLDOWN_SEC:
        print(f"Alert suppressed (cooldown): {message}")
        return
    
    payload = {
        "text": f" *Blue/Green Alert* \n{message}",
        "username": "Blue/Green Monitor",
        "icon_emoji": ":warning:"
    }
    
    try:
        response = requests.post(SLACK_WEBHOOK_URL, json=payload)
        if response.status_code == 200:
            print(f"Alert sent: {message}")
            last_alert_time = current_time
        else:
            print(f"Failed to send alert: {response.status_code}")
    except Exception as e:
        print(f"Error sending to Slack: {e}")

def process_log_line(line):
    """Process a single log line and check for alerts"""
    global last_pool
    
    try:
        log_data = json.loads(line)
        
        # Extract fields
        pool = log_data.get('pool', 'unknown')
        upstream_status = log_data.get('upstream_status', '')
        status = log_data.get('status', '')
        
        # Track errors for rate calculation
        is_error = upstream_status.startswith('5') or status.startswith('5')
        error_window.append(1 if is_error else 0)
        
        # Check for pool failover
        if pool != 'unknown' and pool != last_pool:
            message = f"Failover detected: {last_pool} → {pool}\nTime: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            send_slack_alert(message)
            last_pool = pool
        
        # Check error rate
        if len(error_window) >= WINDOW_SIZE:
            error_rate = (sum(error_window) / len(error_window)) * 100
            if error_rate > ERROR_RATE_THRESHOLD:
                message = f"High error rate: {error_rate:.1f}% (threshold: {ERROR_RATE_THRESHOLD}%)\nLast {WINDOW_SIZE} requests"
                send_slack_alert(message)
                # Clear window after alert to avoid spam
                error_window.clear()
                
    except json.JSONDecodeError:
        # Skip non-JSON lines (like nginx startup messages)
        pass
    except Exception as e:
        print(f"Error processing log: {e}")

def main():
    """Main loop to watch nginx logs"""
    log_file = "/var/log/nginx/access.log"
    
    print("Starting Blue/Green log watcher...")
    print(f"Config: {ERROR_RATE_THRESHOLD}% error threshold, {WINDOW_SIZE} request window")
    
    # Wait for log file to exist
    while not os.path.exists(log_file):
        print("Waiting for nginx log file...")
        time.sleep(2)
    
    # Tail the log file
    with open(log_file, 'r') as file:
        # Go to end of file
        file.seek(0, 2)
        
        while True:
            line = file.readline()
            if line:
                process_log_line(line.strip())
            else:
                time.sleep(0.1)

if __name__ == "__main__":
    main()
