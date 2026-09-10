### Title
Builder withdrawal amount is not capped when queued, causing the execution layer to mint more Gwei than the beacon chain deducts from the builder's balance - (File: specs/gloas/beacon-chain.md)

### Summary
`get_builder_withdrawals` copies `withdrawal.amount` from `state.builder_pending_withdrawals` verbatim into the `Withdrawal` object that becomes part of `state.payload_expected_withdrawals`, which the execution layer is contractually bound to honor in full. `apply_withdrawals`, however, only deducts `min(withdrawal.amount, builder_balance)` from the builder's actual CL-side balance. When a queued withdrawal amount exceeds the builder's current balance, the EL mints the full requested amount to `fee_recipient` while the CL only burns the (smaller) available balance, creating Gwei that is not backed by any deducted stake.

### Finding Description
`process_execution_payload_bid` records a `BuilderPendingPayment`/`BuilderPendingWithdrawal` with `amount=bid.value` at bid time [1](#0-0) , gated only by `can_builder_cover_bid` at that specific moment [2](#0-1) . This queued amount is carried forward untouched through `settle_builder_payment` [3](#0-2)  into `state.builder_pending_withdrawals`.

Later, `get_builder_withdrawals` builds the `Withdrawal` record using the raw, un-capped `amount` field from the pending withdrawal: [4](#0-3) 

This `Withdrawal` becomes part of `state.payload_expected_withdrawals`, which per the spec's own documentation "any execution payload that has the corresponding block as parent beacon block is required to honor these withdrawals in the execution layer" — i.e. the EL mints the full `withdrawal.amount` to `fee_recipient` [5](#0-4) .

But when `apply_withdrawals` actually debits the builder, it caps the deduction to the currently available balance: [6](#0-5) 

Because the balance can shrink between the time the bid/payment is queued and the time the withdrawal is finally swept (e.g. via other pending withdrawals draining the same builder, or a builder's balance decreasing from an intervening exit sweep), `withdrawal.amount` (what the EL pays out) can legitimately exceed `builder_balance` (what the CL deducts). The spec's own note acknowledges this exact asymmetry for the validator-sweep saturation case ("since the execution layer mints the full committed amount regardless, any CL-side saturation creates a net supply inflation") [7](#0-6) , but the builder path has the identical unchecked-mismatch pattern: the queued/committed amount is never re-validated or capped against the builder's live balance before being placed in the `Withdrawal.amount` that the EL is bound to execute.

This is the direct analog of the external report's bug class: a value transfer (here, the CL→EL withdrawal commitment) is recorded/executed without checking whether the underlying balance can actually support it, so the flow (payload minting) proceeds to completion on the full accounted amount while the source-side ledger (`apply_withdrawals`) only reflects a partial, capped deduction. The mismatch is a Gwei-creation event: value is paid to `fee_recipient` (a non-owner beneficiary of the excess) that was never actually backed by a deduction from any account, breaking the invariant that CL balance decreases equal EL payouts.

### Impact Explanation
This breaks the equality "Gwei paid out by the execution layer must equal Gwei deducted on the consensus layer." It results in Gwei created (paid to `fee_recipient`) that is not sourced from any account's balance reduction — a supply-inflation / unbacked-payment bug, which the report's rules classify as Critical (Gwei created/paid to a non-owner beyond what was committed). The magnitude depends on how large a gap can arise between queue time and sweep time for a builder's balance, but any positive gap directly creates unbacked Gwei.

### Likelihood Explanation
Likelihood is Low-to-Medium: builders can only queue withdrawal amounts they could cover at bid time (checked by `can_builder_cover_bid`), but nothing re-validates the amount against the builder's balance at settlement time. A builder can trigger several overlapping payments/pending-withdrawals in the same epoch, or trigger an exit/sweep that drains balance between the bid and the deferred settlement (especially the "older than previous epoch" direct-append path), producing exactly the balance-insufficient condition that the spec's own test suite explicitly exercises (`test_builder_withdrawal_insufficient_balance`), confirming this is a reachable, spec-intended-but-unguarded state rather than a purely theoretical one.

### Recommendation
Before appending a `Withdrawal` in `get_builder_withdrawals` (or when queuing `BuilderPendingWithdrawal`), cap the recorded `amount` to `min(withdrawal.amount, builder.balance)` — i.e., apply the same capping used in `apply_withdrawals` before the amount is committed into `payload_expected_withdrawals`, so the EL-committed payout can never exceed what is actually deducted from the builder's balance. Alternatively, re-validate/clamp against current balance at the point the pending withdrawal is created (`settle_builder_payment`) so a shrinking balance cannot desynchronize the committed vs. deducted amounts.

### Proof of Concept
1. Builder `B` posts an execution payload bid with `value = V` that passes `can_builder_cover_bid` at bid time (balance ≥ V) [8](#0-7) ; this queues a `BuilderPendingPayment` with `withdrawal.amount = V`.
2. Before this payment settles (still pending in `builder_pending_payments`/`builder_pending_withdrawals`), builder `B`'s balance is reduced by other means (e.g. exit sweep or being slashed pathway, or by other pending withdrawals also queued against `B` consuming the balance first, as demonstrated by `test_builder_withdrawal_insufficient_balance` where balance = 1 ETH but requested withdrawal = 5 ETH).
3. `get_builder_withdrawals` copies `amount = V` unchanged into the `Withdrawal` placed into `state.payload_expected_withdrawals` [4](#0-3) , which the EL will pay in full to `fee_recipient`.
4. `apply_withdrawals` only deducts `min(V, current_balance)` < `V` from `state.builders[B].balance` [6](#0-5) .
5. Result: EL pays `V` Gwei to `fee_recipient`, CL only removes `current_balance` (< V) Gwei from `B`'s balance — the difference `V - current_balance` is Gwei paid out of nowhere, confirmed by the existing spec test `test_builder_withdrawal_insufficient_balance`, which explicitly asserts `withdrawal.amount == 5 ETH` (the full requested amount) while `builders[0].balance == 0` (capped deduction) [9](#0-8) .

### Citations

**File:** specs/gloas/beacon-chain.md (L1521-1527)
```markdown
def settle_builder_payment(state: BeaconState, payment_index: Uint64) -> None:
    assert payment_index < len(state.builder_pending_payments)
    payment = state.builder_pending_payments[payment_index]
    if payment.withdrawal.amount > 0:
        state.builder_pending_withdrawals.append(payment.withdrawal)
    state.builder_pending_payments[payment_index] = BuilderPendingPayment.empty()
```
```

**File:** specs/gloas/beacon-chain.md (L1821-1829)
```markdown
        builder_index = withdrawal.builder_index
        withdrawals.append(
            Withdrawal(
                index=withdrawal_index,
                validator_index=convert_builder_index_to_validator_index(builder_index),
                address=withdrawal.fee_recipient,
                amount=withdrawal.amount,
            )
        )
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

**File:** specs/gloas/beacon-chain.md (L1969-1990)
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

**File:** specs/gloas/beacon-chain.md (L2100-2104)
```markdown
        assert is_active_builder(state, builder_index)
        # Verify that the builder is a payload builder
        assert state.builders[builder_index].version == PAYLOAD_BUILDER_VERSION
        # Verify that the builder has funds to cover the bid
        assert can_builder_cover_bid(state, builder_index, amount)
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
