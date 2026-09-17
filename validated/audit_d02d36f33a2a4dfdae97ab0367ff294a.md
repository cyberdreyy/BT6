### Title
Per-IP WebSocket connection rate limit is bypassable via IPv6 address rotation, enabling global connection-pool exhaustion - ([File: crates/infra/websocket-proxy/src/rate_limit.rs])

### Summary
The `base-websocket-proxy` service, which serves the public flashblocks pending-state WebSocket feed, enforces per-client connection limits by keying an in-memory `HashMap<IpAddr, usize>` on the *exact* client IP address returned by the trusted-proxy resolver. Unlike the sibling `base-telemetry` crate in the same repository — which explicitly truncates IPv6 addresses to a `/64` bucket before rate-limiting specifically to prevent this class of bypass — the WebSocket proxy's `InMemoryRateLimit` does no such normalization. This is the same bug class as CVE-2026-15144 (`@fastify/rate-limit` keying buckets by verbatim client IP): an IPv6-capable client controlling a `/64` or larger delegation can rotate the low bits of its address on every connection to obtain unlimited "per-IP" connection slots, exhausting the shared global connection semaphore and denying the public flashblocks WebSocket service to legitimate anonymous RPC clients.

### Finding Description
`InMemoryRateLimit::try_acquire` tracks `active_connections: HashMap<IpAddr, usize>` and compares the count for the *exact* IP passed in against `per_ip_limit`: [1](#0-0) 

The IP passed to `try_acquire` is produced by `websocket_handler`, which resolves the client IP through `TrustedProxyConfig::client_ip` and passes it straight through with no subnet aggregation: [2](#0-1) 

`TrustedProxyConfig::client_ip`/`try_client_ip` only canonicalizes IPv4-mapped IPv6 addresses (`to_canonical()`); it performs no IPv6 prefix masking: [3](#0-2) 

This is the exact bug class described in the external report: a single IPv6-capable client (whether connecting directly, or via `X-Forwarded-For` from a trusted proxy that forwards the true client IPv6 address) holds a `/64` or larger delegation (2^64 addresses) and can present a different literal address on every WebSocket connection attempt, each of which hashes to a distinct `HashMap` key and therefore gets its own fresh `per_ip_limit` connection allowance.

The repository's own `base-telemetry` crate demonstrates the code owners are aware of and have fixed this exact issue elsewhere: its `IpRateLimiter::bucket_key` explicitly masks IPv6 addresses to their `/64` prefix "since clients routinely hold an entire /64 delegation, so keying on exact addresses would let them defeat the quota by rotating the interface identifier on every request": [4](#0-3) 

The `websocket-proxy` crate's `InMemoryRateLimit`, used to protect the public flashblocks pending-state feed, has no equivalent masking, leaving it exposed to the same bypass the telemetry crate was hardened against.

### Impact Explanation
The WebSocket proxy enforces both a per-IP limit and a shared global connection semaphore (`Semaphore::new(instance_limit)`): [5](#0-4) 

Because the per-IP check can be trivially defeated by rotating the low 64 bits of an IPv6 address, a single unprivileged anonymous client can open connections up to the global `instance_limit` by itself, permanently starving the shared semaphore. Any other client — including legitimate consumers of the flashblocks pending-state feed — is then unable to establish a WebSocket connection, i.e. "an RPC the node can no longer serve" for all other users of that public endpoint, until the attacker-held connections are dropped. This is a concrete denial-of-service against the flashblocks pending-state WebSocket path reachable by an anonymous, unauthenticated RPC client via `unauthenticated_websocket_handler` / `/ws`.

### Likelihood Explanation
Likelihood is high: exploitation requires only ordinary IPv6 connectivity (a single home or cloud IPv6 allocation typically includes a full `/64` or larger), no authentication (the unauthenticated `/ws` public endpoint is enabled per `public_access_enabled`), and no special privileges — matching the CVSS 3.1 AV:N/AC:L/PR:N/UI:N vector of the referenced advisory. The attack requires only repeatedly opening WebSocket connections from sequentially rotated IPv6 addresses within the attacker's own delegated prefix.

### Recommendation
Mirror the fix already present in `crates/infra/telemetry/src/rate_limit.rs`: before using the resolved client IP as the `HashMap`/rate-limit key in `InMemoryRateLimit` (and anywhere else in `websocket-proxy` that keys per-client state by IP), canonicalize IPv4-mapped IPv6 addresses and truncate IPv6 addresses to a configurable subnet prefix (default `/64`) so that an entire delegated block shares one bucket, consistent with the `IpRateLimiter::bucket_key` approach.

### Proof of Concept
1. Deploy `base-websocket-proxy` with public access enabled (`public_access_enabled = true`, no `authentication`), exposing `GET /ws`.
2. From a host with an IPv6 `/64` delegation (e.g. `2001:db8:1:2::/64`), open `per_ip_limit` WebSocket connections to `/ws` using address `2001:db8:1:2::1`; further connections from this address are rejected with `RateLimitType::PerIp`.
3. Reconnect using a different address in the same `/64`, e.g. `2001:db8:1:2::2`, `2001:db8:1:2::3`, etc. Each is treated as an independent key in `active_connections`, so `per_ip_limit` new connections succeed each time.
4. Repeat until the shared `semaphore` (`instance_limit`) is exhausted; subsequent connection attempts from any other client (including legitimate ones) fail with `RateLimitType::Global`, confirming denial of service of the public flashblocks WebSocket feed.

### Citations

**File:** crates/infra/websocket-proxy/src/rate_limit.rs (L78-118)
```rust
impl InMemoryRateLimit {
    /// Create a new in-memory rate limiter with the given limits.
    pub fn new(instance_limit: usize, per_ip_limit: usize) -> Self {
        Self {
            per_ip_limit,
            inner: Mutex::new(Inner {
                active_connections: HashMap::new(),
                semaphore: Arc::new(Semaphore::new(instance_limit)),
            }),
        }
    }
}

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

**File:** crates/infra/websocket-proxy/src/server.rs (L223-235)
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
```

**File:** crates/utilities/trusted-proxy/src/trusted_proxy.rs (L118-132)
```rust
    /// Resolves the client IP, trusting forwarding headers only from configured proxy CIDRs.
    ///
    /// Unlike [`Self::try_client_ip`], this is lenient: a missing or
    /// invalid header from a trusted proxy falls back to the peer address
    /// with a warning instead of failing.
    pub fn client_ip(&self, connect_addr: IpAddr, headers: &HeaderMap) -> IpAddr {
        let connect_addr = connect_addr.to_canonical();
        match self.try_client_ip(connect_addr, headers) {
            Ok(client_ip) => client_ip,
            Err(error) => {
                warn!(error = %error, peer = %connect_addr, "could not resolve forwarded client IP");
                connect_addr
            }
        }
    }
```

**File:** crates/infra/telemetry/src/rate_limit.rs (L87-101)
```rust
    /// Returns the rate-limit bucket key for `ip`.
    ///
    /// `IPv4` addresses key individually, while `IPv6` addresses key by
    /// their /64 prefix: clients routinely hold an entire /64 delegation, so
    /// keying on exact addresses would let them defeat the quota by rotating
    /// the interface identifier on every request.
    pub const fn bucket_key(ip: IpAddr) -> IpAddr {
        match ip.to_canonical() {
            ip @ IpAddr::V4(_) => ip,
            IpAddr::V6(v6) => {
                let seg = v6.segments();
                IpAddr::V6(Ipv6Addr::new(seg[0], seg[1], seg[2], seg[3], 0, 0, 0, 0))
            }
        }
    }
```
