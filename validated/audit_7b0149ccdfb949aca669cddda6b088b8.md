## Finding

The ingress RPC service — the public JSON-RPC endpoint that unprivileged, anonymous callers use to submit `eth_sendRawTransaction` bundles into the Base block builder pipeline — builds its `jsonrpsee` HTTP server with no hardening at all, unlike the equivalent code path in the consensus RPC actor. [1](#0-0) 

Compare this to `launch_rpc_server` in the consensus service, which explicitly documents the need to bound an unauthenticated RPC surface and applies a request timeout, a concurrency limit, load shedding, and a `max_connections` cap before binding: [2](#0-1) 

The ingress-rpc binary skips every one of these protections — no `TimeoutLayer`, no `ConcurrencyLimitLayer`, no `LoadShedLayer`, and no explicit `ServerConfig::max_connections` — despite `IngressService::send_raw_transaction` being reachable by any anonymous transaction sender on the internet: [3](#0-2) [4](#0-3) 

### Title
Unbounded/undefended ingress JSON-RPC HTTP server permits slow-client connection exhaustion, halting transaction ingestion - ([File: bin/ingress-rpc/src/main.rs])

### Summary
`bin/ingress-rpc/src/main.rs` builds its `jsonrpsee::server::Server` for the public `eth_sendRawTransaction` ingress endpoint with a bare `Server::builder().build(&bind_addr)` call — no `ServerConfig` tuning, no `TimeoutLayer`, `ConcurrencyLimitLayer`, or `LoadShedLayer` like the hardened consensus RPC does. A client that opens a connection and sends request bytes arbitrarily slowly (one byte per second, as in CVE-2025-58436) can occupy a server connection/worker slot indefinitely, and repeating this with a handful of slow connections exhausts the server's available connection capacity, denying `eth_sendRawTransaction` service to all other, legitimate transaction senders.

### Finding Description
`bin/ingress-rpc/src/main.rs` starts the RPC server used by `IngressService` — the entry point through which anonymous transaction senders submit raw transactions into the builder metering pipeline — via `Server::builder().build(&bind_addr).await?` with no explicit `ServerConfig`. [1](#0-0)  This differs from the internal consensus control-plane RPC, whose author left an explicit "SECURITY" comment stating the RPC is unauthenticated and must be bounded, and which accordingly stacks `TimeoutLayer`, `tower::limit::ConcurrencyLimitLayer`, `tower::load_shed::LoadShedLayer`, and an explicit `ServerConfig::builder().max_connections(...)` before starting the server. [2](#0-1)  The ingress-rpc endpoint is strictly more exposed (it is meant to receive raw transactions from arbitrary, unauthenticated internet clients) yet has none of that protection.

Because the underlying transport (jsonrpsee's hyper-based HTTP server) has no per-connection body-read deadline configured here, a client that completes the TCP/HTTP handshake and then trickles the JSON-RPC POST body in slowly (à la CUPS's "one byte per second" attack) keeps a server connection slot open for as long as it wishes. Since no `ConcurrencyLimitLayer`/`max_connections` cap is set to bound and gracefully shed excess load, a modest number of such slow connections is sufficient to occupy all of the process's available accept/worker capacity, after which the service can no longer accept or service new `eth_sendRawTransaction` calls from other users.

### Impact Explanation
This is the transaction ingestion front door for the Base builder pipeline; stalling it prevents ordinary users and dApps from submitting transactions through this ingress path, a node/service halt (DoS) directly caused by a single anonymous, unprivileged client — matching the CVE-2025-58436 bug class ("slow client can halt cupsd, leading to a possible DoS attack").

### Likelihood Explanation
The attack requires only opening one or more plain TCP connections to the ingress-rpc listener and sending data slower than any timeout enforced by the stack — since no `TimeoutLayer`/`ConcurrencyLimitLayer`/`max_connections` is configured for this specific binary, no authentication or special network position is needed, making this trivially reachable by any external caller who can reach the ingress-rpc port.

### Recommendation
Apply the same hardening already used for the consensus RPC actor to the ingress-rpc server: configure a bounded `ServerConfig` (`max_connections`), and wrap the HTTP service with `TimeoutLayer`, `ConcurrencyLimitLayer`, and `LoadShedLayer` (or equivalent) in `bin/ingress-rpc/src/main.rs` before calling `Server::builder()...build(&bind_addr)`, mirroring `launch_rpc_server` in `crates/consensus/service/src/actors/rpc/actor.rs`.

### Proof of Concept
1. Deploy/run the `ingress-rpc` binary as configured in `bin/ingress-rpc/src/main.rs`.
2. From an external client, open N TCP connections to the RPC bind address and, for each, send an HTTP POST request line/headers/body one byte at a time with long delays (e.g., using a raw socket and `time.sleep(1)` between byte writes), never completing the JSON-RPC body.
3. Observe that legitimate `eth_sendRawTransaction` JSON-RPC calls from other clients begin to fail/timeout once the slow connections have consumed the server's available connection/worker capacity, since no timeout or concurrency-limiting middleware exists to evict or reject them.

### Citations

**File:** bin/ingress-rpc/src/main.rs (L111-117)
```rust
    let bind_addr = format!("{}:{}", config.address, config.port);
    let service = IngressService::new(simulation_provider, audit_tx, builder_tx, cli.config);

    let server = Server::builder().build(&bind_addr).await?;
    let addr = server.local_addr()?;
    let handle = server.start(service.into_rpc());

```

**File:** crates/consensus/service/src/actors/rpc/actor.rs (L76-101)
```rust
    // SECURITY: This unauthenticated control-plane RPC is internal.
    // Deployments must restrict it to trusted operators on a private network.
    let middleware = tower::ServiceBuilder::new()
        .layer(TimeoutLayer::with_status_code(StatusCode::REQUEST_TIMEOUT, config.http_timeout))
        .layer(tower::limit::ConcurrencyLimitLayer::new(config.max_concurrent_requests.get()))
        .layer(tower::load_shed::LoadShedLayer::new())
        .layer(EthHealthCheckLayer)
        .layer(
            ProxyGetRequestLayer::new([("/healthz", "healthz")])
                .expect("Critical: Failed to build GET method proxy"),
        );
    // The tower HTTP middleware above (concurrency limit, timeout, load shed) only bounds HTTP
    // requests — it does not see individual JSON-RPC calls streamed over a WebSocket connection, and
    // jsonrpsee serves WS by default even when the WS engine module is not merged. So a WS client
    // could otherwise stream unauthenticated `base_*` calls past those limits. Disable the WS
    // transport entirely unless it is explicitly enabled, and cap total concurrent connections
    // (transport-independent) as a backstop.
    let mut server_config = ServerConfig::builder()
        .max_connections(u32::try_from(config.max_concurrent_requests.get()).unwrap_or(u32::MAX));
    if !config.ws_enabled() {
        server_config = server_config.http_only();
    }
    let server = Server::builder()
        .set_config(server_config.build())
        .set_http_middleware(middleware)
        .build(config.socket)
```

**File:** crates/infra/ingress-rpc/src/service.rs (L31-36)
```rust
#[rpc(server, namespace = "eth")]
pub trait IngressApi {
    /// Handler for: `eth_sendRawTransaction`
    #[method(name = "sendRawTransaction")]
    async fn send_raw_transaction(&self, tx: Bytes) -> RpcResult<B256>;
}
```

**File:** crates/infra/ingress-rpc/src/service.rs (L82-87)
```rust
#[async_trait]
impl IngressApiServer for IngressService {
    async fn send_raw_transaction(&self, data: Bytes) -> RpcResult<B256> {
        let start = Instant::now();
        let transaction = self.get_tx(&data).await?;
        let tx_hash = transaction.tx_hash();
```
