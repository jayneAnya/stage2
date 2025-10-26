# Blue/Green Deployment with Nginx Auto-Failover

A Docker Compose-based blue/green deployment setup with automatic failover using Nginx reverse proxy.

## Architecture Overview

```
┌─────────────┐
│   Client    │
└──────┬──────┘
       │ :8080
       ▼
┌─────────────┐
│   Nginx     │ ◄── Reverse Proxy + Load Balancer
└──────┬──────┘
       │
   ┌───┴────┐
   │        │
   ▼        ▼
┌──────┐ ┌──────┐
│ Blue │ │Green │ ◄── Node.js Applications
│:8081 │ │:8082 │
└──────┘ └──────┘
```

## Features

- **Automatic Failover**: If Blue fails, traffic instantly switches to Green
- **Zero Downtime**: Requests are retried to backup within the same client request
- **Health Monitoring**: Nginx monitors upstream health automatically
- **Manual Access**: Direct ports (8081/8082) for chaos testing

## Quick Start

### 1. Prerequisites

- Docker & Docker Compose installed
- Pre-built application images available

### 2. Setup

Copy the environment template and configure it:

```bash
cp .env.example .env
```

Edit `.env` with your actual values:

```bash
BLUE_IMAGE=ghcr.io/yourorg/app:blue
GREEN_IMAGE=ghcr.io/yourorg/app:green
RELEASE_ID_BLUE=blue-v1.0.0
RELEASE_ID_GREEN=green-v1.0.0
```

### 3. Start Services

```bash
docker-compose up -d
```

Check everything is running:

```bash
docker-compose ps
```

### 4. Test Normal Operation

```bash
curl http://18.118.46.122:8080/version
```

Expected response:
```json
{
  "version": "1.0.0",
  "pool": "blue"
}
```

Headers should include:
- `X-App-Pool: blue`
- `X-Release-Id: blue-v1.0.0`

### 5. Test Failover

Trigger chaos on Blue:

```bash
curl -X POST http://18.118.46.122:8081/chaos/start?mode=error
```

Now test the main endpoint:

```bash
# Should still return 200, but from Green
curl http://18.118.46.122:8080/version
```

Expected response:
```json
{
  "version": "1.0.0",
  "pool": "green"
}
```

Headers should now show:
- `X-App-Pool: green`
- `X-Release-Id: green-v1.0.0`

### 6. Stop Chaos

```bash
curl -X POST http://18.118.46.122:8081/chaos/stop
```

After ~10 seconds (fail_timeout), Blue will be back in rotation.

## Configuration Details

### Nginx Failover Settings

| Setting | Value | Purpose |
|---------|-------|---------|
| `max_fails` | 2 | Mark server down after 2 failures |
| `fail_timeout` | 10s | Wait 10s before retrying failed server |
| `proxy_connect_timeout` | 2s | Fast connection timeout |
| `proxy_read_timeout` | 3s | Fast read timeout |
| `proxy_next_upstream` | error, timeout, http_5xx | Conditions to try backup |

### Port Mapping

| Service | Internal | External | Purpose |
|---------|----------|----------|---------|
| Nginx | 80 | 8080 | Main entry point |
| Blue | 3000 | 8081 | Primary app + chaos control |
| Green | 3000 | 8082 | Backup app + chaos control |

## API Endpoints

### Main Service (via Nginx :8080)

- `GET /version` - Returns app version and pool info
- `GET /healthz` - Health check endpoint

### Direct Access (Blue :8081, Green :8082)

- `POST /chaos/start?mode=error` - Simulate 500 errors
- `POST /chaos/start?mode=timeout` - Simulate timeouts
- `POST /chaos/stop` - Stop chaos simulation

## Monitoring

View Nginx logs:
```bash
docker-compose logs -f nginx
```

View application logs:
```bash
docker-compose logs -f app_blue app_green
```

## Troubleshooting

### Blue stays down after chaos stop

Wait for `fail_timeout` (10s) to expire. Nginx will automatically retry.

### Both services failing

Check if images are pulled correctly:
```bash
docker-compose pull
```

### Headers not showing up

Verify with verbose curl:
```bash
curl -v http://18.118.46.122:8080/version
```

## Testing Scenario

Here's a complete test flow:

```bash
# 1. Verify Blue is active
for i in {1..5}; do
  curl -s http://18.118.46.122:8080/version | grep -o '"pool":"[^"]*"'
done
# Should show: "pool":"blue" (5 times)

# 2. Break Blue
curl -X POST http://18.118.46.122:8081/chaos/start?mode=error

# 3. Verify automatic switch to Green (zero failures)
for i in {1..20}; do
  curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8080/version
  sleep 0.5
done
# Should show: 200 (20 times, no 500s)

# 4. Check all responses are from Green
for i in {1..5}; do
  curl -s http://18.118.46.122:8080/version | grep -o '"pool":"[^"]*"'
done
# Should show: "pool":"green" (5 times)

# 5. Restore Blue
curl -X POST http://18.118.46.122:8081/chaos/stop

# 6. Wait and verify Blue returns
sleep 12
curl -s http://18.118.46.122:8080/version | grep -o '"pool":"[^"]*"'
# Should show: "pool":"blue"
```

## Cleanup

Stop and remove everything:

```bash
docker-compose down
```

Remove volumes (if any):

```bash
docker-compose down -v
```

## License

MIT
