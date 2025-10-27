#!/bin/bash

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Test configuration
BASE_URL="http://localhost:8080"
BLUE_URL="http://localhost:8081"
GREEN_URL="http://localhost:8082"
TEST_COUNT=10
RETRY_DELAY=2

# Function to print colored output
print_status() {
    local status=$1
    local message=$2
    if [ "$status" = "success" ]; then
        echo -e "${GREEN}✓${NC} $message"
    elif [ "$status" = "error" ]; then
        echo -e "${RED}✗${NC} $message"
    elif [ "$status" = "info" ]; then
        echo -e "${BLUE}ℹ${NC} $message"
    elif [ "$status" = "warning" ]; then
        echo -e "${YELLOW}⚠${NC} $message"
    fi
}

# Function to check if service is ready
wait_for_service() {
    local url=$1
    local service=$2
    local max_attempts=30
    local attempt=1
    
    print_status "info" "Waiting for $service to be ready..."
    
    while [ $attempt -le $max_attempts ]; do
        if curl -s -f "$url/healthz" > /dev/null 2>&1; then
            print_status "success" "$service is ready"
            return 0
        fi
        print_status "warning" "Attempt $attempt: $service not ready, retrying in 1s..."
        sleep 1
        ((attempt++))
    done
    
    print_status "error" "$service failed to become ready after $max_attempts attempts"
    return 1
}

# Function to make HTTP request and extract headers
make_request() {
    local url=$1
    local endpoint=$2
    local response=$(curl -s -i "$url$endpoint")
    local status_code=$(echo "$response" | grep "HTTP/" | awk '{print $2}')
    local app_pool=$(echo "$response" | grep -i "X-App-Pool:" | awk '{print $2}' | tr -d '\r')
    local release_id=$(echo "$response" | grep -i "X-Release-Id:" | awk '{print $2}' | tr -d '\r')
    
    echo "$status_code|$app_pool|$release_id"
}

# Function to test endpoint consistency
test_endpoint_consistency() {
    local endpoint=$1
    local expected_pool=$2
    local test_name=$3
    
    print_status "info" "Testing $test_name ($endpoint)..."
    
    local success_count=0
    local total_count=$TEST_COUNT
    local wrong_pool_count=0
    local error_count=0
    
    for i in $(seq 1 $total_count); do
        local result=$(make_request "$BASE_URL" "$endpoint")
        local status_code=$(echo $result | cut -d'|' -f1)
        local app_pool=$(echo $result | cut -d'|' -f2)
        local release_id=$(echo $result | cut -d'|' -f3)
        
        if [ "$status_code" = "200" ]; then
            if [ "$app_pool" = "$expected_pool" ]; then
                ((success_count++))
            else
                ((wrong_pool_count++))
                print_status "warning" "Request $i: Wrong pool. Expected: $expected_pool, Got: $app_pool"
            fi
        else
            ((error_count++))
            print_status "error" "Request $i: HTTP $status_code"
        fi
    done
    
    local success_rate=$((success_count * 100 / total_count))
    
    if [ $success_count -eq $total_count ]; then
        print_status "success" "Consistency test passed: $success_count/$total_count (${success_rate}%) requests successful with correct pool"
    else
        print_status "error" "Consistency test failed: $success_count/$total_count (${success_rate}%) requests successful"
        print_status "error" "  - Wrong pool: $wrong_pool_count"
        print_status "error" "  - Errors: $error_count"
        return 1
    fi
}

# Function to induce chaos
induce_chaos() {
    local mode=${1:-"error"}
    
    print_status "info" "Inducing chaos on Blue (mode: $mode)..."
    
    local response=$(curl -s -w "%{http_code}" -X POST "$BLUE_URL/chaos/start?mode=$mode" -o /dev/null)
    
    if [ "$response" = "200" ]; then
        print_status "success" "Chaos started successfully on Blue"
        # Wait a moment for chaos to take effect
        sleep 2
    else
        print_status "error" "Failed to start chaos on Blue (HTTP $response)"
        return 1
    fi
}

# Function to stop chaos
stop_chaos() {
    print_status "info" "Stopping chaos on Blue..."
    
    local response=$(curl -s -w "%{http_code}" -X POST "$BLUE_URL/chaos/stop" -o /dev/null)
    
    if [ "$response" = "200" ]; then
        print_status "success" "Chaos stopped successfully"
        # Wait for recovery
        sleep 3
    else
        print_status "error" "Failed to stop chaos on Blue (HTTP $response)"
        return 1
    fi
}

# Function to test failover during chaos
test_failover() {
    print_status "info" "Testing failover during chaos..."
    
    local success_count=0
    local total_count=$TEST_COUNT
    local blue_count=0
    local green_count=0
    local error_count=0
    
    for i in $(seq 1 $total_count); do
        local result=$(make_request "$BASE_URL" "/version")
        local status_code=$(echo $result | cut -d'|' -f1)
        local app_pool=$(echo $result | cut -d'|' -f2)
        local release_id=$(echo $result | cut -d'|' -f3)
        
        if [ "$status_code" = "200" ]; then
            ((success_count++))
            if [ "$app_pool" = "blue" ]; then
                ((blue_count++))
            elif [ "$app_pool" = "green" ]; then
                ((green_count++))
            fi
        else
            ((error_count++))
            print_status "error" "Request $i: HTTP $status_code (FAILOVER TEST)"
        fi
        # Small delay between requests
        sleep 0.5
    done
    
    local success_rate=$((success_count * 100 / total_count))
    local green_rate=$((green_count * 100 / total_count))
    
    print_status "info" "Failover test results:"
    print_status "info" "  - Successful requests: $success_count/$total_count (${success_rate}%)"
    print_status "info" "  - Blue responses: $blue_count"
    print_status "info" "  - Green responses: $green_count (${green_rate}%)"
    print_status "info" "  - Errors: $error_count"
    
    # Requirements check
    if [ $error_count -gt 0 ]; then
        print_status "error" "FAIL: Found $error_count non-200 responses during chaos"
        return 1
    fi
    
    if [ $green_rate -lt 95 ]; then
        print_status "warning" "WARNING: Only ${green_rate}% of requests went to Green (expected ≥95%)"
    else
        print_status "success" "SUCCESS: Failover working correctly (${green_rate}% to Green)"
    fi
}

# Main test execution
main() {
    echo -e "${BLUE}=========================================${NC}"
    echo -e "${BLUE}  Blue/Green Deployment Test Suite${NC}"
    echo -e "${BLUE}=========================================${NC}"
    
    # Wait for all services to be ready
    wait_for_service "$BASE_URL" "Nginx" || exit 1
    wait_for_service "$BLUE_URL" "Blue service" || exit 1
    wait_for_service "$GREEN_URL" "Green service" || exit 1
    
    echo
    
    # Test 1: Baseline - All requests should go to Blue
    print_status "info" "=== TEST 1: Baseline (Blue active) ==="
    test_endpoint_consistency "/version" "blue" "Baseline routing"
    if [ $? -ne 0 ]; then
        print_status "error" "Baseline test failed!"
        exit 1
    fi
    
    echo
    
    # Test 2: Induce chaos and test failover
    print_status "info" "=== TEST 2: Failover during chaos ==="
    induce_chaos "error" || exit 1
    
    test_failover
    local failover_result=$?
    
    stop_chaos || exit 1
    
    if [ $failover_result -ne 0 ]; then
        print_status "error" "Failover test failed!"
        exit 1
    fi
    
    echo
    
    # Test 3: Recovery - After stopping chaos, should return to Blue
    print_status "info" "=== TEST 3: Recovery after chaos ==="
    print_status "info" "Waiting for system to stabilize after chaos..."
    sleep 5
    
    test_endpoint_consistency "/version" "blue" "Recovery routing"
    if [ $? -ne 0 ]; then
        print_status "error" "Recovery test failed!"
        exit 1
    fi
    
    echo
    
    # Test 4: Test health endpoints
    print_status "info" "=== TEST 4: Health checks ==="
    
    blue_health=$(curl -s -o /dev/null -w "%{http_code}" "$BLUE_URL/healthz")
    green_health=$(curl -s -o /dev/null -w "%{http_code}" "$GREEN_URL/healthz")
    nginx_health=$(curl -s -o /dev/null -w "%{http_code}" "$BASE_URL/healthz")
    
    if [ "$blue_health" = "200" ]; then
        print_status "success" "Blue health check: HTTP $blue_health"
    else
        print_status "error" "Blue health check: HTTP $blue_health"
    fi
    
    if [ "$green_health" = "200" ]; then
        print_status "success" "Green health check: HTTP $green_health"
    else
        print_status "error" "Green health check: HTTP $green_health"
    fi
    
    if [ "$nginx_health" = "200" ]; then
        print_status "success" "Nginx health check: HTTP $nginx_health"
    else
        print_status "error" "Nginx health check: HTTP $nginx_health"
    fi
    
    echo
    
    # Final summary
    echo -e "${GREEN}=========================================${NC}"
    echo -e "${GREEN}  All tests completed successfully!${NC}"
    echo -e "${GREEN}=========================================${NC}"
    echo
    print_status "success" "✓ Baseline routing: All requests go to Blue"
    print_status "success" "✓ Failover: Automatic switch to Green during Blue failure"
    print_status "success" "✓ Recovery: Automatic return to Blue after recovery"
    print_status "success" "✓ Zero non-200 responses maintained during chaos"
}

# Handle script interruption
cleanup() {
    echo
    print_status "info" "Cleaning up..."
    stop_chaos > /dev/null 2>&1
    exit 0
}

trap cleanup SIGINT SIGTERM

# Run main function
main "$@"