### Title
Uncapped builder withdrawal amount committed to payload while CL deduction is capped at available balance — creates unbacked Gwei - ([File: specs/gloas/beacon-chain.md])

### Summary
In Gloas (EIP-7732), builder payments are recorded as `BuilderPendingWithdrawal.amount` and later converted into `Withdrawal` objects that the execution layer is contractually required to honor exactly. The conversion function `get_builder_withdrawals` copies the requested `amount` unmodified into the `Withdrawal`, but the balance-mutating function `apply_withdrawals` only deducts `min(withdrawal.amount, builder_balance)` from the builder's CL-tracked balance. If a builder's balance has fallen below the committed amount by the time the withdrawal is processed, the EL-side payload commits/pays the full uncapped amount while the CL only debits the (smaller) available balance — creating Gwei that is not backed by any real balance decrease, exactly mirroring the `_claimRewardsOnBehalf()` pattern where the cap check happens on the wrong side of the equality.

### Finding Description
`get_builder_withdrawals` builds the withdrawal to be committed in the block's `payload_expected_withdrawals` using the raw requested `amount`, with no cap against the builder's current balance: [1](#0-0) 

That committed `Withdrawal` object is exactly what "any execution payload that has the corresponding block as parent beacon block is required to honor" per the spec's own note on `process_withdrawals`: [2](#0-1) 

But when the CL applies this same withdrawal to internal state, `apply_withdrawals` silently caps the deduction to `min(withdrawal.amount, builder_balance)`: [3](#0-2) 

This is confirmed as intended/tested behavior (not merely a hypothetical edge case) by the spec test suite, which explicitly asserts the withdrawal is emitted for the full requested amount while the builder's balance is only reduced to zero: [4](#0-3) 

The equality that should hold is: `Gwei credited to fee_recipient on EL == Gwei debited from builder.balance on CL`. When `withdrawal.amount > builder.balance`, this equality breaks: the EL side pays/mints the full `withdrawal.amount` (since the withdrawal is a fixed commitment the block must honor), while the CL side only ever subtracts the builder's actual balance. The difference is Gwei created from nothing, paid to `withdrawal.fee_recipient` — a party that need not be the builder's own `execution_address` (the report/spec test explicitly notes `fee_recipient` is "Independent of builder's own execution_address").

A concrete path to a mismatch: `BuilderPendingWithdrawal.amount` is validated against the builder's balance only at bid-acceptance time via `can_builder_cover_bid`/`process_execution_payload_bid`, and payments sit in `builder_pending_payments`/`builder_pending_withdrawals` for potentially multiple epochs (delayed inclusion, missed slots, multiple queued payments) before `get_builder_withdrawals`/`apply_withdrawals` actually run. In that window, the builder's balance can be reduced by other operations (other builder-exit withdrawals, other queued pending withdrawals for the same builder being processed earlier in the same call due to per-block caps and ordering) — nothing re-checks that the balance is still sufficient at withdrawal-application time.

### Impact Explanation
This breaks the "Gwei created or destroyed" invariant at the Critical/High boundary: the execution layer will credit `withdrawal.amount` (as committed by the beacon block) to `fee_recipient`, while the consensus layer accounting for the builder only reflects a capped debit. Total supply invariance between CL and EL is broken by an amount that a builder (or a builder's own actions across multiple blocks) can effectively steer — the excess is minted for free on the EL side. This is the same shape of bug as the `_claimRewardsOnBehalf()` finding (transfer/pay the uncapped amount while internal accounting caps the deduction), but here the mismatch propagates into the execution-layer payload, which the protocol treats as authoritative and un-vetoable once committed.

### Likelihood Explanation
The spec's own test suite (`test_builder_withdrawal_insufficient_balance`, `test_builder_withdrawal_insufficient_balance_realistic_bounds`, `test_all_builder_withdrawals_zero_balance`) already constructs exactly this state — a `BuilderPendingWithdrawal.amount` exceeding the builder's current balance — and explicitly asserts the withdrawal is still emitted at the full requested amount while the balance deduction is capped, confirming this is a reachable, non-theoretical state that the spec accepts as valid input/output for `process_withdrawals`.

### Recommendation
Cap the `amount` field of the `Withdrawal` object itself (not just the internal balance deduction) to `min(requested_amount, builder.balance)` inside `get_builder_withdrawals`, so the payload committed to the execution layer never promises more Gwei than the CL can actually back out of the builder's balance. Equivalently, ensure `apply_withdrawals` and `get_builder_withdrawals` agree on the same effective amount, so the invariant `EL_credited_amount == CL_debited_amount` always holds for builder withdrawals.

### Proof of Concept
1. A builder submits a bid with `bid.value = V` that it can currently cover (`can_builder_cover_bid` passes), producing a `BuilderPendingPayment` with `withdrawal.amount = V`. [5](#0-4) 
2. The payment queue rotates for one or more epochs (e.g., missed slots) before quorum is reached and `process_builder_pending_payments` moves it into `state.builder_pending_withdrawals` with `amount = V` unchanged. [6](#0-5) 
3. In the interim, the builder's `balance` is reduced below `V` (e.g., a builder-exit sweep withdrawal, `BuilderExitRequest`, or another pending withdrawal for the same builder is applied first).
4. When `process_withdrawals` finally runs, `get_builder_withdrawals` emits `Withdrawal(amount=V, address=fee_recipient)` into `payload_expected_withdrawals` — this is what the execution layer will credit `fee_recipient` with in full. [7](#0-6) 
5. `apply_withdrawals` only debits `min(V, builder.balance)` — say the builder's balance is now `B < V` — from `state.builders[i].balance`, leaving it at `0` rather than negative, per the test `test_builder_withdrawal_insufficient_balance`. [3](#0-2) [4](#0-3) 
6. Result: `fee_recipient` receives `V` Gwei on the execution layer, but only `B < V` Gwei was ever removed from any CL-tracked balance — `V - B` Gwei has been created with no corresponding debit anywhere in the system.

Note: I could not fully trace every code path by which a builder's balance could concretely drop below a previously-queued withdrawal amount (e.g., interactions with `BuilderExitRequest` processing and multiple concurrent pending withdrawals for the same builder) within the available index; a Devin session with full repository access would be needed to enumerate all such paths exhaustively and confirm there is no compensating balance re-check elsewhere in the codebase.

### Citations

**File:** specs/gloas/beacon-chain.md (L1664-1677)
```markdown
def process_builder_pending_payments(state: BeaconState) -> None:
    """
    Processes the builder pending payments from the previous epoch.
    """
    quorum = get_builder_payment_quorum_threshold(state)
    for payment in state.builder_pending_payments[:SLOTS_PER_EPOCH]:
        if payment.weight >= quorum:
            state.builder_pending_withdrawals.append(payment.withdrawal)

    old_payments = state.builder_pending_payments[SLOTS_PER_EPOCH:]
    state.builder_pending_payments[:SLOTS_PER_EPOCH] = old_payments
    new_payments = [BuilderPendingPayment.empty() for _ in range(SLOTS_PER_EPOCH)]
    state.builder_pending_payments[SLOTS_PER_EPOCH:] = new_payments
```
```

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

**File:** specs/gloas/beacon-chain.md (L1923-1931)
```markdown
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

**File:** specs/gloas/beacon-chain.md (L1969-1976)
```markdown
*Note*: This is modified to only take the `state` as parameter. Withdrawals are
deterministic given the beacon state, any execution payload that has the
corresponding block as parent beacon block is required to honor these
withdrawals in the execution layer. `process_withdrawals` must be called after
`process_parent_execution_payload` (which updates `state.latest_block_hash`) and
before `process_execution_payload_bid` as the latter function affects validator
balances.

```

**File:** specs/gloas/beacon-chain.md (L2124-2137)
```markdown
    # Record the pending payment if there is some payment
    if amount > 0:
        pending_payment = BuilderPendingPayment(
            weight=Gwei(0),
            withdrawal=BuilderPendingWithdrawal(
                fee_recipient=bid.fee_recipient,
                amount=amount,
                builder_index=builder_index,
            ),
            proposer_index=get_beacon_proposer_index(state),
        )
        state.builder_pending_payments[SLOTS_PER_EPOCH + bid.slot % SLOTS_PER_EPOCH] = (
            pending_payment
        )
```

**File:** tests/core/pyspec/eth_consensus_specs/test/gloas/block_processing/test_process_withdrawals.py (L116-156)
```python
def test_builder_withdrawal_insufficient_balance(spec, state):
    """
    Test builder withdrawal with insufficient balance.

    Input State Configured:
        - state.builders[0]: Builder exists with only 1 ETH balance
        - builder_pending_withdrawals: Contains 1 entry requesting 5 ETH
        - builders[0].balance: 1 ETH (insufficient for requested 5 ETH)

    Output State Verified:
        - payload_expected_withdrawals: Contains 1 withdrawal
        - withdrawal.amount: 5 ETH (requested amount)
        - builders[0].balance: 0 (deduction capped to available balance)
        - builder_pending_withdrawals: Reduced by 1 (processed even if capped)
        - next_withdrawal_index: Incremented by 1
    """
    builder_index = 0
    withdrawal_amount = spec.Gwei(5_000_000_000)
    available_balance = spec.Gwei(1_000_000_000)

    prepare_process_withdrawals(
        spec,
        state,
        builder_indices=[builder_index],
        builder_withdrawal_amounts={builder_index: withdrawal_amount},
        builder_balances={builder_index: available_balance},
    )

    pre_state = state.copy()
    yield from run_gloas_withdrawals_processing(spec, state)

    assert_process_withdrawals(
        spec,
        state,
        pre_state,
        withdrawal_count=1,
        builder_balances={builder_index: 0},
        builder_pending_delta=-1,
        withdrawal_index_delta=1,
        withdrawal_amounts_builders={builder_index: withdrawal_amount},
    )
```
