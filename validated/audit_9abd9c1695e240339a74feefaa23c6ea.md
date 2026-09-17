### Title
Base RPC methods `base_meterBundle`/`base_meterBlockByHash`/`base_meterBlockByNumber` return raw internal provider/database error strings to unauthenticated RPC callers - (File: crates/execution/metering/src/rpc.rs)

### Summary
The `base_*` metering RPC namespace, when enabled, formats internal `reth_provider` errors (e.g. `BlockReader`/`StateProviderFactory`/`HeaderProvider` failures) directly into the JSON-RPC `message`/error string returned to the caller, instead of returning a generic message like the codebase's own pattern elsewhere (e.g. `crates/consensus/rpc/src/admin.rs`, `crates/infra/audit/src/rpc.rs`) that explicitly redacts internal details.

### Finding Description
`MeteringApiImpl` implements `MeteringApiServer` (namespace `base`, methods `meterBundle`, `meterBlockByHash`, `meterBlockByNumber`) as shown in [1](#0-0) . This module is mounted on the node's public RPC surface via `MeteringExtension::apply`, which calls `ctx.modules.merge_configured(metering_api.into_rpc())` unconditionally whenever metering is enabled, with no additional authentication/authorization gate visible in the extension itself: [2](#0-1) .

Multiple handlers propagate the `Display` of the underlying storage/provider error directly into the JSON-RPC error object sent to the client:
- `meter_bundle`'s header/state lookups: `format!("Failed to get canonical block header: {e}")` and `format!("Failed to get state provider: {e}")` [3](#0-2) 
- `meter_block_by_hash` / `meter_block_by_number`: `format!("Failed to get block: {e}")` [4](#0-3) 
- The shared helper `get_l1_block_info`, which also embeds `{e}` from `block_by_hash` into the response: [5](#0-4) 

This is the same bug class as CWE-209 in the referenced Superset advisory: an application surface returns backend-internal error text (here, `reth`'s storage/provider error `Display` output, which can include on-disk database backend details, I/O errors, or internal state-transition diagnostics) to an untrusted caller, rather than a sanitized message. The codebase demonstrates the correct, intended pattern elsewhere — e.g. `sequencer_admin_error` and `upgrade_signal_refresh_failed` in `crates/consensus/rpc/src/admin.rs` explicitly strip error internals and even have a regression test (`sequencer_admin_error_redacts_internal_failure_details`) asserting this [6](#0-5) , and `internal_rpc_error` in `crates/infra/audit/src/rpc.rs` logs the real error server-side but returns only `"internal server error"` to the client [7](#0-6) . The metering RPC module does not follow this pattern for its `InternalError`-coded responses.

### Impact Explanation
An unprivileged, anonymous JSON-RPC client sending a `base_meterBundle`, `base_meterBlockByHash`, or `base_meterBlockByNumber` request that triggers a storage/provider failure receives the raw `Display` text of reth's internal `ProviderError`/database error in the RPC response. This can reveal node-internal implementation details (storage backend error text, internal state) that operators would not intend to expose publicly, and provides a reconnaissance vector for further attacks on the node. This does not directly enable theft of funds, unbacked supply, node halt, or a wrong provable output root, so per the CVSS of the analog advisory (Low confidentiality impact, no integrity/availability impact) this is best characterized as a **Medium** severity information-disclosure issue, consistent with CVE-2024-53948's classification.

### Likelihood Explanation
The affected methods are reachable directly by any client with RPC access, with no special privileges required, as long as the `MeteringExtension` is enabled on the node (`MeteringConfig::enabled()`); triggering the error path only requires an ordinary lookup of a non-existent or transiently-inaccessible block/state (e.g. `meter_block_by_hash` with a hash whose underlying storage read fails), making the likelihood of hitting this path straightforward for any caller that can reach the endpoint when it is exposed.

### Recommendation
Follow the pattern already used in `crates/consensus/rpc/src/admin.rs` and `crates/infra/audit/src/rpc.rs`: log the full error server-side with `error!`/`tracing`, and return a generic `ErrorCode::InternalError` (or a fixed, non-parameterized message) to the RPC caller for all `StateProviderFactory`/`BlockReader`/`HeaderProvider` failures in `crates/execution/metering/src/rpc.rs`, removing `{e}` interpolation from the `InternalError`-coded responses in `meter_bundle`, `meter_block_by_hash`, `meter_block_by_number`, and `get_l1_block_info`.

### Proof of Concept
1. Enable the metering RPC extension on a node (`MeteringConfig::enabled()`), exposing `base_meterBlockByHash`/`base_meterBundle` on the public RPC endpoint, as wired in `crates/execution/metering/src/extension.rs`.
2. As an anonymous RPC client, send `base_meterBlockByHash` with a block hash/parameters chosen to trigger a `provider.block_by_hash` storage error (or use `base_meterBundle` in a state where `sealed_header_by_number_or_tag`/`state_by_block_hash` fails), per the code paths at `crates/execution/metering/src/rpc.rs` lines 171-229 and 68-111.
3. Observe the JSON-RPC error response's `message` field contains the raw `Display` output of the internal reth provider/database error (`format!("Failed to get block: {e}")`) instead of a generic message, confirming the information-disclosure path.

### Citations

**File:** crates/execution/metering/src/traits.rs (L10-45)
```rust
/// RPC API for transaction metering.
///
/// The API exposes bundle simulation and block profiling.
#[rpc(server, namespace = "base")]
pub trait MeteringApi {
    /// Simulates and meters a bundle of transactions against latest canonical state.
    #[method(name = "meterBundle")]
    async fn meter_bundle(&self, bundle: Bundle) -> RpcResult<MeterBundleResponse>;

    /// Handler for: `base_meterBlockByHash`
    ///
    /// Re-executes a block and returns timing metrics for signer recovery and EVM execution.
    ///
    /// This method fetches the block by hash, re-executes all transactions against the parent
    /// block's state, and measures:
    /// - `executionTimeUs`: Time to execute all transactions in the EVM
    /// - `totalTimeUs`: Sum of signer recovery and execution time
    /// - `meteredTransactions`: Per-transaction execution times and gas usage
    #[method(name = "meterBlockByHash")]
    async fn meter_block_by_hash(&self, hash: B256) -> RpcResult<MeterBlockResponse>;

    /// Handler for: `base_meterBlockByNumber`
    ///
    /// Re-executes a block and returns timing metrics for signer recovery and EVM execution.
    ///
    /// This method fetches the block by number, re-executes all transactions against the parent
    /// block's state, and measures:
    /// - `executionTimeUs`: Time to execute all transactions in the EVM
    /// - `totalTimeUs`: Sum of signer recovery and execution time
    /// - `meteredTransactions`: Per-transaction execution times and gas usage
    #[method(name = "meterBlockByNumber")]
    async fn meter_block_by_number(
        &self,
        number: BlockNumberOrTag,
    ) -> RpcResult<MeterBlockResponse>;
}
```

**File:** crates/execution/metering/src/extension.rs (L36-55)
```rust
impl BaseNodeExtension for MeteringExtension {
    /// Applies the extension to the supplied hooks.
    fn apply(self: Box<Self>, hooks: NodeHooks) -> NodeHooks {
        if !self.enabled {
            return hooks;
        }

        let metered_opcodes = Arc::new(self.metered_opcodes);

        hooks.add_rpc_module(move |ctx| {
            info!("starting metering RPC");
            let metering_api =
                MeteringApiImpl::new(ctx.provider().clone(), Arc::clone(&metered_opcodes));

            ctx.modules.merge_configured(metering_api.into_rpc())?;

            Ok(())
        })
    }
}
```

**File:** crates/execution/metering/src/rpc.rs (L74-109)
```rust
        let header = self
            .provider
            .sealed_header_by_number_or_tag(BlockNumberOrTag::Latest)
            .map_err(|e| {
                jsonrpsee::types::ErrorObjectOwned::owned(
                    jsonrpsee::types::ErrorCode::InternalError.code(),
                    format!("Failed to get canonical block header: {e}"),
                    None::<()>,
                )
            })?
            .ok_or_else(|| {
                jsonrpsee::types::ErrorObjectOwned::owned(
                    jsonrpsee::types::ErrorCode::InternalError.code(),
                    "Canonical block not found".to_string(),
                    None::<()>,
                )
            })?;

        debug!(canonical_block = header.number, "Using canonical block state for metering");

        let parsed_bundle = ParsedBundle::try_from(bundle).map_err(|e| {
            jsonrpsee::types::ErrorObjectOwned::owned(
                jsonrpsee::types::ErrorCode::InvalidParams.code(),
                format!("Failed to parse bundle: {e}"),
                None::<()>,
            )
        })?;

        let state_provider = self.provider.state_by_block_hash(header.hash()).map_err(|e| {
            error!(error = %e, block_hash = %header.hash(), "Failed to get state provider");
            jsonrpsee::types::ErrorObjectOwned::owned(
                jsonrpsee::types::ErrorCode::InternalError.code(),
                format!("Failed to get state provider: {e}"),
                None::<()>,
            )
        })?;
```

**File:** crates/execution/metering/src/rpc.rs (L174-229)
```rust
        let block = self
            .provider
            .block_by_hash(hash)
            .map_err(|e| {
                error!(error = %e, "Failed to get block by hash");
                jsonrpsee::types::ErrorObjectOwned::owned(
                    jsonrpsee::types::ErrorCode::InternalError.code(),
                    format!("Failed to get block: {e}"),
                    None::<()>,
                )
            })?
            .ok_or_else(|| {
                jsonrpsee::types::ErrorObjectOwned::owned(
                    jsonrpsee::types::ErrorCode::InvalidParams.code(),
                    format!("Block not found: {hash}"),
                    None::<()>,
                )
            })?;

        let response = self.meter_block_internal(&block)?;

        debug!(
            block_hash = %hash,
            signer_recovery_time_us = response.signer_recovery_time_us,
            execution_time_us = response.execution_time_us,
            total_time_us = response.total_time_us,
            "Block metering completed successfully"
        );

        Ok(response)
    }

    async fn meter_block_by_number(
        &self,
        number: BlockNumberOrTag,
    ) -> RpcResult<MeterBlockResponse> {
        debug!(block_number = ?number, "Starting block metering by number");

        let block = self
            .provider
            .block_by_number_or_tag(number)
            .map_err(|e| {
                error!(error = %e, "Failed to get block by number");
                jsonrpsee::types::ErrorObjectOwned::owned(
                    jsonrpsee::types::ErrorCode::InternalError.code(),
                    format!("Failed to get block: {e}"),
                    None::<()>,
                )
            })?
            .ok_or_else(|| {
                jsonrpsee::types::ErrorObjectOwned::owned(
                    jsonrpsee::types::ErrorCode::InvalidParams.code(),
                    format!("Block not found: {number:?}"),
                    None::<()>,
                )
            })?;
```

**File:** crates/execution/metering/src/rpc.rs (L259-297)
```rust
    fn get_l1_block_info(&self, block_hash: B256) -> RpcResult<L1BlockInfo> {
        let first_tx = self
            .provider
            .block_by_hash(block_hash)
            .map_err(|e| {
                error!(error = %e, block_hash = %block_hash, "Failed to get block");
                jsonrpsee::types::ErrorObjectOwned::owned(
                    jsonrpsee::types::ErrorCode::InternalError.code(),
                    format!("Failed to get block: {e}"),
                    None::<()>,
                )
            })?
            .ok_or_else(|| {
                jsonrpsee::types::ErrorObjectOwned::owned(
                    jsonrpsee::types::ErrorCode::InvalidParams.code(),
                    format!("Block not found: {block_hash}"),
                    None::<()>,
                )
            })?
            .body
            .transactions
            .first()
            .ok_or_else(|| {
                jsonrpsee::types::ErrorObjectOwned::owned(
                    jsonrpsee::types::ErrorCode::InvalidParams.code(),
                    format!("Block has no transactions: {block_hash}"),
                    None::<()>,
                )
            })?
            .clone();

        extract_l1_info_from_tx(&first_tx).map_err(|e| {
            jsonrpsee::types::ErrorObjectOwned::owned(
                jsonrpsee::types::ErrorCode::InvalidParams.code(),
                format!("Failed to extract L1 block info from transaction: {e}"),
                None::<()>,
            )
        })
    }
```

**File:** crates/consensus/rpc/src/admin.rs (L83-96)
```rust
/// Maps public sequencer admin failures without exposing internal details.
fn sequencer_admin_error(error: SequencerAdminAPIError) -> ErrorObject<'static> {
    match error {
        SequencerAdminAPIError::NotLeader => {
            ErrorObject::owned(-32002, "Node is not the conductor leader.", None::<()>)
        }
        SequencerAdminAPIError::RequestError(_)
        | SequencerAdminAPIError::ResponseError
        | SequencerAdminAPIError::ErrorAfterSequencerWasStopped(_)
        | SequencerAdminAPIError::LeaderOverrideError(_) => {
            ErrorObject::from(ErrorCode::InternalError)
        }
    }
}
```

**File:** crates/infra/audit/src/rpc.rs (L335-342)
```rust
fn internal_rpc_error(error: anyhow::Error) -> ErrorObjectOwned {
    error!(error = %error, "transaction event query failed");
    ErrorObjectOwned::owned(
        ErrorCode::InternalError.code(),
        "internal server error".to_string(),
        None::<()>,
    )
}
```
