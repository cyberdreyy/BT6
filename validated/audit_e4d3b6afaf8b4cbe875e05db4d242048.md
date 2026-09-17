### Title
Unbounded per-connection WebSocket request flood in the base-consensus RPC server bypasses HTTP concurrency/load-shed protections - (File: crates/consensus/service/src/actors/rpc/actor.rs)

### Summary
The `base-consensus` node's RPC server explicitly documents and accepts that its HTTP-layer DoS protections (`ConcurrencyLimitLayer`, `LoadShedLayer`, `TimeoutLayer`) do not apply to WebSocket transport, and relies only on a global `max_connections` cap as backstop, with no per-connection request-rate or in-flight-request limiting once a WS connection is accepted.

### Finding Description
`launch_rpc_server` builds a `tower` middleware stack (timeout, concurrency limit, load shed) and applies it via `set_http_middleware`, which the code's own comment states "only bounds HTTP requests — it does not see individual JSON-RPC calls streamed over a WebSocket connection." [1](#0-0) 

The only WS-specific control is disabling the WS transport entirely when `ws_enabled` is `false`, and setting a transport-agnostic `max_connections` cap sized from `max_concurrent_requests` (default 1024). [2](#0-1)  This means once WS is enabled (`--rpc.ws-enabled`), an already-connected, unauthenticated remote WebSocket client can send an unbounded stream of JSON-RPC frames over a single connection — none of which are subject to the concurrency limit, load-shedding, or timeout applied to HTTP — matching the "unbounded WebSocket frame flood" bug class from the CryptPad advisory (an attacker floods a WS connection with frames faster than the server can process, exhausting resources and degrading service for other users). [3](#0-2) 

The exposed RPC surface reachable over this WS connection includes the `rollup` namespace (`RollupRpc`), the `p2p` namespace, and conditionally the `base` namespace and `dev` engine-introspection subscriptions — all merged into the same `RpcModule` served over the same transport, so a WS flood competes for the same worker resources as legitimate `eth_*`-style rollup queries. [4](#0-3) 

### Impact Explanation
An unauthenticated, unprivileged remote client that establishes a WebSocket connection to a `base-consensus` node (when `--rpc.ws-enabled` is set) can flood that single connection with a high volume of JSON-RPC requests. Because HTTP-layer concurrency limiting and load shedding do not apply to WS frames, this can consume node CPU/queueing capacity, degrading the responsiveness of the RPC server for legitimate operators/consumers of the `rollup`, `p2p`, and `base` namespaces — a resource-exhaustion / node degradation condition analogous to the CryptPad WS frame flood (medium severity, availability-impacting, no confidentiality/integrity impact).

### Likelihood Explanation
Likelihood depends entirely on whether `--rpc.ws-enabled` is turned on in a given deployment; it defaults to `false`. [5](#0-4)  Where it is enabled (the code comment implies this is an expected/supported production configuration, e.g. to "track block production"), no authentication is applied to the WS transport before frame processing, and any network client that can reach the RPC port can exploit this with a trivial script — no special privileges, contract deployment, or transaction submission required.

### Recommendation
Apply per-connection request-rate or in-flight-message limiting to the WS transport specifically (jsonrpsee supports per-connection rate limiting middleware), or apply an equivalent of the existing `ConcurrencyLimitLayer`/`LoadShedLayer` at the JSON-RPC method-dispatch level rather than only the HTTP transport layer, so both HTTP and WS clients are bounded by the same in-flight-request ceiling.

### Proof of Concept
1. Start a `base-consensus` node with `--rpc.ws-enabled=true`.
2. From an unauthenticated remote host, open a WebSocket connection to the RPC port.
3. Send a very high rate of JSON-RPC requests (e.g., `rollup_*` or `p2p_*` calls) over that single connection in a tight loop.
4. Observe that, unlike an equivalent flood via HTTP (which is capped by `ConcurrencyLimitLayer`/`LoadShedLayer` and rejected once the limit is hit), the WS flood is not subject to these limits and can consume server resources, degrading response times/throughput for other RPC consumers, until the global `max_connections` backstop is reached only by connection count, not by request rate.

### Citations

**File:** crates/consensus/service/src/actors/rpc/actor.rs (L76-99)
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
```

**File:** crates/consensus/service/src/actors/rpc/actor.rs (L132-174)
```rust
        let mut modules = RpcModule::new(());

        modules.merge(HealthzApiServer::into_rpc(HealthzRpc {}))?;

        // Build the p2p rpc module.
        if let Some(p2p_network) = p2p_network {
            modules.merge(P2pRpc::new(p2p_network).into_rpc())?;
        }

        // Build the admin rpc module, gated on the `--rpc.enable-admin` flag.
        if self.config.admin_enabled()
            && let Some(network_admin) = network_admin
        {
            modules.merge(
                AdminRpc::new(self.sequencer_admin_rpc_client, network_admin)
                    .with_upgrade_signal_refresher(self.upgrade_signal_refresher)
                    .into_rpc(),
            )?;
        }

        // Create context for communication between actors.
        let rollup_rpc = RollupRpc::new(
            self.engine_rpc_client.clone(),
            l1_watcher_queries,
            Arc::clone(&self.safe_db_reader),
        );
        modules.merge(rollup_rpc.into_rpc())?;

        // Public `base` namespace (read-only, non-admin), enabled when the upgrade signal is
        // configured so operators — including external ones — can query upgrade readiness.
        if let Some(base_rpc) = self.base_rpc {
            modules.merge(base_rpc.into_rpc())?;
        }

        // Add development RPC module for engine state introspection if enabled
        if self.config.dev_enabled() {
            let dev_rpc = DevEngineRpc::new(self.engine_rpc_client.clone());
            modules.merge(dev_rpc.into_rpc())?;
        }

        if self.config.ws_enabled() {
            modules.merge(WsRPC::new(self.engine_rpc_client.clone()).into_rpc())?;
        }
```

**File:** crates/consensus/cli/src/rpc.rs (L37-53)
```rust
    /// Enables websocket rpc server to track block production
    #[arg(long = "rpc.ws-enabled", default_value = "false", env = "BASE_NODE_RPC_WS_ENABLED")]
    pub ws_enabled: bool,
    /// Enables development RPC endpoints for engine state introspection
    #[arg(long = "rpc.dev-enabled", default_value = "false", env = "BASE_NODE_RPC_DEV_ENABLED")]
    pub dev_enabled: bool,
    /// HTTP request timeout in seconds for the RPC server.
    #[arg(long = "rpc.timeout", default_value = "60", env = "BASE_NODE_RPC_TIMEOUT")]
    pub http_timeout_secs: u64,
    /// Maximum number of concurrent in-flight RPC requests.
    #[arg(
        long = "rpc.max-concurrent",
        default_value = "1024",
        env = "BASE_NODE_RPC_MAX_CONCURRENT",
        value_parser = clap::value_parser!(NonZeroUsize),
    )]
    pub max_concurrent_requests: NonZeroUsize,
```
