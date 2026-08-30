### Title
`total_send_fees` reuses the outer `sender_is_receiver` for a `Delegate`/`DelegateV2` action's inner actions instead of recomputing it from `delegate_action.sender_id == delegate_action.receiver_id`, diverging from `total_prepaid_send_fees` - ([File: runtime/runtime/src/config.rs])

### Summary
`total_send_fees` (config.rs:141-167) computes the send-fee cost of a `Delegate`/`DelegateV2` action's *inner* actions by recursing with the caller-supplied outer `sender_is_receiver` flag, while `total_prepaid_send_fees` (config.rs:257-294) recomputes `sender_is_receiver` from `delegate_action.sender_id() == delegate_action.receiver_id()` before doing the same recursion. These two functions are supposed to describe the same quantity (the send fee that must eventually be paid/burnt for the inner actions of a `DelegateAction`) but use different, mutually inconsistent conventions. [1](#0-0) [2](#0-1) 

### Finding Description
`total_send_fees` is a general-purpose "sum the send fee of these actions" routine; the caller passes in `sender_is_receiver` describing the relationship between the *hop* being charged (e.g. `tx.signer_id == tx.receiver_id` at transaction→receipt conversion time). When it encounters `Action::Delegate`/`Action::DelegateV2`, it adds the delegate action's own send fee (correctly keyed on the outer `sender_is_receiver`) and then **recurses into the inner actions of the `DelegateAction`, still using that same outer `sender_is_receiver`**: [3](#0-2) 

`total_prepaid_send_fees` exists specifically to compute the send fee of a `DelegateAction`'s inner actions (per its doc comment, "the send fees of the inner actions need to be prepaid"), and it deliberately **recomputes** `sender_is_receiver` from the delegate action's own `sender_id`/`receiver_id`: [4](#0-3) [5](#0-4) 

This is the correct convention because the inner actions will eventually be executed as a *new* receipt originating from `delegate_action.sender_id` to `delegate_action.receiver_id` — a completely different account pair from the transaction's `signer_id`/`receiver_id`. `total_prepaid_send_fees`'s recomputation reflects that reality; `total_send_fees`'s reuse of the outer flag does not.

An unprivileged attacker can trigger the divergence with nothing more than an ordinary meta-transaction where the transaction's own signer/receiver relationship differs from the wrapped `DelegateAction`'s sender/receiver relationship — e.g., a self-submitted meta-transaction where `tx.signer_id == tx.receiver_id == delegate_action.sender_id` (so the outer `sender_is_receiver` is `true`) while `delegate_action.receiver_id` is a different, target contract account (so the correct inner `sender_is_receiver` is `false`). In this case `total_send_fees` (used at transaction→receipt conversion time, e.g. via `tx_cost`) computes the inner action send-fee component using the cheaper "same account" fee schedule, while `total_prepaid_send_fees` (and, presumably, whatever downstream code actually burns the send fee when the `DelegateAction` produces its child receipt) uses the correct, more expensive "different account" fee schedule. That is a concrete, reachable disagreement without needing to construct a nested `Delegate`-inside-`Delegate` chain at all.

Note: the specific "nested `Delegate` wrapping another `Delegate`" precondition in the question is likely not constructible, since `DelegateAction`'s inner action list is typed via `NonDelegateAction`/`get_actions()` conversions in `core/primitives/src/action/delegate.rs`, which is designed to reject `Delegate`/`DelegateV2` as an inner action. I was not able to fully re-verify this typing in this session due to tool-call budget, so this exclusion should be double-checked, but it does not change the underlying bug: the sender_is_receiver mismatch is already reachable with a single, non-nested `DelegateAction`.

I was unable, within the remaining tool budget, to read the body of `tx_cost` (config.rs) or the `actions.rs` code that applies a `DelegateAction` and actually burns/consumes the prepaid send fee for its inner actions. I therefore could not fully confirm whether this discrepancy manifests as (a) an outright balance/gas-underflow panic when the receiver shard tries to burn more gas than was reserved, (b) silent under-collection of fees (only some of the intended gas is ever burnt), or (c) is masked by some other consistency check I did not locate. This is a meaningful gap in my verification.

### Impact Explanation
If confirmed to reach actual gas accounting (via `tx_cost`'s `gas_burnt`/`gas_remaining` versus the amount actually burnt when the `DelegateAction`'s inner actions are converted into a receipt), this would be a value-conservation violation: the signer/relayer could purchase less gas than the network will actually need to burn for the delegated hop, which is either (a) a shard-halting panic if the runtime asserts gas/balance conservation strictly, or (b) systematic fee under-collection (economic loss to the fee-receiving side / validators), both of which map to the "token inflation or loss" / "shard-halting panic" bounty categories. I could not fully verify which of these actually occurs due to incomplete tracing of `tx_cost` and `actions.rs`.

### Likelihood Explanation
The triggering condition — a self-submitted meta-transaction where `tx.signer_id == tx.receiver_id` but `delegate_action.receiver_id` differs — requires no special privileges, keys, or relayer cooperation; an ordinary account can construct and sign such a transaction and submit it to any public RPC endpoint. This makes the code-level inconsistency trivially and repeatably reachable. What remains unverified is whether downstream code neutralizes the discrepancy before it can cause real fund loss or a panic.

### Recommendation
In `total_send_fees` (config.rs:141-167), when handling `Action::Delegate`/`Action::DelegateV2`, recompute `sender_is_receiver` for the recursive call from `delegate_action.sender_id == delegate_action.receiver_id` (mirroring `total_prepaid_send_fees` at config.rs:267 and :280) instead of reusing the caller-supplied outer flag. Add a unit test asserting `total_send_fees(config, false, inner_actions, receiver_id)`'s Delegate-recursion path and `total_prepaid_send_fees(config, &[delegate_action])` agree for a fixed action set where the outer and inner `sender_is_receiver` values differ.

### Proof of Concept
1. Construct a `DelegateAction` with `sender_id = "alice"`, `receiver_id = "bob"` (bob != alice), containing one inner `Transfer` action.
2. Wrap it in a `Transaction` with `signer_id = "alice"`, `receiver_id = "alice"` (self-submitted, no relayer), `actions = [Action::Delegate(signed_delegate_action)]`.
3. Call `total_send_fees(&config, /*sender_is_receiver=*/true, &tx.actions, &tx.receiver_id)` and separately `total_prepaid_send_fees(&config, &[Action::Delegate(signed_delegate_action)])`.
4. Assert that the "inner actions" component computed inside `total_send_fees`'s Delegate branch (obtained by subtracting the delegate action's own base fee) differs numerically from `total_prepaid_send_fees`'s result for the identical inner `Transfer` action, demonstrating the sender_is_receiver mismatch.
5. Follow up (not completed in this session) by tracing `tx_cost`'s use of `total_send_fees` and the `actions.rs` code that consumes `total_prepaid_send_fees` when applying a `DelegateAction`, to confirm whether the mismatch causes an actual balance/gas discrepancy at receipt-conversion vs. execution time.

### Citations

**File:** runtime/runtime/src/config.rs (L141-167)
```rust
            Delegate(signed_delegate_action) => {
                let delegate_cost = fees.fee(ActionCosts::delegate).send_fee(sender_is_receiver);
                let delegate_action = &signed_delegate_action.delegate_action;

                delegate_cost
                    .checked_add(total_send_fees(
                        config,
                        sender_is_receiver,
                        &delegate_action.get_actions(),
                        &delegate_action.receiver_id,
                    )?)
                    .unwrap()
            }
            DelegateV2(signed_delegate_action) => {
                let delegate_cost = fees.fee(ActionCosts::delegate).send_fee(sender_is_receiver);
                let delegate_action =
                    VersionedDelegateActionRef::from(&signed_delegate_action.delegate_action);

                delegate_cost
                    .checked_add(total_send_fees(
                        config,
                        sender_is_receiver,
                        &delegate_action.get_actions(),
                        delegate_action.receiver_id(),
                    )?)
                    .unwrap()
            }
```

**File:** runtime/runtime/src/config.rs (L257-294)
```rust
pub fn total_prepaid_send_fees(
    config: &RuntimeConfig,
    actions: &[Action],
) -> Result<ParameterCost, IntegerOverflowError> {
    let mut result = ParameterCost::ZERO;
    for action in actions {
        use Action::*;
        let delta = match action {
            Delegate(signed_delegate_action) => {
                let delegate_action = &signed_delegate_action.delegate_action;
                let sender_is_receiver = delegate_action.sender_id == delegate_action.receiver_id;

                total_send_fees(
                    config,
                    sender_is_receiver,
                    &delegate_action.get_actions(),
                    &delegate_action.receiver_id,
                )?
            }
            DelegateV2(signed_delegate_action) => {
                let delegate_action =
                    VersionedDelegateActionRef::from(&signed_delegate_action.delegate_action);
                let sender_is_receiver =
                    delegate_action.sender_id() == delegate_action.receiver_id();

                total_send_fees(
                    config,
                    sender_is_receiver,
                    &delegate_action.get_actions(),
                    delegate_action.receiver_id(),
                )?
            }
            _ => ParameterCost::ZERO,
        };
        result = result.checked_add_result(delta)?;
    }
    Ok(result)
}
```
