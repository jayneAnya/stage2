# Implementation Decisions & Reasoning

## Overview

This document explains the key decisions made during implementation of the Blue/Green deployment system.

## Architecture Decisions

### 1. Static vs Dynamic Nginx Configuration

**Decision**: Used a static nginx.conf with Blue as primary and Green as backup.

**Reasoning**:
- The task specifies "Blue is active by default" - this naturally maps to the primary/backup pattern
- Nginx's `backup` directive is perfect for this use case
- Simpler than templating - fewer moving parts means fewer failure points
- The automatic failover handles the "switching" without manual config changes
- Easy to understand and debug

**Alternative Considered**: 
- Using `envsubst` to template the active pool
- Rejected because it adds complexity without real benefit for automatic failover

### 2. Timeout Values

**Decision**: 
```nginx
proxy_connect_timeout 2s;
proxy_send_timeout 3s;
proxy_read_timeout 3s;
```

**Reasoning**:
- Tight timeouts ensure failures are detected quickly (within single-digit seconds)
- Task requires failover to happen "immediately"
- 2-3 second timeouts strike a balance between:
  - Fast enough to detect real failures
  - Loose enough to avoid false positives on slow network
- Total request time: 2s (connect) + 3s (read) = 5s max before retry

**Why not 1s?**: Too aggressive - could cause false positives under load

**Why not 5s+?**: Too slow - task emphasizes "immediate" failover

### 3. Failover Trigger Conditions

**Decision**:
```nginx
proxy_next_upstream error timeout http_500 http_502 http_503 http_504;
```

**Reasoning**:
- `error` - catches connection failures
- `timeout` - catches slow/hanging responses
- `http_5xx` - catches application errors (chaos mode returns 500)
- Covers all failure modes mentioned in the task
- Does NOT retry on 4xx (client errors) - those should propagate

### 4. max_fails and fail_timeout

**Decision**:
```nginx
server app_blue:3000 max_fails=2 fail_timeout=10s;
```

**Reasoning**:
- `max_fails=2` - Two strikes and you're out. Not too aggressive (avoids marking healthy servers down on transient issues), not too lenient (reacts quickly)
- `fail_timeout=10s` - Short enough to recover quickly, long enough to avoid flapping
- After 10s, Nginx will try Blue again (automatic recovery)

### 5. Port Mapping Strategy

**Decision**:
```yaml
nginx:
  ports: ["8080:80"]
app_blue:
  ports: ["8081:3000"]
app_green:
  ports: ["8082:3000"]
```

**Reasoning**:
- 8080 - Standard HTTP alt port, main entry point
- 8081/8082 - Direct app access for chaos testing (task requirement)
- Internal apps run on 3000 (typical Node.js convention)
- Clean separation: public (8080) vs testing (8081/8082)

### 6. Docker Network

**Decision**: Single custom bridge network `app_network`

**Reasoning**:
- All containers need to communicate
- Custom network provides:
  - DNS resolution (app_blue, app_green, nginx)
  - Network isolation from other Docker projects
  - Predictable container naming
- Bridge driver is lightweight and sufficient for single-host deployment

### 7. Health Checks in Docker Compose

**Decision**: Added healthcheck to app containers

```yaml
healthcheck:
  test: ["CMD", "wget", "--quiet", "--tries=1", "--spider", "http://localhost:3000/healthz"]
  interval: 10s
  timeout: 3s
```

**Reasoning**:
- Provides visibility in `docker-compose ps`
- Ensures containers are actually ready before Nginx tries them
- Aligns with Nginx's health checking
- `wget` is available in most base images (lightweight)

**Note**: This is Docker-level health checking. Nginx does its own application-level checks.

### 8. Header Forwarding

**Decision**: Rely on Nginx's default header forwarding behavior

**Reasoning**:
- By default, Nginx forwards response headers from upstream
- No need to explicitly add or manipulate headers
- Simpler config = fewer bugs
- Headers `X-App-Pool` and `X-Release-Id` come from the app, we just pass them through

**Avoided**: Using `add_header` or `proxy_set_header` for response headers - these would override app headers

### 9. Environment Variable Strategy

**Decision**: Pass env vars directly to containers

```yaml
environment:
  - APP_POOL=blue
  - RELEASE_ID=${RELEASE_ID_BLUE}
```

**Reasoning**:
- Apps