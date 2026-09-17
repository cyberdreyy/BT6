### Title
Verbose Internal Error Messages Leaked to Anonymous Callers via `base_meterBundle`/`base_meterBlockByHash`/`base_meterBlockByNumber` RPC Methods - (File: crates/execution/metering/src/rpc.rs)

### Summary
The `base` namespace metering RPC methods (`meterBundle`, `meterBlockByHash`, `meterBlockByNumber`), which are merged into the node's public JSON-RPC surface whenever the metering extension is enabled, return raw internal error strings (via Rust's `{e}` `Display` formatting) directly inside the JSON-RPC `error.message` field returned to the caller.

### Finding Description
`MeteringApiImpl` implements `MeteringApiServer` and is registered on the node's RPC module set by `MeteringExtension::apply`, which merges it unconditionally into the shared RPC modules whenever metering is enabled — the same modules that serve the public `eth_*` namespace to any anonymous JSON-RPC client. [1](#0-0) 

Every fallible internal operation in this handler — header lookup, bundle parsing, state-provider access, bundle execution, block lookup, and block re-execution — wraps the underlying error with `format!("...: {e}")` and returns it as the JSON-RPC error message with `ErrorCode::InternalError`, instead of a generic, sanitized message: [2](#0-1) [3](#0-2) 

The same pattern repeats in `get_l1_block_info` and `meter_block_internal`, which propagate `ProviderError`, `BaseBlockExecutionError`, and other internal error `Display` output verbatim to the caller: [4](#0-3) 

This is the same bug class as CVE-2018-10950 (verbose error messages disclosing internal state to unauthenticated/unprivileged callers): the RPC surface here converts internal error causes (database/provider failures, EVM/bundle execution failures, decode/parsing failures) into user-visible strings rather than using a fixed, sanitized message, unlike the redaction pattern the codebase otherwise follows in other RPC error mappers (e.g. `sequencer_admin_error` explicitly redacts internal details rather than exposing them): [5](#0-4) 

### Impact Explanation
Any anonymous RPC/HTTP client able to reach the node's JSON-RPC endpoint (the same endpoint serving `eth_*`) can trigger these code paths with crafted or malformed `Bundle`, block hash, or block number inputs and receive verbatim internal error text. This can leak internal implementation details — provider/database error internals, EVM execution/halt reasoning, and L1 info extraction failure detail — that operators would not intend to expose externally, aiding further attacks (e.g., fingerprinting internal state, discovering DB/storage failure modes) without requiring any authorization or state-changing action.

### Likelihood Explanation
High likelihood: the affected methods take fully attacker-controlled input (`Bundle`, `B256` hash, `BlockNumberOrTag`) with no additional guard rail, and multiple error branches in the normal control flow (missing block, parse failure, DB error, EVM error) are trivially reachable by any client with network access to the metering-enabled RPC port.

### Recommendation
Replace all `format!("...: {e}")` error messages in `crates/execution/metering/src/rpc.rs` with generic, sanitized messages (mirroring the pattern already used in `crates/consensus/rpc/src/admin.rs`'s `sequencer_admin_error`), logging the detailed error server-side via `error!`/`debug!` (as is already partially done) but never embedding raw error `Display` output in the `ErrorObjectOwned` returned to the RPC caller.

### Proof of Concept
1. Enable the metering extension on a Base execution node (`MeteringExtension` with `enabled = true`), exposing `base_meterBundle`, `base_meterBlockByHash`, `base_meterBlockByNumber` on the public JSON-RPC port.
2. As an unauthenticated client, send `base_meterBlockByHash` with a non-existent or malformed block hash, or `base_meterBundle` with a bundle designed to trigger a state-provider/database error.
3. Observe the JSON-RPC response's `error.message` field contains the raw internal error string (e.g., `"Failed to get block: <provider internal error>"`, `"Block metering failed: <execution internal error>"`), confirming information disclosure through verbose error messages as in `crates/execution/metering/src/rpc.rs` lines 74-136 and 259-309.

### Citations

**File:** crates/execution/metering/src/extension.rs (L36-54)
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

**File:** crates/execution/metering/src/rpc.rs (L111-136)
```rust
        let l1_block_info = self.get_l1_block_info(header.hash())?;
        let state_block_number = header.number;

        let output = meter_bundle(MeterBundleInput {
            state_provider,
            chain_spec: self.provider.chain_spec(),
            bundle: parsed_bundle,
            header,
            l1_block_info,
            metered_opcodes: Arc::clone(&self.metered_opcodes),
        })
        .map_err(|e| {
            // Sample error msg:
            // Transaction $TX_HASH execution failed: EVM reported invalid transaction ($TX_HASH): nonce $EXPECTED_NONCE too high, expected $EXPECTED_NONCE"
            let error_msg = e.to_string();
            if error_msg.contains("nonce") {
                debug!(error = %e, "Bundle metering failed");
            } else {
                info!(error = %e, "Bundle metering failed");
            }
            jsonrpsee::types::ErrorObjectOwned::owned(
                jsonrpsee::types::ErrorCode::InternalError.code(),
                format!("Bundle metering failed: {e}"),
                None::<()>,
            )
        })?;
```

**File:** crates/execution/metering/src/rpc.rs (L259-309)
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

    /// Internal helper to meter a block's execution
    fn meter_block_internal(&self, block: &BaseBlock) -> RpcResult<MeterBlockResponse> {
        meter_block(self.provider.clone(), self.provider.chain_spec(), block).map_err(|e| {
            error!(error = %e, "Block metering failed");
            jsonrpsee::types::ErrorObjectOwned::owned(
                jsonrpsee::types::ErrorCode::InternalError.code(),
                format!("Block metering failed: {e}"),
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
