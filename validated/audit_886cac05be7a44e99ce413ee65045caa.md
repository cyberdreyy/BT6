Confirmed: `program_subscribe` similarly accepts `RpcProgramAccountsConfig` (which flattens `RpcAccountInfoConfig`, including `min_context_slot`) but never reads `config.account_config.min_context_slot` when building `ProgramSubscriptionParams` — it's dropped just like in `account_subscribe`. [1](#0-0) [2](#0-1) 

### Title
`accountSubscribe`/`programSubscribe` silently discard `minContextSlot`, causing stale/wrong-slot data to be streamed to unprivileged subscribers - (File: rpc/src/rpc_pubsub.rs)

### Summary
`RpcAccountInfoConfig` (and the `RpcProgramAccountsConfig` that flattens it) contains a `min_context_slot` field whose documented purpose, matching its use in every HTTP RPC method (e.g. `get_bank_with_config`), is to guarantee that the returned/streamed data reflects at least the requested slot before servicing the request [3](#0-2) . In the pubsub implementation of `account_subscribe`, the field is explicitly destructured and thrown away with `min_context_slot: _, // ignored` [4](#0-3) . The `program_subscribe` handler exhibits the same defect: it builds `ProgramSubscriptionParams` from `config.account_config` but never reads `config.account_config.min_context_slot` [5](#0-4) .

### Finding Description
The `AccountSubscriptionParams` and `ProgramSubscriptionParams` structs built by these handlers carry `commitment`, `encoding`, and `data_slice`, but no `min_context_slot`, so this constraint is unenforceable downstream and never checked against the slot at subscription time or for subsequent notifications. A caller supplying `minContextSlot` on `accountSubscribe`/`programSubscribe` — mirroring the exact same field used successfully on `getAccountInfo`/`getProgramAccounts` and enforced via `RpcCustomError::MinContextSlotNotReached` in the synchronous RPC path [6](#0-5)  — has every reason to believe the same guarantee applies to the subscription. Instead, the subscription is established immediately regardless of the current bank slot, and the client will receive notifications reflecting older/stale account or program-account state than what it explicitly asked to avoid.

### Impact Explanation
This is a wrong-slot/stale-data-returned issue: a caller uses a documented safety parameter to avoid acting on stale account state (e.g. sequencing decisions across RPC nodes that may lag), and the validator silently ignores it without any warning or error, unlike the `getAccountInfo`/`getProgramAccounts` HTTP methods which reject the same situation with `MinContextSlotNotReached`. The impact is low-to-moderate because the caller still receives *some* data, but that data is not what was contractually requested, and there's no way for the client to detect the omission from the subscription confirmation.

### Likelihood Explanation
High: any client that reuses the same `RpcAccountInfoConfig`/`RpcProgramAccountsConfig` types (which is idiomatic since they are shared between HTTP and pubsub APIs) and passes `min_context_slot` when subscribing will unknowingly hit this gap every time, with 100% certainty since the code path unconditionally discards the field.

### Recommendation
Either (a) reject subscription requests that include `min_context_slot` with an explicit "unsupported parameter" error to fail loudly instead of silently, or (b) implement the intended semantics by validating the current bank slot against `min_context_slot` at subscribe time (and, if feasible, suppressing/delaying notifications until that slot is reached), consistent with how `get_bank_with_config` enforces it for the synchronous methods [3](#0-2) .

### Proof of Concept
1. Call `accountSubscribe` (or `programSubscribe`) with `{"minContextSlot": <slot far in the future>}`.
2. Observe the subscription succeeds immediately (`Ok(SubscriptionId)`), and the client begins receiving notifications from the current node slot, even though the node has not reached `minContextSlot`.
3. Compare with `getAccountInfo` using the same `minContextSlot`: that call correctly fails with `MinContextSlotNotReached` per `get_bank_with_config` at [6](#0-5) , demonstrating the inconsistency and confirming the parameter is meaningful elsewhere but unused here.

### Citations

**File:** rpc/src/rpc_pubsub.rs (L424-443)
```rust
impl RpcSolPubSubInternal for RpcSolPubSubImpl {
    fn account_subscribe(
        &self,
        pubkey_str: String,
        config: Option<RpcAccountInfoConfig>,
    ) -> Result<SubscriptionId> {
        let RpcAccountInfoConfig {
            encoding,
            data_slice,
            commitment,
            min_context_slot: _, // ignored
        } = config.unwrap_or_default();
        let params = AccountSubscriptionParams {
            pubkey: param::<Pubkey>(&pubkey_str, "pubkey")?,
            commitment: commitment.unwrap_or_default(),
            data_slice,
            encoding: encoding.unwrap_or(UiAccountEncoding::Binary),
        };
        self.subscribe(SubscriptionParams::Account(params))
    }
```

**File:** rpc/src/rpc_pubsub.rs (L449-476)
```rust
    fn program_subscribe(
        &self,
        pubkey_str: String,
        config: Option<RpcProgramAccountsConfig>,
    ) -> Result<SubscriptionId> {
        let config = config.unwrap_or_default();
        let mut filters = config.filters.unwrap_or_default();
        if let Err(error) = verify_filters(&filters) {
            return Err(Error {
                code: ErrorCode::InvalidParams,
                message: error.to_string(),
                data: None,
            });
        }
        optimize_filters(&mut filters);
        let params = ProgramSubscriptionParams {
            pubkey: param::<Pubkey>(&pubkey_str, "pubkey")?,
            filters,
            encoding: config
                .account_config
                .encoding
                .unwrap_or(UiAccountEncoding::Binary),
            data_slice: config.account_config.data_slice,
            commitment: config.account_config.commitment.unwrap_or_default(),
            with_context: config.with_context.unwrap_or_default(),
        };
        self.subscribe(SubscriptionParams::Program(params))
    }
```

**File:** rpc/src/rpc.rs (L273-289)
```rust
impl JsonRpcRequestProcessor {
    fn get_bank_with_config(&self, config: RpcContextConfig) -> Result<Arc<Bank>> {
        let RpcContextConfig {
            commitment,
            min_context_slot,
        } = config;
        let bank = self.bank(commitment);
        if let Some(min_context_slot) = min_context_slot
            && bank.slot() < min_context_slot
        {
            return Err(RpcCustomError::MinContextSlotNotReached {
                context_slot: bank.slot(),
            }
            .into());
        }
        Ok(bank)
    }
```
