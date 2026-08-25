### Title
No Rate Limiting on JSON RPC HTTP Endpoint Allows Denial-of-Service - (File: `rpc/src/rpc_service.rs`)

### Summary
The Agave JSON RPC HTTP server, configured in `JsonRpcService::new`, builds the `jsonrpc_http_server::ServerBuilder` with CORS, a custom `RpcRequestMiddleware`, and a `max_request_body_size` cap, but applies no per-IP or per-client request-rate limiting to incoming JSON-RPC calls [1](#0-0) . This mirrors the HAL-23 finding on the SSP Relay server: critical request-handling endpoints are exposed without throttling, making them susceptible to volumetric abuse.

### Finding Description
The RPC server thread constructs a `MetaIoHandler` exposing the full JSON-RPC surface (`rpc_minimal`, `rpc_bank`, `rpc_accounts`, `rpc_accounts_scan`, `rpc_full`) and starts an HTTP listener via `ServerBuilder::with_meta_extractor(...).threads(1).cors(...).request_middleware(request_middleware).max_request_body_size(max_request_body_size).start_http(&rpc_addr)` [2](#0-1) . Unlike the QUIC/TPU ingest path, which enforces a global `TokenBucket` connection-rate limiter, a per-IP `ConnectionRateLimiter`, and stake-weighted stream throttling before packets are ever handed to consumers [3](#0-2) [4](#0-3) [5](#0-4) , no equivalent IP-based or identity-based throttle exists on the JSON RPC HTTP entry point. There is no `rate_limit`/`RateLimit`/`governor`-style construct anywhere in `rpc/src/rpc.rs` or `rpc/src/rpc_service.rs` guarding request admission; the only bound is on request body size, not request frequency.

### Impact Explanation
Because compute-intensive read methods (e.g., `getProgramAccounts`, `simulateTransaction`, repeated `sendTransaction` submissions) are dispatched with no admission control, a single client (or a small botnet) can flood a public/full RPC node with concurrent or rapid-fire requests, exhausting the RPC worker thread pool, bank-forks read locks, or the leader-forwarding path shared by `SendTransactionService`. This can degrade or crash the RPC service, denying legitimate wallets, dApps, and other validators/RPC consumers access to the node — the same "endpoint overloading" risk described in the source report, but here affecting a load-bearing, always-online RPC surface rather than an optional offline-capable relay.

### Likelihood Explanation
Likelihood is moderate-to-high for any RPC node that exposes the full API to untrusted clients (a common production configuration): the attack requires no special privileges, no valid stake, and no cryptographic material — just standard HTTP access to the RPC port, which is explicitly designed to be reachable by "ordinary user" RPC calls. This is analogous in reachability to the original SSP Relay bug (unauthenticated, unthrottled endpoints reachable from the network).

### Recommendation
Add request-rate limiting to the JSON-RPC HTTP server, e.g., wrap `RpcRequestMiddleware`/`ServerBuilder` with a per-IP token-bucket limiter analogous to `ConnectionRateLimiter` already used for QUIC/TPU ingest [5](#0-4) , and/or bound concurrent in-flight requests per source IP. Expose CLI flags (mirroring `--tpu-max-connections-per-ipaddr-per-minute`) so operators can tune limits for public RPC endpoints [6](#0-5) .

### Proof of Concept
An attacker issues a sustained burst of JSON-RPC POST requests (e.g., `getProgramAccounts` against a large program, or repeated `sendTransaction`) to a public Agave RPC node's HTTP port. Because `rpc/src/rpc_service.rs` enforces only `max_request_body_size` and no rate/concurrency limiting before dispatching to `MetaIoHandler`, the flood consumes RPC thread/runtime capacity and bank-read resources without being throttled or rejected, degrading service for other clients — directly analogous to the unthrottled SSP Relay endpoints described in HAL-23.

### Citations

**File:** rpc/src/rpc_service.rs (L708-743)
```rust
                let mut io = MetaIoHandler::default();

                io.extend_with(rpc_minimal::MinimalImpl.to_delegate());
                if full_api {
                    io.extend_with(rpc_bank::BankDataImpl.to_delegate());
                    io.extend_with(rpc_accounts::AccountsDataImpl.to_delegate());
                    io.extend_with(rpc_accounts_scan::AccountsScanImpl.to_delegate());
                    io.extend_with(rpc_full::FullImpl.to_delegate());
                }

                let request_middleware = RpcRequestMiddleware::new(
                    ledger_path,
                    snapshot_config,
                    bank_forks,
                    health.clone(),
                );
                let server = ServerBuilder::with_meta_extractor(
                    io,
                    move |req: &hyper::Request<hyper::Body>| {
                        let xbigtable = req.headers().get("x-bigtable");
                        if xbigtable.is_some_and(|v| v == "disabled") {
                            request_processor.clone_without_bigtable()
                        } else {
                            request_processor.clone()
                        }
                    },
                )
                .event_loop_executor(runtime.handle().clone())
                .threads(1)
                .cors(DomainsValidation::AllowOnly(vec![
                    AccessControlAllowOrigin::Any,
                ]))
                .cors_max_age(86400)
                .request_middleware(request_middleware)
                .max_request_body_size(max_request_body_size)
                .start_http(&rpc_addr);
```

**File:** streamer/src/nonblocking/quic.rs (L270-281)
```rust
    let rate_limiter = Arc::new(ConnectionRateLimiter::new(
        quic_server_params.max_connections_per_ipaddr_per_min,
        // allow for 10x burst to make sure we can accommodate legitimate
        // bursts from container environments running multiple pods on same IP
        quic_server_params.max_connections_per_ipaddr_per_min * 10,
        num_shards,
    ));
    let overall_connection_rate_limiter = Arc::new(TokenBucket::new(
        MAX_CONNECTION_BURST,
        MAX_CONNECTION_BURST,
        TOTAL_CONNECTIONS_PER_SECOND,
    ));
```

**File:** streamer/src/nonblocking/quic.rs (L346-369)
```rust
            // check overall connection request rate limiter
            if overall_connection_rate_limiter.current_tokens() == 0 {
                stats
                    .connection_rate_limited_across_all
                    .fetch_add(1, Ordering::Relaxed);
                debug!(
                    "Ignoring incoming connection from {} due to overall rate limit.",
                    incoming.remote_address()
                );
                incoming.ignore();
                continue;
            }
            // then perform per IpAddr rate limiting
            if !rate_limiter.is_allowed(&incoming.remote_address().ip()) {
                stats
                    .connection_rate_limited_per_ipaddr
                    .fetch_add(1, Ordering::Relaxed);
                debug!(
                    "Ignoring incoming connection from {} due to per-IP rate limiting.",
                    incoming.remote_address()
                );
                incoming.ignore();
                continue;
            }
```

**File:** streamer/src/nonblocking/connection_rate_limiter.rs (L16-50)
```rust
impl ConnectionRateLimiter {
    /// Create a new rate limiter per IpAddr. The rate is specified as the count per minute to allow for
    /// less frequent connections. Higher limit also allows higher bursts.
    /// num_shards controls how many shards are used in the underlying dashmap,
    /// should be set >= number of contending threads.
    pub fn new(limit_per_minute: u64, max_burst: u64, num_shards: usize) -> Self {
        Self {
            limiter: KeyedRateLimiter::new(
                CONNECTION_RATE_LIMITER_CLEANUP_SIZE_THRESHOLD,
                TokenBucket::new(limit_per_minute, max_burst, limit_per_minute as f64 / 60.0),
                num_shards,
            ),
        }
    }

    /// Check if the connection from the said `ip` is allowed.
    /// Here we assume that only IPs with actual confirmed connections are stored in it,
    /// since we should only modify server state once source IP is verified
    pub fn is_allowed(&self, ip: &IpAddr) -> bool {
        // Check if we have records in the rate limiter for the given IP address
        match self.limiter.current_tokens(ip) {
            Some(r) => r > 0, // we have a record, and rate is not exceeded
            None => true,     // if we have not seen IP, allow connection request
        }
    }

    pub fn register_connection(&self, ip: &IpAddr) -> bool {
        if self.limiter.consume_tokens(*ip, 1).is_ok() {
            debug!("Request from IP {ip:?} allowed");
            true // Request allowed
        } else {
            debug!("Request from IP {ip:?} blocked");
            false // Request blocked
        }
    }
```

**File:** validator/src/commands/run/args.rs (L697-705)
```rust
    .arg(
        Arg::with_name("tpu_max_connections_per_ipaddr_per_minute")
            .long("tpu-max-connections-per-ipaddr-per-minute")
            .takes_value(true)
            .default_value(&default_args.tpu_max_connections_per_ipaddr_per_minute)
            .validator(is_parsable::<u32>)
            .hidden(hidden_unless_forced())
            .help("Controls the rate of the clients connections per IpAddr per minute."),
    )
```
