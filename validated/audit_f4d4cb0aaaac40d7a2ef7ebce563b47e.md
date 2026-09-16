Confirmed root cause: `authenticated_websocket_handler` and `authenticated_filter_websocket_handler` check the API key via `state.auth.get_application_for_key(&api_key)` and, on failure, immediately return a `401` with no throttling of any kind.### Title
Missing Rate Limiting on WebSocket Proxy API Key Authentication Enables Brute-Force Guessing of Weak API Keys - (File: crates/infra/websocket-proxy/src/server.rs)

### Summary
The `websocket-proxy` binary's authenticated WebSocket endpoints (`/ws/{api_key}` and `/ws/{api_key}/filter`) validate the API key on every request but apply no throttling to failed authentication attempts. An anonymous RPC client can send unlimited API-key guesses per second and only incur a `401 Unauthorized` response with no lockout, delay, or per-IP guess counter, mirroring the CWE-307/CWE-521 pattern in the reference advisory (missing guess-rate limiting on a webhook secret).

### Finding Description
`authenticated_websocket_handler` and `authenticated_filter_websocket_handler` look up the supplied `api_key` path segment against `Authentication::get_application_for_key` and immediately return `401` on a miss: [1](#0-0) [2](#0-1) 

The only rate limiting present in this service is `InMemoryRateLimit`, which is a connection-slot semaphore keyed by client IP, and it is exercised solely on the *successful*-auth branch inside `websocket_handler` (i.e., after the API key has already been validated): [3](#0-2) 

The failed-auth path only increments a Prometheus counter (`unauthorized_requests`), which has no effect on request admission: [4](#0-3) 

The API-key store itself is a simple exact-match `HashMap<String, String>` with no minimum length/entropy enforcement, and keys are supplied as plain CLI/env arguments (`<app>:<key>`), so weak or short operator-chosen keys are possible: [5](#0-4) [6](#0-5) 

Because failed guesses never consume a rate-limit ticket, an attacker can issue an unbounded number of concurrent/sequential HTTP requests to `/ws/{guess}` from many source IPs (or a single IP up to the connection limit's request rate, since only successful upgrades hold a ticket) to brute-force a weak key, exactly analogous to the reported Telegram webhook flaw where "auth previously rejected bad secrets but did not throttle repeated guesses."

### Impact Explanation
A successful guess grants the attacker an authenticated identity (`app`) and unauthorized access to the proxied real-time WebSocket feed (e.g., flashblocks/pending-block broadcast data) that the operator intended to gate behind the API key. This is unauthorized access to a privileged data path reachable by any anonymous RPC/WebSocket client, consistent with the "public ... WebSocket query paths" reachable-surface category. Depending on deployment, this could expose paid/rate-limited flashblocks pending-state data to unauthorized parties, undermining the confidentiality/access-control guarantee the API-key gate exists to provide.

### Likelihood Explanation
Exploitability depends entirely on operator-chosen key strength: if API keys are short or predictable, brute-forcing them at line rate (no throttling on the guess path itself) is straightforward from a single unprivileged network client. If operators use long random keys, practical exploitation likelihood drops significantly. The report's original CVSS (`AC:H`, no privileges required) reflects exactly this dependency on secret strength.

### Recommendation
Apply per-IP (and/or global) throttling to failed authentication attempts in `authenticated_websocket_handler`/`authenticated_filter_websocket_handler` *before* returning the `401`, independent of the existing post-auth connection-slot limiter — e.g., reuse `IpRateLimiter`/`PerIpRateLimit` (already present in `crates/infra/telemetry/src/rate_limit.rs`) or a dedicated failed-auth bucket keyed by client IP with exponential backoff/lockout, and use constant-time comparison for the key lookup to avoid timing side channels.

### Proof of Concept
1. Start `websocket-proxy` with `--api-keys app1:shortkey`.
2. From a single client, issue a tight loop of `GET /ws/<guess>` HTTP upgrade requests iterating over a keyspace (e.g., dictionary or short alphanumeric brute force).
3. Observe that every failed guess returns `401` instantly with no `Retry-After`, no IP lockout, and no increase in per-IP counters that block further attempts (`unauthorized_requests` metric increments but does not gate requests) — confirmed by reading `authenticated_websocket_handler` and the absence of any throttle call prior to the `Metrics::unauthorized_requests().increment(1)` branch.
4. Once the correct key is found, `GET /ws/shortkey` succeeds and is accepted into `websocket_handler`, granting the attacker the `app1` identity's stream access.

### Citations

**File:** crates/infra/websocket-proxy/src/server.rs (L162-184)
```rust
async fn authenticated_websocket_handler(
    State(state): State<ServerState>,
    ws: WebSocketUpgrade,
    ConnectInfo(addr): ConnectInfo<SocketAddr>,
    headers: HeaderMap,
    Path(api_key): Path<String>,
) -> impl IntoResponse {
    let application = state.auth.get_application_for_key(&api_key).cloned();

    application.map_or_else(
        || {
            Metrics::unauthorized_requests().increment(1);

            Response::builder()
                .status(StatusCode::UNAUTHORIZED)
                .body(Body::from(json!({"message": "Invalid API key"}).to_string()))
                .unwrap()
        },
        |app| {
            Metrics::connections_by_app(app).increment(1);
            websocket_handler(state, ws, addr, headers, FilterType::None)
        },
    )
```

**File:** crates/infra/websocket-proxy/src/server.rs (L187-212)
```rust
async fn authenticated_filter_websocket_handler(
    State(state): State<ServerState>,
    ws: WebSocketUpgrade,
    ConnectInfo(addr): ConnectInfo<SocketAddr>,
    headers: HeaderMap,
    Path(api_key): Path<String>,
    query: Query<FilterQuery>,
) -> impl IntoResponse {
    let application = state.auth.get_application_for_key(&api_key).cloned();

    application.map_or_else(
        || {
            Metrics::unauthorized_requests().increment(1);

            Response::builder()
                .status(StatusCode::UNAUTHORIZED)
                .body(Body::from(json!({"message": "Invalid API key"}).to_string()))
                .unwrap()
        },
        |app| {
            Metrics::connections_by_app(app).increment(1);
            let filter = create_filter_from_query(query.0);
            websocket_handler(state, ws, addr, headers, filter)
        },
    )
}
```

**File:** crates/infra/websocket-proxy/src/server.rs (L223-255)
```rust
fn websocket_handler(
    state: ServerState,
    ws: WebSocketUpgrade,
    addr: SocketAddr,
    headers: HeaderMap,
    filter: FilterType,
) -> Response {
    let connect_addr = addr.ip();
    let client_addr = state.trusted_proxy_config.client_ip(connect_addr, &headers);

    let ticket = match state.rate_limiter.try_acquire(client_addr) {
        Ok(ticket) => ticket,
        Err(RateLimitError::Limit { reason, limit_type }) => {
            match limit_type {
                RateLimitType::PerIp => {
                    info!(
                        message = "per-IP rate limit exceeded",
                        client_ip = client_addr.to_string(),
                        reason = reason
                    );
                    Metrics::per_ip_rate_limited_requests().increment(1);
                }
                RateLimitType::Global => {
                    Metrics::global_rate_limited_requests().increment(1);
                }
            }

            return Response::builder()
                .status(StatusCode::TOO_MANY_REQUESTS)
                .body(Body::from(json!({"message": reason}).to_string()))
                .unwrap();
        }
    };
```

**File:** crates/infra/websocket-proxy/src/metrics.rs (L25-26)
```rust
    #[describe("Count of unauthorized requests with invalid API keys")]
    unauthorized_requests: counter,
```

**File:** crates/infra/websocket-proxy/src/auth.rs (L8-12)
```rust
/// Maps API keys to application names for request authentication.
#[derive(Clone, Debug)]
pub struct Authentication {
    key_to_application: HashMap<String, String>,
}
```

**File:** crates/infra/websocket-proxy/src/auth.rs (L102-105)
```rust
    /// Look up the application name associated with the given API key.
    pub fn get_application_for_key(&self, api_key: &str) -> Option<&String> {
        self.key_to_application.get(api_key)
    }
```
