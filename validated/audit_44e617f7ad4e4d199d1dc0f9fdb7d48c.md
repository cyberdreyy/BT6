### Title
`min_context_slot` request parameter is silently ignored by `accountSubscribe`, allowing stale/wrong-slot account data to be delivered on a lagging node - ([File: rpc/src/rpc_pubsub.rs])

### Summary
The `min_context_slot` field of `RpcAccountInfoConfig` is meant to be a per-request guarantee that the RPC node will not answer using state older than a caller-specified slot — it is enforced by `getAccountInfo` and other synchronous RPC methods via `MinContextSlotNotReached`. In `RpcSolPubSubImpl::account_subscribe`, however, the same field is explicitly destructured and discarded, so the guarantee that is enforced everywhere else in the RPC surface is silently dropped for the JSON-RPC PubSub `accountSubscribe`/notification path, exactly the same class of bug as the WLFI report: a state/consistency flag is enforced at some entry points but not at the one that matters for ongoing delivery of data to the caller.

### Finding Description
`account_subscribe` in `rpc/src/rpc_pubsub.rs` explicitly ignores the caller-supplied `min_context_slot`:

```rust
// rpc/src/rpc_pubsub.rs
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
    ...
}
``` [1](#0-0) 

By contrast, the synchronous RPC surface (e.g. `getAccountInfo`) checks `min_context_slot` against the current bank and returns `Error::MinContextSlotNotReached` if the node has not yet reached that slot — this is the mechanism callers rely on to avoid being served stale or pre-fork state from a lagging/partitioned validator, and it is defined as a first-class RPC error in `rpc-client-api/src/custom_error.rs` and consumed throughout `rpc/src/rpc.rs`.

The subscription pipeline in `rpc/src/rpc_subscriptions.rs` (`AccountSubscriptionParams`, `check_commitment_and_notify`, `notify_watchers`) never carries or checks a minimum-slot requirement for account subscriptions — it only tracks `commitment` and `last_notified_slot`. This means:

- A client that specifies `min_context_slot` on `accountSubscribe` (to require the node be caught up to a known slot before trusting its account view) gets no such guarantee.
- If the RPC node being queried is behind the cluster (e.g. mid-catchup, on a minority fork, or otherwise lagging), it will still open the subscription and start emitting account-state notifications for stale/wrong-fork slots as soon as its own bank updates cross the commitment level, without ever verifying it has reached the slot the client asked for.

This mirrors the WLFI bug class precisely: the "enabled"/guard check is implemented and honored on some code paths (`getAccountInfo` → `MinContextSlotNotReached`) but is a no-op on the parallel "ongoing" path (`accountSubscribe` → notification stream), so the intended per-request consistency guarantee silently stops applying once the client moves from a single query to a subscription.

### Impact Explanation
This is a wrong-slot/fork data-return bug reachable by any unprivileged JSON-RPC PubSub client with a single subscribe call, no special privileges required. A caller who deliberately sets `min_context_slot` to guard against consuming data from a lagging or forked node can be silently served account updates from a bank state that predates the slot they required, since the guard is dropped rather than enforced. Because `min_context_slot` exists specifically to prevent this "wrong state returned" outcome for the synchronous API, its silent omission from the WS subscription API is a genuine consistency violation with concrete downstream impact for any consumer (e.g. wallets/exchanges) that depend on `min_context_slot` to avoid acting on pre-catch-up or minority-fork account state.

### Likelihood Explanation
High likelihood of being triggered unintentionally (any client passing `min_context_slot` to `accountSubscribe` against a node that is behind) and trivially reachable by an unprivileged caller with a single WebSocket subscribe request — no elevated permissions, no multiple calls, and no special node configuration required.

### Recommendation
Thread `min_context_slot` through `AccountSubscriptionParams` (and the analogous `program_subscribe`/other config-driven subscriptions that accept `RpcAccountInfoConfig`) the same way `commitment` is threaded today, and reject the subscribe call (or defer delivering notifications) until the bank at the requested commitment level has reached `min_context_slot`, mirroring the `MinContextSlotNotReached` behavior already implemented for `getAccountInfo` in `rpc/src/rpc.rs`.

### Proof of Concept
1. Start a node that is behind the cluster (e.g. mid-startup catch-up) with PubSub enabled.
2. Call `accountSubscribe` with `{"encoding":"base64","commitment":"confirmed","minContextSlot": <slot far ahead of node's current bank>}`.
3. Observe that the subscription is accepted immediately (`account_subscribe` never reads `min_context_slot`), and as soon as the node's own (stale) bank crosses the configured commitment for that account, a notification with account state from before the required slot is delivered — behavior a client would not have chosen had `min_context_slot` been honored as it is for `getAccountInfo`.

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
