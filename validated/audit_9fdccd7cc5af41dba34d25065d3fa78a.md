### Title
Missing brute-force protection on WebSocket API-key authentication in `websocket-proxy` - (File: `crates/infra/websocket-proxy/src/server.rs`)

### Summary
The `websocket-proxy` component gates access to the Flashblocks pending-state feed behind a static API key supplied in the URL path (`/ws/{api_key}`). Unlike the Nextcloud password-confirmation flaw (CVE-2023-25820), where the confirmation endpoint could be brute-forced without limit, the `authenticated_websocket_handler`/`authenticated_filter_websocket_handler` functions check the API key **before** any rate limiting is applied, so failed authentication attempts never consume a rate-limit token and are never throttled.

### Finding Description
`authenticated_websocket_handler` looks up the supplied API key in a `HashMap` and only calls `websocket_handler` — which is the function that invokes `state.rate_limiter.try_acquire(client_addr)` — on a **successful** match: [1](#0-0) 

The per-IP/global connection limiter lives inside `websocket_handler`, invoked only after the key already validated: [2](#0-1) 

`Authentication::get_application_for_key` is a plain `HashMap::get`, with no attempt counter, lockout, or delay tied to failed lookups: [3](#0-2) 

Because the `401 Unauthorized` branch returns immediately after only bumping a metric counter, an anonymous client can send unlimited HTTP upgrade requests with different `api_key` path values with no per-attempt throttling, cooldown, or connection cost — exactly the same missing-brute-force-protection root cause as CVE-2023-25820 (checking a secret without any rate limiting on the check itself).

### Impact Explanation
The API key is the sole access control gating the Flashblocks feed, which streams pending block/state data intended only for authorized downstream RPC nodes (per the service's own README: "restricting access" to Flashblocks from rollup-boost). An attacker who brute-forces a valid key gains unauthorized real-time access to sequencer pending-state data, which is commercially/MEV-sensitive and normally restricted to paying/whitelisted consumers. This is an unauthorized-access vulnerability against a gated data feed, reachable by any anonymous WebSocket client with no signed transaction or on-chain action required.

### Likelihood Explanation
Exploitation only requires sending HTTP WebSocket-upgrade requests with different path parameters; there is no cost, delay, or lockout, so brute-forcing shorter/weaker operator-chosen API keys is fully automatable. The existing per-IP `InMemoryRateLimit` (`crates/infra/websocket-proxy/src/rate_limit.rs`) is architecturally present but is bypassed entirely for the authentication check itself because it's only reached after a key already matches.

### Recommendation
Move the rate-limit/connection-ticket acquisition (or a dedicated failed-auth counter) to before or alongside the API-key lookup in `authenticated_websocket_handler` and `authenticated_filter_websocket_handler`, so failed attempts also consume the same per-IP quota (or a stricter one), and consider constant-time key comparison plus exponential backoff/lockout on repeated failures from a single IP.

### Proof of Concept
1. Deploy `websocket-proxy` with `--api-keys app1:<secret>` (authenticated mode, no `--public-access-enabled`).
2. From an unauthenticated client, issue repeated `GET /ws/<guess>` upgrade requests with different `<guess>` values in a tight loop.
3. Observe that each request is answered immediately with `401` (or succeeds) without ever hitting `TOO_MANY_REQUESTS`, because `websocket_handler`'s rate limiter (`crates/infra/websocket-proxy/src/server.rs:233`) is never invoked for failed lookups — confirming unlimited, unthrottled brute-force capability against the API key.

### Citations

**File:** crates/infra/websocket-proxy/src/server.rs (L162-185)
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

**File:** crates/infra/websocket-proxy/src/auth.rs (L102-106)
```rust
    /// Look up the application name associated with the given API key.
    pub fn get_application_for_key(&self, api_key: &str) -> Option<&String> {
        self.key_to_application.get(api_key)
    }
}
```
