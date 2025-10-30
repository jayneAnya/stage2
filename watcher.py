#!/usr/bin/env python3
import os
import time
import json
import requests
from collections import deque
from datetime import datetime
import logging

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class LogWatcher:
    def __init__(self):
        self.slack_webhook = os.getenv('SLACK_WEBHOOK_URL')
        self.error_threshold = float(os.getenv('ERROR_RATE_THRESHOLD', 2))
        self.window_size = int(os.getenv('WINDOW_SIZE', 200))
        self.cooldown_sec = int(os.getenv('ALERT_COOLDOWN_SEC', 300))
        self.active_pool = os.getenv('ACTIVE_POOL', 'blue')
        
        # State tracking
        self.last_pool = None
        self.request_window = deque(maxlen=self.window_size)
        self.last_alert_time = {}
        
        logger.info(f"LogWatcher started: threshold={self.error_threshold}%, window={self.window_size}, cooldown={self.cooldown_sec}s")

    def is_server_error(self, status_code):
        """Check if status code is a 5xx error"""
        try:
            return 500 <= int(status_code) < 600
        except (ValueError, TypeError):
            return False

    def calculate_error_rate(self):
        """Calculate current error rate in the window"""
        if not self.request_window:
            return 0.0
        
        error_count = sum(1 for status in self.request_window if self.is_server_error(status))
        return (error_count / len(self.request_window)) * 100

    def can_send_alert(self, alert_type):
        """Check if we can send alert (respect cooldown)"""
        now = time.time()
        last_time = self.last_alert_time.get(alert_type, 0)
        return (now - last_time) >= self.cooldown_sec

    def send_slack_alert(self, message, alert_type):
        """Send alert to Slack"""
        if not self.can_send_alert(alert_type):
            logger.info(f"Cooldown active: skipping {alert_type} alert")
            return False
            
        if not self.slack_webhook:
            logger.error("SLACK_WEBHOOK_URL not configured")
            return False

        payload = {
            "text": message,
            "username": "Blue-Green Monitor",
            "icon_emoji": ":warning:"
        }

        try:
            response = requests.post(
                self.slack_webhook,
                json=payload,
                headers={'Content-Type': 'application/json'},
                timeout=10
            )
            if response.status_code == 200:
                self.last_alert_time[alert_type] = time.time()
                logger.info(f"Slack alert sent: {alert_type}")
                return True
            else:
                logger.error(f"Slack API error: {response.status_code}")
                return False
        except Exception as e:
            logger.error(f"Failed to send Slack alert: {e}")
            return False

    def detect_failover(self, current_pool):
        """Detect if a failover has occurred"""
        if (self.last_pool and 
            self.last_pool != current_pool and 
            current_pool and 
            current_pool != 'null'):
            
            failover_type = f"{self.last_pool.upper()}_TO_{current_pool.upper()}"
            message = f"🚨 FAILOVER DETECTED\n\n*From:* {self.last_pool}\n*To:* {current_pool}\n*Time:* {datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')}\n\nTraffic has automatically failed over. Check health of {self.last_pool} pool."
            
            if self.send_slack_alert(message, f"FAILOVER_{failover_type}"):
                logger.info(f"Failover detected: {self.last_pool} -> {current_pool}")
                return True
        return False

    def monitor_error_rate(self):
        """Monitor and alert on error rate breaches"""
        if len(self.request_window) < self.window_size:
            return False
            
        error_rate = self.calculate_error_rate()
        
        if error_rate > self.error_threshold:
            message = f"⚠️ HIGH ERROR RATE DETECTED\n\n*Current Rate:* {error_rate:.1f}%\n*Threshold:* {self.error_threshold}%\n*Window Size:* {self.window_size} requests\n*Time:* {datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')}\n\nInvestigate upstream services for issues."
            
            if self.send_slack_alert(message, "HIGH_ERROR_RATE"):
                logger.warning(f"High error rate alert: {error_rate:.1f}%")
                return True
        return False

    def process_log_line(self, line):
        """Process a single JSON log line"""
        try:
            # Try to parse as JSON
            data = json.loads(line)
            
            pool = data.get('pool')
            status = data.get('upstream_status')
            
            if not pool or not status:
                return

            # Update request window for error rate calculation
            self.request_window.append(status)
            
            # Detect failover
            if pool and pool != 'null':
                self.detect_failover(pool)
                self.last_pool = pool
            
            # Monitor error rate periodically
            if len(self.request_window) % 10 == 0:
                self.monitor_error_rate()
                
        except json.JSONDecodeError:
            # Skip non-JSON lines
            pass
        except Exception as e:
            logger.debug(f"Error processing line: {e}")

    def tail_log_file(self):
        """Tail the log file using polling"""
        log_file_path = '/var/log/nginx/access.log'
        
        # Wait for log file to exist
        while not os.path.exists(log_file_path):
            logger.info(f"Waiting for log file: {log_file_path}")
            time.sleep(2)
        
        logger.info(f"Starting to monitor: {log_file_path}")
        last_size = 0
        
        while True:
            try:
                current_size = os.path.getsize(log_file_path)
                
                if current_size < last_size:
                    # File was rotated
                    last_size = 0
                    logger.info("Log file rotated, resetting position")
                
                if current_size > last_size:
                    # New content available
                    with open(log_file_path, 'r') as file:
                        file.seek(last_size)
                        new_lines = file.readlines()
                        last_size = file.tell()
                        
                        for line in new_lines:
                            line = line.strip()
                            if line:
                                self.process_log_line(line)
                
                time.sleep(1)  # Poll every second
                
            except FileNotFoundError:
                logger.error("Log file not found, waiting...")
                time.sleep(5)
            except Exception as e:
                logger.error(f"Error reading log file: {e}")
                time.sleep(5)

    def run(self):
        """Main monitoring loop"""
        self.tail_log_file()

def main():
    watcher = LogWatcher()
    watcher.run()

if __name__ == '__main__':
    main()