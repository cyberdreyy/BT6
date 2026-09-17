### Title
Missing Origin/CSWSH validation on the consensus node's admin JSON-RPC WebSocket transport allows unauthorized sequencer control - (File: `crates/consensus/service/src/actors/rpc/actor.rs`)

### Summary
The consensus-layer RPC server (`launch_rpc_server`) builds its `jsonrpsee` `Server` with a tower middleware stack that provides timeouts, concurrency limiting, load-shedding and a health-check proxy, but never installs any CORS/`Origin` validation layer [1](#0-0) . When `--rpc.ws-enabled` is set, this same unauthenticated RPC module set — which includes the `admin_*` namespace when `--rpc.enable-admin` is set — is served over a raw WebSocket transport with no way to restrict which web origins may connect [2](#0-1) . This is the same bug class as the Headroom CVE: a WebSocket server that forwards privileged upstream calls without validating the client's `Origin` header, enabling Cross-Site WebSocket Hijacking from any page loaded in a browser that can reach the port.

### Finding Description
`launch_rpc_server` in `crates/consensus/service/src/actors/rpc/actor.rs` constructs the JSON-RPC server used by `RpcActor::start`: [3](#0-2) 

The inline comment explicitly acknowledges the server is unauthenticated ("SECURITY: This unauthenticated control-plane RPC is internal") [4](#0-3) , but the mitigation applied is only a tower `TimeoutLayer` / `ConcurrencyLimitLayer` / `LoadShedLayer`/`EthHealthCheckLayer` stack applied to the HTTP transport, plus disabling WS entirely by default via `server_config.http_only()` [5](#0-4) . Critically, there is **no CORS/`Origin` header check anywhere** in this middleware chain, and no configuration surface (`RpcBuilder`/`RpcArgs`) even exposes an "allowed origins" option, unlike execution-layer nodes elsewhere in the repo which explicitly expose `--ws.origins`/`--http.corsdomain` flags (see `etc/scripts/node/execution-entrypoint` and `etc/docker/docker-compose.yml`) [6](#0-5) .

When `--rpc.ws-enabled` is passed, `RpcActor::start` merges the full RPC module set — including `AdminRpc` (gated only by `--rpc.enable-admin`, not by any authentication) — into the same server that now also accepts WebSocket upgrades: [7](#0-6) 

`AdminRpc` exposes highly privileged sequencer-control methods with no authentication check other than the `--rpc.enable-admin` flag gate at startup: [8](#0-7) [9](#0-8) 

The RPC listen address defaults to `0.0.0.0`, i.e. all interfaces, per the CLI defaults: [10](#0-9) 

Because jsonrpsee's WebSocket transport accepts the upgrade regardless of the `Origin` header (no `tower_http::cors` or equivalent layer is set via `set_http_middleware`), any HTML page rendered in a browser that has network reachability to this port (e.g., an operator's browser on a shared LAN/VPN, or via DNS-rebinding against `0.0.0.0`-bound services) can silently open a WebSocket connection from an attacker-controlled origin and issue JSON-RPC calls that are treated as legitimate, exactly as described for Headroom's unauthenticated WS forwarding.

### Impact Explanation
A successful CSWSH against this endpoint lets an attacker-controlled webpage, executed in the browser of anyone with network access to the consensus RPC port, silently invoke:
- `admin_stopSequencer` — halts block production on the target node [11](#0-10) 
- `admin_overrideLeader` — force a conductor leadership change, risking split-brain sequencing across an HA cluster [12](#0-11) 
- `admin_setRecoverMode` / `admin_resetDerivationPipeline` — alter node recovery/derivation state [13](#0-12) 
- `admin_postUnsafePayload` — inject an unsafe execution payload into the node's engine pipeline [14](#0-13) 

These map directly to permitted impact categories: node halt (sequencer stop) and chain split (forced leader override in a conductor cluster), both without requiring any credential — only that the admin+WS RPC be reachable and enabled, which is a supported, documented operational configuration (`basectl sequencer start/stop` documentation confirms these RPCs are the standard operational interface) [15](#0-14) .

### Likelihood Explanation
Exploitation requires `--rpc.ws-enabled` and `--rpc.enable-admin` together on a node whose RPC port is reachable from a browser context (default bind is `0.0.0.0`) [10](#0-9) . This is a realistic operator configuration, since `basectl` is designed to drive these exact admin RPCs for cluster operations, meaning operators' machines/browsers routinely have network paths to these RPC endpoints. No credential or secret is required to invoke the admin methods once a WebSocket connection is hijacked cross-origin — the only "protection" is host-network isolation, which the code comment itself flags as the sole control and which CSWSH bypasses via the victim's own already-permitted network path.

### Recommendation
Add an explicit `Origin` allow-list check (mirroring `--http.corsdomain`/`--ws.origins` used elsewhere in the stack) as a tower/http middleware layer applied before the WebSocket upgrade in `launch_rpc_server`, rejecting or requiring exact-match `Origin` headers for both HTTP and WS transports. At minimum, reject the WS upgrade when `Origin` is present and not in an operator-configured allow-list, and require an explicit CLI flag (e.g. `--rpc.ws-origins`) with a safe default rather than relying purely on network segmentation. Consider also requiring authentication (e.g., a bearer token or JWT) for the `admin` namespace regardless of transport, independent of network placement.

### Proof of Concept
1. Start a `base` consensus node with `--rpc.enable-admin --rpc.ws-enabled` (as `RpcActor::start` merges `AdminRpc` and enables the WS transport per `crates/consensus/service/src/actors/rpc/actor.rs` lines 140-174) on a host reachable from an operator's browser network.
2. Host a malicious webpage at `https://evil.example` containing:
```js
const ws = new WebSocket("ws://<consensus-rpc-host>:<port>");
ws.onopen = () => {
  ws.send(JSON.stringify({
    jsonrpc: "2.0", id: 1, method: "admin_stopSequencer", params: []
  }));
};
ws.onmessage = (e) => console.log(e.data);
```
3. When a victim with network access to the RPC port visits the page in a browser, the WebSocket upgrade succeeds because no `Origin` check exists in `launch_rpc_server`'s middleware stack, and the `admin_stopSequencer` call executes exactly as if invoked by a trusted operator via `basectl`, halting the sequencer.

### Citations

**File:** crates/consensus/service/src/actors/rpc/actor.rs (L72-111)
```rust
pub(crate) async fn launch_rpc_server(
    config: &RpcBuilder,
    module: RpcModule<()>,
) -> Result<ServerHandle, std::io::Error> {
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
        .await?;

    if let Ok(addr) = server.local_addr() {
        info!(target: "rpc", addr = ?addr, "RPC server bound to address");
    } else {
        error!(target: "rpc", "Failed to get local address for RPC server");
    }

    Ok(server.start(module))
}
```

**File:** crates/consensus/service/src/actors/rpc/actor.rs (L140-174)
```rust

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

**File:** etc/scripts/node/execution-entrypoint (L80-89)
```text
  --ws \
  --ws.origins="*" \
  --ws.addr=0.0.0.0 \
  --ws.port="$WS_PORT" \
  --ws.api=web3,debug,eth,net,txpool \
  --http \
  --http.corsdomain="*" \
  --http.addr=0.0.0.0 \
  --http.port="$RPC_PORT" \
  --http.api=web3,debug,eth,net,txpool,miner \
```

**File:** crates/consensus/rpc/src/admin.rs (L113-124)
```rust
    async fn admin_post_unsafe_payload(
        &self,
        payload: BaseExecutionPayloadEnvelope,
    ) -> RpcResult<()> {
        // Note: intentionally no sequencer guard here. Posting an unsafe payload is a P2P/gossip
        // operation that is valid on both sequencer and validator nodes.
        Metrics::rpc_calls("admin_postUnsafePayload").increment(1.0);
        self.network_sender
            .send(NetworkAdminQuery::PostUnsafePayload { payload: Box::new(payload) })
            .await
            .map_err(|_| ErrorObject::from(ErrorCode::InternalError))
    }
```

**File:** crates/consensus/rpc/src/admin.rs (L150-214)
```rust
    async fn admin_start_sequencer(&self, unsafe_head: B256) -> RpcResult<()> {
        // If the sequencer is not enabled (mode runs in validator mode), return an error.
        let Some(ref sequencer_client) = self.sequencer_admin_client else {
            return Err(sequencer_unavailable());
        };

        sequencer_client.start_sequencer(unsafe_head).await.map_err(sequencer_admin_error)
    }

    async fn admin_stop_sequencer(&self) -> RpcResult<B256> {
        // If the sequencer is not enabled (mode runs in validator mode), return an error.
        let Some(ref sequencer_client) = self.sequencer_admin_client else {
            return Err(sequencer_unavailable());
        };

        sequencer_client.stop_sequencer().await.map_err(sequencer_admin_error)
    }

    async fn admin_conductor_enabled(&self) -> RpcResult<bool> {
        // If the sequencer is not enabled (mode runs in validator mode), return an error.
        let Some(ref sequencer_client) = self.sequencer_admin_client else {
            return Err(sequencer_unavailable());
        };

        sequencer_client
            .is_conductor_enabled()
            .await
            .map_err(|_| ErrorObject::from(ErrorCode::InternalError))
    }

    async fn admin_recover_mode(&self) -> RpcResult<bool> {
        // If the sequencer is not enabled (mode runs in validator mode), return an error.
        let Some(ref sequencer_client) = self.sequencer_admin_client else {
            return Err(sequencer_unavailable());
        };

        sequencer_client
            .is_recovery_mode()
            .await
            .map_err(|_| ErrorObject::from(ErrorCode::InternalError))
    }

    async fn admin_set_recover_mode(&self, mode: bool) -> RpcResult<()> {
        // If the sequencer is not enabled (mode runs in validator mode), return an error.
        let Some(ref sequencer_client) = self.sequencer_admin_client else {
            return Err(sequencer_unavailable());
        };

        sequencer_client
            .set_recovery_mode(mode)
            .await
            .map_err(|_| ErrorObject::from(ErrorCode::InternalError))
    }

    async fn admin_override_leader(&self) -> RpcResult<()> {
        // If the sequencer is not enabled (mode runs in validator mode), return an error.
        let Some(ref sequencer_client) = self.sequencer_admin_client else {
            return Err(sequencer_unavailable());
        };

        sequencer_client
            .override_leader()
            .await
            .map_err(|_| ErrorObject::from(ErrorCode::InternalError))
    }
```

**File:** crates/consensus/rpc/src/jsonrpsee.rs (L260-311)
```rust
/// The admin namespace for the consensus node.
#[cfg_attr(not(feature = "client"), rpc(server, namespace = "admin"))]
#[cfg_attr(feature = "client", rpc(server, client, namespace = "admin"))]
#[async_trait]
pub trait AdminApi {
    /// Posts the unsafe payload.
    #[method(name = "postUnsafePayload")]
    async fn admin_post_unsafe_payload(
        &self,
        payload: BaseExecutionPayloadEnvelope,
    ) -> RpcResult<()>;

    /// Clears pending outbound P2P connection attempts.
    #[method(name = "clearPendingP2pConnections")]
    async fn admin_clear_pending_p2p_connections(&self) -> RpcResult<usize>;

    /// Checks if the sequencer is active.
    #[method(name = "sequencerActive")]
    async fn admin_sequencer_active(&self) -> RpcResult<bool>;

    /// Starts the sequencer.
    #[method(name = "startSequencer")]
    async fn admin_start_sequencer(&self, unsafe_head: B256) -> RpcResult<()>;

    /// Stops the sequencer.
    #[method(name = "stopSequencer")]
    async fn admin_stop_sequencer(&self) -> RpcResult<B256>;

    /// Checks if the conductor is enabled.
    #[method(name = "conductorEnabled")]
    async fn admin_conductor_enabled(&self) -> RpcResult<bool>;

    /// Gets the recover mode.
    #[method(name = "adminRecoverMode")]
    async fn admin_recover_mode(&self) -> RpcResult<bool>;

    /// Sets the recover mode.
    #[method(name = "setRecoverMode")]
    async fn admin_set_recover_mode(&self, mode: bool) -> RpcResult<()>;

    /// Overrides the leader in the conductor.
    #[method(name = "overrideLeader")]
    async fn admin_override_leader(&self) -> RpcResult<()>;

    /// Resets the derivation pipeline.
    #[method(name = "resetDerivationPipeline")]
    async fn admin_reset_derivation_pipeline(&self) -> RpcResult<()>;

    /// Refreshes the runtime upgrade signal schedule from L1.
    #[method(name = "refreshUpgradeSignal")]
    async fn admin_refresh_upgrade_signal(&self) -> RpcResult<UpgradeSignalApplySummary>;
}
```

**File:** crates/consensus/cli/src/rpc.rs (L24-39)
```rust
    /// RPC listening address.
    #[arg(long = "rpc.addr", default_value = "0.0.0.0", env = "BASE_NODE_RPC_ADDR")]
    pub listen_addr: IpAddr,
    /// RPC listening port.
    #[arg(long = "port", alias = "rpc.port", default_value = "9545", env = "BASE_NODE_RPC_PORT")]
    pub listen_port: u16,
    /// Enable the admin API.
    #[arg(long = "rpc.enable-admin", env = "BASE_NODE_RPC_ENABLE_ADMIN")]
    pub enable_admin: bool,
    /// File path used to persist state changes made via the admin API so they persist across
    /// restarts. Disabled if not set.
    #[arg(long = "rpc.admin-state", env = "BASE_NODE_RPC_ADMIN_STATE")]
    pub admin_persistence: Option<PathBuf>,
    /// Enables websocket rpc server to track block production
    #[arg(long = "rpc.ws-enabled", default_value = "false", env = "BASE_NODE_RPC_WS_ENABLED")]
    pub ws_enabled: bool,
```

**File:** bin/basectl/README.md (L276-297)
```markdown
### `basectl sequencer`

Sequencer inspection and control commands for the nodes in an HA conductor
cluster.

- `basectl sequencer status [NODE]` shows sequencer activity, health, pause
  state, L1/L2 heads, and peer counts for every node, or for one selected node
  when `NODE` is provided.
- `basectl sequencer start <NODE> [UNSAFE_HEAD]` starts sequencing on one node
  through the consensus node's `admin_startSequencer` RPC.
- `basectl sequencer stop <NODE>` stops sequencing on one node through the
  consensus node's `admin_stopSequencer` RPC.

Like `basectl conductor`, sequencer commands use the selected config's
hardcoded `conductors` list when present and otherwise discover the live raft
membership from the global `--conductor-rpc` bootstrap URL or
`discovery.bootstrap_rpc` in the config.

When `start` omits `UNSAFE_HEAD`, basectl uses the node's currently observed
unsafe L2 hash. This matches the existing TUI behavior and the sequencer RPC's
safety contract: the requested hash must match the node's current engine unsafe
head.
```
