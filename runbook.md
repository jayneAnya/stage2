# Blue-Green Deployment Runbook

## Alert Types and Meanings

### 1. Failover Detected Alert
**Message**: `FAILOVER DETECTED - Blue → Green` (or vice versa)

**What it means**:
- The primary pool has failed health checks
- Nginx has automatically switched traffic to the backup pool
- This indicates potential issues with the primary deployment

**Operator Actions**:
1. **Immediate Response**:
   - Check health of failed pool containers: `docker-compose ps app_blue app_green`
   - Examine container logs: `docker-compose logs app_blue` (or app_green)
   - Verify resource usage: `docker stats`

2. **Investigation**:
   - Check application-specific logs in the failing container
   - Verify database connections if applicable
   - Check for recent deployments or configuration changes

3. **Recovery**:
   - Restart failed containers: `docker-compose restart app_blue`
   - Monitor health checks until stable
   - Manually verify endpoints: `curl http://localhost:8081/healthz`

4. **Post-Recovery**:
   - The system will continue serving from backup pool
   - No immediate action needed to switch back
   - Plan maintenance window to restore primary

### 2. High Error Rate Alert
**Message**: `HIGH ERROR RATE DETECTED - X.X% (Threshold: Y%)`

**What it means**:
- More than 2% (configurable) of requests are returning 5xx errors
- This could indicate partial degradation rather than complete failure
- The system may still be serving some traffic successfully

**Operator Actions**:
1. **Immediate Assessment**:
   - Check current error rate in logs
   - Identify patterns in failing requests
   - Determine if specific endpoints are affected

2. **Diagnosis**:
   - Examine application logs for exceptions
   - Check upstream dependencies (databases, APIs)
   - Monitor resource metrics (CPU, memory, disk)

3. **Containment**:
   - If errors persist, consider manual failover
   - Scale up resources if needed
   - Implement circuit breakers for failing dependencies

4. **Resolution**:
   - Deploy hotfix if code issue identified
   - Restart containers if memory leaks suspected
   - Adjust timeouts or retry policies

## Maintenance Procedures

### Planned Maintenance
Before planned deployments or maintenance:
1. Set `MAINTENANCE_MODE=true` in .env
2. Restart watcher: `docker-compose restart alert_watcher`
3. Perform maintenance activities
4. Set `MAINTENANCE_MODE=false` after completion
5. Verify alert functionality

### Manual Failover Testing
To test failover detection:
```bash
# Stop primary pool
docker-compose stop app_blue

# Generate traffic to trigger failover
for i in {1..10}; do curl http://localhost:8080/; done

# Verify alert in Slack
# Restart primary
docker-compose start app_blue