### Title
Builder pending withdrawals can pay the execution layer the full requested amount while the beacon state only debits the builder's capped (lesser) balance, inflating total ETH supply - (specs/gloas/beacon-chain.md)

### Summary
`get_builder_withdrawals` places the *requested* `BuilderPendingWithdrawal.amount` verbatim into the `Withdrawal.amount` field that is authoritative for the execution layer, while `apply_withdrawals` only debits `min(withdrawal.amount, builder_balance)` from the builder's own CL-side balance. If a builder's balance is ever lower than the amount already queued for it, the EL mints/pays the fee recipient the full queued amount but the CL removes less than that from the builder's balance, permanently creating Gwei out of thin air — breaking the "Gwei paid == Gwei debited" equality, analogous to the Notional PT report's reliance on a value that can diverge from the actual available balance.

### Finding Description
`get_builder_withdrawals` (specs/gloas/beacon-chain.md) builds the committed `Withdrawal` objects directly from the queue entries without ever comparing them to the builder's live balance: [1](#0-0) 

These `Withdrawal` objects become `state.payload_expected_withdrawals`, which the spec explicitly documents as binding on the execution layer — "any execution payload that has the corresponding block as parent beacon block is required to honor these withdrawals in the execution layer" and "the execution layer mints the full committed amount regardless" of what the CL later does: [2](#0-1) 

Meanwhile `apply_withdrawals` clamps the actual balance decrement to what the builder currently has: [3](#0-2) 

So the amount the EL is obligated to pay (`withdrawal.amount`, the raw queued value) and the amount the CL removes from the paying party (`min(withdrawal.amount, builder_balance)`) are two different, independently-computed quantities — exactly the "use of a value that can diverge from the real balance" pattern in the Notional report, where `maxRedeem()` was used instead of the actual redeemable balance and the two values could silently diverge.

### Impact Explanation
If `builder.balance < builder_pending_withdrawal.amount` at settlement time, the EL pays the `fee_recipient` the full `amount`, but the builder's CL balance is only reduced by the smaller `builder_balance`. The difference is Gwei that is paid out on the EL side without any corresponding debit anywhere in the CL accounting — a net supply inflation, i.e. "Gwei created," which the rules classify as Critical impact. This is a stronger and more direct violation than the Notional case (which caused *loss/freezing*), because here it is outright *creation* of value with no corresponding debit, and unlike the documented validator-sweep saturation caveat (which is bounded/acknowledged for `decrease_balance`), the builder branch computes its own explicit `min()`-based cap that is inconsistent with the withdrawal amount already locked into the payload.

### Likelihood Explanation
The report's own design note only calls out this trade-off in the context of validator withdrawals reducing via `process_pending_consolidations` between commit and apply — it does not discuss the builder branch, and the builder branch has an explicit `min()` guard, suggesting the spec authors treated it as "safe," yet the guard only protects the CL-side ledger and does nothing to prevent the EL from being committed to pay more than the builder actually has. Whether a builder's balance can actually fall below a queued payment before settlement (e.g., via `process_builder_exit_request`, multiple pending payments queued in the same window that jointly exceed the balance, or slashable-adjacent balance reduction paths for builders) needs to be traced through `settle_builder_payment`/`process_builder_pending_payments`/`process_builder_exit_request`, which I was not able to fully inspect within the remaining budget — their exact bodies (in specs/gloas/beacon-chain.md) should be reviewed to confirm whether an honest sequence of builder deposit/bid/exit operations can produce `builder.balance < sum of its queued builder_pending_withdrawals.amount` without requiring a malicious peer or client bug, purely from spec-following state transitions.

### Recommendation
Cap the amount placed into the committed `Withdrawal` in `get_builder_withdrawals` to `min(withdrawal.amount, builder.balance)` (mirroring what `apply_withdrawals` already does), so the amount the EL is obligated to pay is always identical to the amount debited from the builder, preventing any possibility of the CL committing to more Gwei than the builder actually holds.

### Proof of Concept
Conceptual state transition (exact preconditions on `settle_builder_payment` / builder balance reduction paths still need confirmation, see Likelihood):
1. Builder `B` has `balance = X`.
2. A `BuilderPendingWithdrawal` for `B` with `amount = X` gets queued (via `settle_builder_payment`/`apply_parent_execution_payload`).
3. Before this withdrawal is processed, some other spec-following state transition legitimately reduces `B.balance` to `Y < X` (e.g., another queued payment for `B` is committed first, or `process_builder_exit_request` triggers a sweep withdrawal that empties `B.balance`).
4. `process_withdrawals` runs: `get_builder_withdrawals` emits `Withdrawal(amount=X)` for `B` (uncapped) into `state.payload_expected_withdrawals`.
5. `apply_withdrawals` executes `B.balance -= min(X, Y) = Y`, leaving `B.balance = 0`.
6. The execution layer, bound by `payload_expected_withdrawals`, must pay `X` Gwei to the fee recipient, but only `Y` Gwei was ever debited anywhere in the beacon state — `X - Y` Gwei has been created with no corresponding source, violating the "no Gwei created/paid beyond what is committed" equality.

### Citations

**File:** specs/gloas/beacon-chain.md (L1815-1833)
```markdown
    for withdrawal in state.builder_pending_withdrawals:
        all_withdrawals = list(prior_withdrawals) + withdrawals
        has_reached_limit = len(all_withdrawals) >= withdrawals_limit
        if has_reached_limit:
            break

        builder_index = withdrawal.builder_index
        withdrawals.append(
            Withdrawal(
                index=withdrawal_index,
                validator_index=convert_builder_index_to_validator_index(builder_index),
                address=withdrawal.fee_recipient,
                amount=withdrawal.amount,
            )
        )
        withdrawal_index += 1
        processed_count += 1

    return withdrawals, withdrawal_index, processed_count
```

**File:** specs/gloas/beacon-chain.md (L1920-1932)
```markdown
##### Modified `apply_withdrawals`

```python
def apply_withdrawals(state: BeaconState, withdrawals: Sequence[Withdrawal]) -> None:
    for withdrawal in withdrawals:
        # [Modified in Gloas:EIP7732]
        if is_builder_index(withdrawal.validator_index):
            builder_index = convert_validator_index_to_builder_index(withdrawal.validator_index)
            builder_balance = state.builders[builder_index].balance
            state.builders[builder_index].balance -= min(withdrawal.amount, builder_balance)
        else:
            decrease_balance(state, withdrawal.validator_index, withdrawal.amount)
```
```

**File:** specs/gloas/beacon-chain.md (L1969-1989)
```markdown
*Note*: This is modified to only take the `state` as parameter. Withdrawals are
deterministic given the beacon state, any execution payload that has the
corresponding block as parent beacon block is required to honor these
withdrawals in the execution layer. `process_withdrawals` must be called after
`process_parent_execution_payload` (which updates `state.latest_block_hash`) and
before `process_execution_payload_bid` as the latter function affects validator
balances.

*Note*: Unlike deposits (which are applied at the child's slot via
`apply_parent_execution_payload`), withdrawal balance deductions are applied
immediately via `apply_withdrawals`. Deferring the deduction to the child's slot
would break the total supply invariant: state transitions between the commitment
slot and the deduction slot (e.g., `process_pending_consolidations` at an epoch
boundary) can reduce a validator's balance below the committed withdrawal
amount, causing `decrease_balance` to saturate at zero. Since the execution
layer mints the full committed amount regardless, any CL-side saturation creates
a net supply inflation. As a consequence, `state.balances` reflects the
withdrawal deduction before the corresponding execution payload is confirmed,
creating a transient asymmetry with the EL state at `state.latest_block_hash`.
Off-chain consumers that require CL/EL balance consistency can reconstruct
pre-deduction balances by adding back `state.payload_expected_withdrawals`.
```
