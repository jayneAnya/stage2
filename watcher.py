#!/usr/bin/env python3
import os
import time
import re
import requests
from collections import deque
from datetime import datetime
import logging

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class LogWatcher:
    def __init__(self):
        self.slack_webhook = os.getenv('SLACK_WEBHOOK_URL')
        self.error_threshold = float(os.getenv('ERROR_RATE_THRESHOLD', 2))
        self.window_size = int(os.getenv('WINDOW_SIZE', 200))
        self.cooldown_sec = int(os.getenv('ALERT_COOLDOWN_SEC', 300))
        self.maintenance_mode = os.getenv('MAINTENANCE_MODE', 'false').lower() == 'true'
        self.active_pool = os.getenv('ACTIVE_POOL', 'blue')
        
        # State tracking
        self.last_pool = None
        self.request_window = deque(maxlen=self.window_size)
        self.last_alert_time = {}
        self.last_file_position = 0
        self.file_checked = False
        
        # Log pattern for nginx custom format
        self.log_pattern = re.compile(
            r'pool=(?P<pool>\S+)\s+'
            r'release=(?P<release>\S+)\s+'
            r'upstream_status=(?P<upstream_status>\S+)\s+'
            r'upstream_addr=(?P<upstream_addr>[\d\.:]+)\s+'
            r'request_time=(?P<request_time>[\d\.]+)\s+'
            r'upstream_response_time=(?P<upstream_response_time>[\d\.]+)'
        )
        
        logger.info(f"LogWatcher initialized: threshold={self.error_threshold}%, "
                   f"window={self.window_size}, cooldown={self.cooldown_sec}s")

    def parse_log_line(self, line):
        """Parse nginx log line and extract relevant fields"""
        try:
            match = self.log_pattern.search(line)
            if match:
                data = match.groupdict()
                # Clean up the data
                if data['pool'] == '-':
                    data['pool'] = 'unknown'
                if data['upstream_status'] == '-':
                    data['upstream_status'] = '000'
                return data
        except Exception as e:
            logger.debug(f"Failed to parse log line: {e}")
        return None

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
        if self.maintenance_mode:
            logger.info(f"Maintenance mode: suppressing {alert_type} alert")
            return False
            
        if not self.can_send_alert(alert_type):
            logger.info(f"Cooldown active: skipping {alert_type} alert")
            return False
            
        if not self.slack_webhook:
            logger.error("SLACK_WEBHOOK_URL not configured")
            return False

        # Determine alert color and emoji based on type
        if "FAILOVER" in alert_type:
            color = "danger"
            icon_emoji = ":arrows_counterclockwise:"
            title = "Failover Detected"
        else:
            color = "warning"
            icon_emoji = ":chart_with_upwards_trend:"
            title = "High Error Rate"

        payload = {
            "text": f"{icon_emoji} {title}",
            "username": "Blue-Green Monitor",
            "icon_emoji": icon_emoji,
            "attachments": [
                {
                    "color": color,
                    "text": message,
                    "fields": [
                        {
                            "title": "Timestamp",
                            "value": datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC'),
                            "short": True
                        },
                        {
                            "title": "Environment",
                            "value": self.active_pool,
                            "short": True
                        }
                    ],
                    "footer": "Blue-Green Deployment Monitor"
                }
            ]
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
            current_pool != 'unknown' and 
            self.last_pool != 'unknown'):
            
            failover_type = f"{self.last_pool.upper()}_TO_{current_pool.upper()}"
            message = f"Traffic has failed over from *{self.last_pool}* to *{current_pool}*\n\n"
            message += "This usually indicates issues with the primary pool. Please check:\n"
            message += "• Container health and logs\n"
            message += "• Resource utilization\n"
            message += "• Application errors"
            
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
            message = f"Error rate has exceeded threshold: *{error_rate:.1f}%* (threshold: {self.error_threshold}%)\n\n"
            message += f"Window: {len(self.request_window)} requests\n"
            message += "Please investigate upstream services for issues."
            
            if self.send_slack_alert(message, "HIGH_ERROR_RATE"):
                logger.warning(f"High error rate alert: {error_rate:.1f}%")
                return True
        return False

    def process_log_line(self, line):
        """Process a single log line"""
        data = self.parse_log_line(line)
        if not data:
            return

        pool = data.get('pool', 'unknown')
        status = data.get('upstream_status', '000')
        
        # Update request window for error rate calculation
        self.request_window.append(status)
        
        # Detect failover
        if pool != 'unknown':
            self.detect_failover(pool)
            self.last_pool = pool
        
        # Monitor error rate (check every 10 requests to reduce load)
        if len(self.request_window) % 10 == 0:
            self.monitor_error_rate()

    def read_new_lines(self, log_file_path):
        """Read new lines from log file using polling"""
        try:
            if not os.path.exists(log_file_path):
                if not self.file_checked:
                    logger.info(f"Waiting for log file: {log_file_path}")
                    self.file_checked = True
                return []
            
            with open(log_file_path, 'r') as file:
                # Get current file size
                file.seek(0, 2)  # Seek to end
                current_size = file.tell()
                
                # If file was rotated or we haven't started reading
                if current_size < self.last_file_position:
                    self.last_file_position = 0
                    logger.info("Log file rotated, resetting position")
                
                # If no new data, return empty
                if current_size <= self.last_file_position:
                    return []
                
                # Read new data
                file.seek(self.last_file_position)
                new_lines = file.readlines()
                self.last_file_position = file.tell()
                
                return new_lines
                
        except Exception as e:
            logger.error(f"Error reading log file: {e}")
            return []

    def run(self):
        """Main monitoring loop using polling"""
        log_file_path = '/var/log/nginx/access.log'
        logger.info(f"Starting log watcher with polling on {log_file_path}")
        
        # Initial wait for nginx to start logging
        time.sleep(5)
        
        poll_count = 0
        while True:
            try:
                # Read new lines
                new_lines = self.read_new_lines(log_file_path)
                
                # Process each new line
                for line in new_lines:
                    line = line.strip()
                    if line:
                        self.process_log_line(line)
                
                # Log status periodically
                poll_count += 1
                if poll_count % 60 == 0:  # Every ~30 seconds
                    logger.info(f"Monitoring active. Window size: {len(self.request_window)}")
                
                # Sleep before next poll
                time.sleep(0.5)  # Poll twice per second
                
            except Exception as e:
                logger.error(f"Error in main loop: {e}")
                time.sleep(5)

if __name__ == '__main__':
    watcher = LogWatcher()
    watcher.run()