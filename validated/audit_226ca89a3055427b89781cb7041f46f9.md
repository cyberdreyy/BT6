## Finding

### Title
Unauthenticated `base` namespace RPC allows any client to disable/poison the block builder's resource-metering throttle - (File: `crates/builder/metering/src/ext.rs`)

### Summary
The builder's metering RPC extension registers three state-mutating JSON-RPC methods (`base_setMeteringInformation`, `base_setMeteringEnabled`, `base_clearMeteringInformation`) under the public, non-admin `base` namespace, with no authorization gate at all, while every other mutating operational RPC in this codebase (batcher admin, proposer admin, txpool admin, upgrade-signal refresh, miner/debug extensions) is explicitly gated behind the privileged `admin` namespace / `RethRpcModule::Admin` feature flag.

### Finding Description
`MeteringStoreExt` implements `BaseApiExtServer`, exposing `setMeteringInformation`, `setMeteringEnabled`, and `clearMeteringInformation` under `namespace = "base"`: [1](#0-0) 

These handlers directly mutate the shared `SharedMeteringProvider` used by the block-building loop, with no caller check: [2](#0-1) 

The extension is wired into the node without any admin-module gate — `add_or_replace_configured` (unconditional), unlike the `merge_if_module_configured(RethRpcModule::Admin, ...)` pattern used elsewhere: [3](#0-2) 

Contrast this with the codebase's own convention for privileged operations, e.g. the equivalent txpool admin API is explicitly gated on the `Admin` reth module: [4](#0-3) 

and the documented intent of the public `base` namespace is that it is strictly "read-only, non-admin" and safe to expose externally: [5](#0-4) 

`MeteringStoreExt` breaks that invariant by putting write operations in the same namespace that is documented and relied upon (elsewhere in the stack) as being safe for unauthenticated/external exposure — this is structurally the same authorization flaw as the Jenkins CVE: a caller who should only have "read" access to an interface can reach a state-changing, privileged operation because the interface's own authorization boundary (namespace/module gate) was not applied to the mutating handlers.

This provider directly drives builder admission decisions: `metering_provider.is_enabled()` gates whether the builder waits for/enforces metering data before including a transaction, and `check_simulated_usage`/`is_active()` gate whether resource-throttling exclusions apply at all: [6](#0-5) [7](#0-6) 

### Impact Explanation
Any RPC client that can reach the builder's public `base` namespace (an unprivileged/anonymous RPC client, matching the allowed threat model) can call `base_setMeteringEnabled(false)` or `base_clearMeteringInformation()` to disable or wipe the resource-metering data the builder uses to throttle expensive transactions, or call `base_setMeteringInformation` to inject forged metering data for an arbitrary `tx_hash`, biasing the builder's inclusion/exclusion and DA/gas-limit accounting for that transaction. Because metering underlies the builder's per-transaction/per-block resource-throttling decisions, this can be used to bypass resource limits meant to bound block-building CPU/DA cost, a node-halt/degradation-class impact if metering is required to keep block-building resource use bounded (denial-of-service / builder resource exhaustion), and it is a clear violation of least-privilege since the RPC surface is documented as non-admin/read-only.

### Likelihood Explanation
High for any deployment that exposes this RPC extension on a public-facing or otherwise reachable HTTP/WS endpoint without an additional reverse-proxy ACL: the vulnerable methods require no signature, no admin flag, and no special caller state — a single unauthenticated JSON-RPC call is sufficient. The severity is bounded by whatever operational deployments choose to firewall the RPC port, which is exactly the same "an attacker with only baseline/read access can escalate" pattern the Jenkins advisory describes.

### Recommendation
Gate `MeteringStoreExt`'s mutating methods behind the same privileged-module mechanism used for every other mutating RPC in this codebase (e.g. `merge_if_module_configured(RethRpcModule::Admin, ...)`, or a dedicated `admin`-namespace registration) rather than the public `base` namespace, or split the trait into a read-only `base` portion and an admin-gated mutation portion, consistent with the documented "read-only, non-admin" contract of the `base` namespace.

### Proof of Concept
1. Start a builder node with the metering extension enabled (`MeteringStoreExtension`) and its RPC HTTP/WS server reachable.
2. From an unauthenticated client, send: `{"jsonrpc":"2.0","method":"base_setMeteringEnabled","params":[false],"id":1}`.
3. Observe `MeteringStoreExt::set_metering_enabled` sets `store.set_enabled(false)` with no auth check [8](#0-7) , silently disabling the builder's resource-metering wait/throttle logic consumed in `flashblocks/context.rs` [6](#0-5) .
4. Alternatively call `base_clearMeteringInformation` or `base_setMeteringInformation` with a crafted `MeterBundleResponse` for a target `tx_hash` to erase or forge the resource data used for that transaction's admission decision.

**Uncertainty note:** I could not verify from the indexed code whether operational deployments front this RPC with an additional network-level ACL/reverse proxy that would mitigate exposure, nor could I fully confirm every code path that consumes `is_enabled()`/`clear()` to rule out a lower severity than "resource-only." This assessment is based on the code available in the index; a full review (e.g., via a Devin session) of deployment configs (`bin/builder/src/main.rs`, k8s/chart configs) would be needed to confirm real-world reachability.

### Citations

**File:** crates/builder/metering/src/ext.rs (L12-31)
```rust
/// RPC trait for metering-related operations.
#[cfg_attr(not(test), rpc(server, namespace = "base"))]
#[cfg_attr(test, rpc(server, client, namespace = "base"))]
pub trait BaseApiExt {
    /// Sets metering information for a transaction.
    #[method(name = "setMeteringInformation")]
    async fn set_metering_information(
        &self,
        tx_hash: TxHash,
        meter: MeterBundleResponse,
    ) -> RpcResult<()>;

    /// Enables or disables resource metering.
    #[method(name = "setMeteringEnabled")]
    async fn set_metering_enabled(&self, enabled: bool) -> RpcResult<()>;

    /// Clears all stored metering information.
    #[method(name = "clearMeteringInformation")]
    async fn clear_metering_information(&self) -> RpcResult<()>;
}
```

**File:** crates/builder/metering/src/ext.rs (L46-69)
```rust
#[async_trait]
impl BaseApiExtServer for MeteringStoreExt {
    async fn set_metering_information(
        &self,
        tx_hash: TxHash,
        metering: MeterBundleResponse,
    ) -> RpcResult<()> {
        self.store.insert(tx_hash, metering);
        Ok(())
    }

    async fn set_metering_enabled(&self, enabled: bool) -> RpcResult<()> {
        self.store.set_enabled(enabled);
        Ok(())
    }

    async fn clear_metering_information(&self) -> RpcResult<()> {
        info!(
            rpc_method = "base_clearMeteringInformation",
            "Clearing builder metering information"
        );
        self.store.clear();
        Ok(())
    }
```

**File:** crates/builder/metering/src/extension.rs (L14-23)
```rust
impl BaseNodeExtension for MeteringStoreExtension {
    fn apply(self: Box<Self>, hooks: NodeHooks) -> NodeHooks {
        let metering_provider = self.metering_provider;
        hooks.add_rpc_module(move |ctx: &mut BaseRpcContext<'_>| {
            let ext = MeteringStoreExt::new(metering_provider);
            ctx.modules.add_or_replace_configured(ext.into_rpc())?;
            Ok(())
        })
    }
}
```

**File:** crates/execution/txpool-rpc/src/extension.rs (L28-45)
```rust
impl BaseNodeExtension for TxPoolRpcExtension {
    fn apply(self: Box<Self>, builder: NodeHooks) -> NodeHooks {
        let sequencer_rpc = self.config.sequencer_rpc;

        builder.add_rpc_module(move |ctx: &mut BaseRpcContext<'_>| {
            // Register Base transaction pool APIs.
            let status_api = TransactionStatusApiImpl::new(sequencer_rpc, ctx.pool().clone())
                .expect("Failed to create transaction status API");
            ctx.modules.merge_configured(TransactionStatusApiServer::into_rpc(status_api))?;

            // Register AdminTxPoolApi
            let admin_txpool_api = AdminTxPoolApiImpl::new(ctx.pool().clone());
            ctx.modules
                .merge_if_module_configured(RethRpcModule::Admin, admin_txpool_api.into_rpc())?;

            Ok(())
        })
    }
```

**File:** crates/consensus/rpc/src/jsonrpsee.rs (L125-129)
```rust
/// Base-specific node RPC interface (the `base` namespace).
///
/// Read-only, non-admin methods intended for node operators — including external operators — so they
/// are exposed on the public RPC without requiring the (privileged) `admin` namespace to be enabled.
#[cfg_attr(not(feature = "client"), rpc(server, namespace = "base"))]
```

**File:** crates/builder/core/src/flashblocks/context.rs (L1111-1123)
```rust
            let resource_usage = self.builder_config.metering_provider.get(&tx_hash);

            // Skip transactions that are too young and don't have metering data yet
            if self.builder_config.metering_provider.is_enabled()
                && resource_usage.is_none()
                && let Some(wait_duration) = self.builder_config.metering_wait_duration
            {
                let now_ms = SystemTime::now()
                    .duration_since(SystemTime::UNIX_EPOCH)
                    .map(|d| d.as_millis())
                    .unwrap_or(0);
                let tx_age_ms = now_ms.saturating_sub(tx_received_at_ms);
                if tx_age_ms < wait_duration.as_millis() {
```

**File:** crates/execution/payload/src/config.rs (L158-170)
```rust
    /// Returns whether metering is enabled with a non-empty schedule.
    pub fn is_active(&self) -> bool {
        self.enabled && !self.schedule.is_empty()
    }

    /// Looks up the simulated sample for `tx_hash`, if the provider has a matching result.
    pub fn simulated_sample(&self, tx_hash: &TxHash) -> Option<ResourceSample> {
        if !self.is_active() {
            return None;
        }
        MeteringProvider::get(self.provider.as_ref(), tx_hash)
            .and_then(|meter| ResourceSample::from_meter(&meter, tx_hash))
    }
```
