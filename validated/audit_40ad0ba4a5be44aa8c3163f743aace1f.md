### Title
`accountSubscribe` silently discards `minContextSlot`, breaking the client's context-slot guarantee - ([File: rpc/src/rpc_pubsub.rs])

### Summary
`RpcSolPubSubImpl::account_subscribe` accepts `min_context_slot` as part of `RpcAccountInfoConfig`, but the field is explicitly destructured and thrown away with `min_context_slot: _, // ignored` instead of being enforced. This mirrors the H-4 pattern: a caller-supplied parameter that is meant to gate/route behavior is accepted by the function signature but never actually consulted, so the operation silently proceeds using the wrong context instead of honoring what the caller asked for.

### Finding Description
In `account_subscribe`, the `RpcAccountInfoConfig` is destructured and `min_context_slot` is dropped: [1](#0-0) 

```
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

`AccountSubscriptionParams` itself has no `min_context_slot` field at all, so the value can never be propagated further down the pipeline. Compare this with the equivalent unary RPC call `getAccountInfo`, which correctly threads `min_context_slot` into `get_bank_with_config` and rejects the request with `MinContextSlotNotReached` if the bank hasn't caught up: [2](#0-1) [3](#0-2) 

For pubsub, `initial_last_notified_slot` (used to seed the subscription's baseline slot) only looks at `params.commitment`, with no `min_context_slot` check anywhere in the path: [4](#0-3) 

This is functionally the same bug shape as the Teller H-4 report: `_lenderCloseLoanWithRecipient` accepted `_collateralRecipient` but never used it, so the call silently used the wrong target instead of enforcing the caller's stated intent. Here, `account_subscribe` accepts `min_context_slot` (the caller's stated intent — "don't give me data from before slot X") but never enforces it, so the subscription can be established and can immediately deliver a notification with account data taken from a bank/slot below the minimum slot the client explicitly required.

### Impact Explanation
A caller that relies on `minContextSlot` to avoid acting on stale state (a documented, intended per-request consistency guarantee that `getAccountInfo`/`getMultipleAccounts`/etc. enforce) gets no such guarantee from `accountSubscribe`. The subscription is accepted unconditionally and the very first notification (and all subsequent ones) can reflect account state from a slot earlier than what was requested. This is a "wrong-slot data returned from a query" class of bug: the RPC method returns/streams account data that does not satisfy the caller's explicitly stated consistency requirement, silently. It does not corrupt validator consensus state, but it does return incorrect/misleading data to an RPC client relying on the parameter for correctness, matching the accepted-impact category of "wrong-slot/fork/account data returned" from a single unprivileged JSON-RPC pubsub call.

### Likelihood Explanation
This triggers on every legitimate `accountSubscribe` call that includes `minContextSlot` in its config — no special conditions, races, or privileges are required. Any unprivileged RPC client can observe the discrepancy by subscribing with a `minContextSlot` above the current bank slot and immediately receiving/being eligible for notifications instead of an error or a delayed first notification.

### Recommendation
Either honor `min_context_slot` in `account_subscribe` (and the analogous `program_subscribe`, `signature_subscribe`, `logs_subscribe` if they accept it) by rejecting the subscription with `MinContextSlotNotReached` until the bank/commitment-tracked slot reaches the requested minimum, or, if pubsub intentionally does not support this parameter, reject requests that supply a non-default `min_context_slot` with an explicit `InvalidParams` error instead of silently accepting and discarding it.

### Proof of Concept
1. Start a validator/RPC node whose current bank slot is `S`.
2. Call `accountSubscribe(pubkey, { "minContextSlot": S + 1000 })`.
3. Observe the subscription succeeds immediately (no `MinContextSlotNotReached` error), and the client begins receiving `accountNotification` messages for account state derived from slot `S` (or later, as it naturally progresses) — well before slot `S + 1000` is ever reached — contrary to the semantics `minContextSlot` provides for `getAccountInfo`/`getMultipleAccounts`.

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

**File:** rpc/src/rpc.rs (L534-548)
```rust
    pub async fn get_account_info(
        &self,
        pubkey: Pubkey,
        config: Option<RpcAccountInfoConfig>,
    ) -> Result<RpcResponse<Option<UiAccount>>> {
        let RpcAccountInfoConfig {
            encoding,
            data_slice,
            commitment,
            min_context_slot,
        } = config.unwrap_or_default();
        let bank = self.get_bank_with_config(RpcContextConfig {
            commitment,
            min_context_slot,
        })?;
```

**File:** rpc/src/rpc_subscriptions.rs (L454-478)
```rust
fn initial_last_notified_slot(
    params: &SubscriptionParams,
    bank_forks: &RwLock<BankForks>,
    block_commitment_cache: &RwLock<BlockCommitmentCache>,
    optimistically_confirmed_bank: &RwLock<OptimisticallyConfirmedBank>,
) -> Option<Slot> {
    match params {
        SubscriptionParams::Account(params) => {
            let slot = if params.commitment.is_finalized() {
                block_commitment_cache
                    .read()
                    .unwrap()
                    .highest_super_majority_root()
            } else if params.commitment.is_confirmed() {
                optimistically_confirmed_bank.read().unwrap().bank.slot()
            } else {
                block_commitment_cache.read().unwrap().slot()
            };

            let bank = bank_forks.read().unwrap().get(slot)?;
            Some(bank.get_account_modified_slot(&params.pubkey)?.1)
        }
        _ => None,
    }
}
```
