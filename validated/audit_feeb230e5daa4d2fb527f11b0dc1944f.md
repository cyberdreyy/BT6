### Title
Unauthenticated `public-access-enabled` WebSocket clients can exhaust the shared global connection pool and deny service to authenticated, paying API-key clients - ([File: crates/infra/websocket-proxy/src/server.rs])

### Summary
When `--public-access-enabled` is set together with API keys, the `base-websocket-proxy` binary exposes both an authenticated route (`/ws/{api_key}`) and an unauthenticated route (`/ws`) that share a single global connection-count semaphore (`InMemoryRateLimit`). Because the unauthenticated path performs no authorization before consuming a slot from this shared global pool, an anonymous client can fill the `instance_connection_limit` capacity and block legitimate, authenticated clients from ever connecting — the same bug class as the reported EigenDA issue, where an unauthorized caller is allowed to consume a shared/global resource before (or without) any authorization check, denying the resource to authorized users.

### Finding Description
`bin/websocket-proxy/src/main.rs` builds a single `Arc<dyn RateLimit>` (`InMemoryRateLimit`) instance and passes the same instance into `Server::new`: [1](#0-0) 

`Server::listen` wires this single `rate_limiter` into `ServerState`, which is shared across all registered routes, including both `authenticated_websocket_handler`/`authenticated_filter_websocket_handler` (`/ws/{api_key}...`) and `unauthenticated_websocket_handler` (`/ws`) when `public_access_enabled` is true: [2](#0-1) 

The unauthenticated handler performs no application/API-key check at all and immediately calls the shared `websocket_handler`, which is the only place a connection slot is consumed from the shared `state.rate_limiter`: [3](#0-2) 

The global semaphore capacity (`instance_connection_limit`) is a single, fixed-size pool shared across both authenticated and unauthenticated clients, enforced in `InMemoryRateLimit::try_acquire`: [4](#0-3) 

This mirrors exactly the reported bug class: a caller that never passes (or bypasses) authorization is still permitted to consume the shared/global rate-limiting resource before rejection, and because the global resource is shared with privileged/authorized callers, this denies them service. In the EigenDA report, the account check happens in `checkRateLimitsAndAddRates` *after* the global buckets are already updated. Here, the analog is structural rather than ordering-based: the unauthenticated route was never gated by authorization in the first place, yet shares the exact same global capacity counter (`Semaphore`) that authenticated clients depend on for guaranteed connection availability, per the `per_ip_connection_limit`/`instance_connection_limit` CLI flags: [5](#0-4) [6](#0-5) 

There is no separate reserved capacity, quota, or priority mechanism to ensure authenticated clients are guaranteed slots independent of anonymous traffic on the public endpoint.

### Impact Explanation
An anonymous, unauthenticated actor can open (or attempt to open) `instance_connection_limit` WebSocket connections against the public `/ws` endpoint. Because that count is drawn from the exact same global semaphore used by the authenticated `/ws/{api_key}` endpoints, doing so exhausts the pool and causes subsequent `try_acquire` calls from legitimate, paying/authenticated clients to fail with `429 Too Many Requests`, i.e., the node/proxy "can no longer serve" its intended authenticated RPC/WebSocket clients — a concrete availability/denial-of-service impact against a service explicitly meant to guarantee access to API-key holders.

### Likelihood Explanation
This requires no privileges: any anonymous client that can reach the `/ws` endpoint (only reachable when the operator has explicitly enabled `--public-access-enabled` alongside API keys) can trivially open many WebSocket connections and hold them open, since holding a connection ties up its semaphore permit until dropped. This is straightforward to trigger and only requires the operator-controlled `public_access_enabled=true` configuration, which is an intended, documented feature ("Allow unauthenticated access to endpoints even if api-keys are provided") — meaning the vulnerable configuration is a supported, expected deployment mode, not a misconfiguration.

### Recommendation
1. Use separate rate limiter/semaphore instances (or partitioned reserved capacity) for the authenticated and unauthenticated routes so that anonymous traffic cannot starve the pool available to API-key holders.
2. Alternatively, reserve a sub-quota of `instance_connection_limit` specifically for unauthenticated connections and enforce authenticated clients against a separate, protected capacity budget.
3. Consider applying per-application (per-API-key) quotas in addition to the current per-IP/global limits so that even authenticated clients cannot starve each other, mirroring the original report's recommendation to move authorization ahead of any global resource consumption and add origin-based limiting.

### Proof of Concept
1. Start `websocket-proxy` with `--api-keys app1:key1 --public-access-enabled true --instance-connection-limit 100 --per-ip-connection-limit 1000`.
2. From N different source IPs (to avoid the per-IP cap), open WebSocket connections to `ws://<proxy>/ws` (the unauthenticated, public route) until 100 connections are established (`instance_connection_limit` reached). No API key or authentication is required for this route: [7](#0-6) 
3. Attempt to connect an authenticated client to `ws://<proxy>/ws/key1` with the valid API key.
4. Observe the authenticated connection attempt fails with `429 Too Many Requests` / `RateLimitType::Global`, because `websocket_handler`'s call to `state.rate_limiter.try_acquire` fails on the shared global semaphore: [8](#0-7)

### Citations

**File:** bin/websocket-proxy/src/main.rs (L52-66)
```rust
    #[arg(
        long,
        env,
        default_value = "100",
        help = "Maximum number of concurrently connected clients per instance"
    )]
    instance_connection_limit: usize,

    #[arg(
        long,
        env,
        default_value = "10",
        help = "Maximum number of concurrently connected clients per IP"
    )]
    per_ip_connection_limit: usize,
```

**File:** bin/websocket-proxy/src/main.rs (L127-133)
```rust
    #[arg(
        long,
        env,
        default_value = "false",
        help = "Allow unauthenticated access to endpoints even if api-keys are provided"
    )]
    public_access_enabled: bool,
```

**File:** bin/websocket-proxy/src/main.rs (L307-319)
```rust
    let rate_limiter: Arc<dyn RateLimit> = Arc::new(InMemoryRateLimit::new(
        args.instance_connection_limit,
        args.per_ip_connection_limit,
    ));

    let server = Server::new(
        args.listen_addr,
        registry.clone(),
        rate_limiter,
        authentication,
        TrustedProxyConfig::new(args.ip_addr_http_header, args.trusted_proxy_cidrs),
        args.public_access_enabled,
    );
```

**File:** crates/infra/websocket-proxy/src/server.rs (L88-121)
```rust
    pub async fn listen(&self, cancellation_token: CancellationToken) {
        let mut router: Router<ServerState> = Router::new().route("/healthz", get(healthz_handler));

        if self.authentication.is_some() {
            info!("Authentication is enabled");
            router = router
                .route("/ws/{api_key}", any(authenticated_websocket_handler))
                .route("/ws/{api_key}/filter", any(authenticated_filter_websocket_handler));
        } else {
            info!("Public endpoint is enabled");
            router = router.route("/ws", any(unauthenticated_websocket_handler));
        }

        if self.public_access_enabled && self.authentication.is_some() {
            info!("Public endpoint is enabled");
            router = router.route("/ws", any(unauthenticated_websocket_handler));
        }

        let router = router.with_state(ServerState {
            registry: self.registry.clone(),
            rate_limiter: Arc::clone(&self.rate_limiter),
            auth: self.authentication.clone().unwrap_or_else(Authentication::none),
            trusted_proxy_config: self.trusted_proxy_config.clone(),
        });

        let listener = tokio::net::TcpListener::bind(self.listen_addr).await.unwrap();

        info!(message = "starting server", address = listener.local_addr().unwrap().to_string());

        axum::serve(listener, router.into_make_service_with_connect_info::<SocketAddr>())
            .with_graceful_shutdown(cancellation_token.cancelled_owned())
            .await
            .unwrap()
    }
```

**File:** crates/infra/websocket-proxy/src/server.rs (L214-255)
```rust
async fn unauthenticated_websocket_handler(
    State(state): State<ServerState>,
    ws: WebSocketUpgrade,
    ConnectInfo(addr): ConnectInfo<SocketAddr>,
    headers: HeaderMap,
) -> impl IntoResponse {
    websocket_handler(state, ws, addr, headers, FilterType::None)
}

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

**File:** crates/infra/websocket-proxy/src/rate_limit.rs (L91-118)
```rust
impl RateLimit for InMemoryRateLimit {
    fn try_acquire(self: Arc<Self>, addr: IpAddr) -> Result<Ticket, RateLimitError> {
        let mut inner = self.inner.lock().unwrap_or_else(|e| e.into_inner());

        let permit = Arc::clone(&inner.semaphore).try_acquire_owned().map_err(|_| {
            RateLimitError::Limit {
                reason: "Global limit".to_owned(),
                limit_type: RateLimitType::Global,
            }
        })?;

        let current_count = inner.active_connections.get(&addr).copied().unwrap_or(0);

        if current_count + 1 > self.per_ip_limit {
            debug!(message = "Rate limit exceeded, trying to acquire", client = addr.to_string());
            return Err(RateLimitError::Limit {
                reason: String::from("IP limit exceeded"),
                limit_type: RateLimitType::PerIp,
            });
        }

        let new_count = current_count + 1;

        inner.active_connections.insert(addr, new_count);

        let rate_limiter: Arc<dyn RateLimit> = Arc::clone(&self) as Arc<dyn RateLimit>;
        Ok(Ticket { addr, _permit: permit, rate_limiter })
    }
```
