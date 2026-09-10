### Title
Uncapped `BuilderPendingWithdrawal.amount` committed to the execution layer while state deduction is capped, allowing Gwei to be paid to a non-owner without a matching balance decrease — (File: `specs/gloas/beacon-chain.md`)

### Summary
In the Gloas fork, a builder's queued payment amount (`BuilderPendingWithdrawal.amount`) is copied verbatim into the `Withdrawal.amount` field that is committed to the execution layer (`get_builder_withdrawals`), but the corresponding consensus-state balance deduction in `apply_withdrawals` is capped at `min(withdrawal.amount, builder_balance)`. If a builder's balance at settlement time is lower than the amount, the withdrawal record — which the execution layer is required to honor exactly — pays out more Gwei to `fee_recipient` than is actually removed from the builder's balance, creating Gwei out of nothing. This mirrors the reported H-03 pattern of a single field being treated inconsistently as "requested" and "actual/capped" value.

### Finding Description
`get_builder_withdrawals` (specs/gloas/beacon-chain.md:1802-1834) builds the `Withdrawal` sent to the execution layer using the raw, uncapped `withdrawal.amount` from `state.builder_pending_withdrawals`: [1](#0-0) 

That `Withdrawal` becomes part of `state.payload_expected_withdrawals`, which the spec explicitly states execution-layer nodes are required to honor: [2](#0-1) 

However, `apply_withdrawals` only deducts the *capped* amount from the builder's consensus-layer balance: [3](#0-2) 

The amount that ends up queued in `state.builder_pending_withdrawals` is normally guarded by a balance-sufficiency check performed at `process_execution_payload_bid` time (this is why tests such as `test_process_execution_payload_bid_sufficient_balance_with_pending_payments` sum bid + existing pending amounts + `MIN_ACTIVATION_BALANCE` against builder balance). But there is a fallback path in `apply_parent_execution_payload` that bypasses this check entirely: when a parent block's payment slot has already been evicted from `builder_pending_payments` (parent older than the previous epoch), the withdrawal is appended directly from the bid value with **no balance check at all**: [4](#0-3) 

This fallback is exercised by a documented spec scenario (missed-epoch settlement), confirming it is reachable in normal operation, not merely theoretical: [5](#0-4) 

Because builders "cannot be slashed" and their balance can be drawn down by other pending payments/withdrawals or exits in the intervening epochs (2+ epochs must pass for this fallback to trigger), a builder's balance at the time this fallback appends the withdrawal can legitimately be lower than the bid `value`. Once queued, `get_builder_withdrawals` copies that value uncapped into the committed `Withdrawal.amount`, while `apply_withdrawals` only deducts `min(amount, balance)` from `builder.balance`. The existing test suite explicitly validates and accepts this asymmetry as the current (unfixed) behavior: [6](#0-5) 

### Impact Explanation
This breaks the equality "Gwei paid out to a recipient == Gwei removed from the payer's balance." The execution layer is contractually bound to honor `payload_expected_withdrawals` exactly, so it will credit `fee_recipient` (an address the builder or the block does not need majority/coalition control over — just their own bid) with the full uncapped `amount`, while the beacon state only reduces the builder's balance by the smaller, capped amount. This is Gwei created from nothing and paid to a party the protocol did not actually debit — a critical-severity total-supply/accounting break as flagged by the spec's own commentary on why the deduction must be synchronous ("Deferring the deduction to the child's slot would break the total supply invariant"). [7](#0-6) 

### Likelihood Explanation
Triggering requires: (1) a builder submits a bid and its payment enters `builder_pending_payments`; (2) roughly 2+ epochs pass without that payment's payload being settled through the normal `settle_builder_payment` path, causing the payment slot to be evicted, which routes settlement through the unchecked fallback in `apply_parent_execution_payload`; and (3) the builder's balance has decreased below the bid value by that time (achievable via other pending payments/withdrawals draining the same balance, since builders cannot be slashed and there is no re-validation before this fallback append). All of these conditions are under a single builder's control (no coalition, no honest-validator compromise needed) and the missed-epoch settlement scenario is already exercised as a first-class spec test case, indicating it is a realistic, not merely theoretical, operating condition.

### Recommendation
Cap the amount recorded in the committed `Withdrawal` (in `get_builder_withdrawals`) to `min(withdrawal.amount, state.builders[builder_index].balance)` at the time the withdrawal is constructed, mirroring the capping already applied in `apply_withdrawals`, so the committed payment and the balance deduction always agree. Additionally, re-validate builder balance sufficiency in the `apply_parent_execution_payload` fallback branch (specs/gloas/beacon-chain.md:1758-1770) before enqueuing a `BuilderPendingWithdrawal`, rather than relying solely on the bid-time check.

### Proof of Concept
1. Builder `B` submits a bid with `value = V` while `balance_B >= V + existing_pending + MIN_ACTIVATION_BALANCE` (passes the check in `process_execution_payload_bid`); a `BuilderPendingPayment` is queued.
2. Before this payment's slot is settled via the normal path, 2+ epochs elapse without the corresponding parent payload being processed in time (e.g., due to missed proposals), causing the payment entry to rotate out of `builder_pending_payments` (`process_builder_pending_payments` at specs/gloas/beacon-chain.md:1664-1677).
3. In the meantime, other pending payments/withdrawals for `B` settle and reduce `balance_B` to `V' < V` (no slashing exists to prevent this, and no fresh check occurs before the fallback).
4. When the parent block is eventually processed, `apply_parent_execution_payload` hits the `elif parent_bid.value > 0` branch and appends `BuilderPendingWithdrawal(amount=V, ...)` directly, without checking `balance_B` (specs/gloas/beacon-chain.md:1761-1770).
5. `get_builder_withdrawals` later emits `Withdrawal(amount=V, address=fee_recipient, ...)` into `payload_expected_withdrawals` (specs/gloas/beacon-chain.md:1822-1829) — the execution layer must credit `fee_recipient` with `V`.
6. `apply_withdrawals` only deducts `min(V, balance_B) = V' < V` from `state.builders[B].balance` (specs/gloas/beacon-chain.md:1929).
7. Net effect: `fee_recipient` receives `V` Gwei on the execution layer, but only `V'` Gwei was removed from the consensus-layer builder balance — `V - V'` Gwei has been created and paid to `fee_recipient` with no corresponding debit anywhere in the beacon state.

### Citations

**File:** specs/gloas/beacon-chain.md (L1758-1770)
```markdown
    elif parent_epoch == get_previous_epoch(state):
        payment_index = parent_slot % SLOTS_PER_EPOCH
        settle_builder_payment(state, payment_index)
    elif parent_bid.value > 0:
        # Parent is older than the previous epoch, its payment entry has been
        # evicted from builder_pending_payments. Append the withdrawal directly.
        state.builder_pending_withdrawals.append(
            BuilderPendingWithdrawal(
                fee_recipient=parent_bid.fee_recipient,
                amount=parent_bid.value,
                builder_index=parent_bid.builder_index,
            )
        )
```

**File:** specs/gloas/beacon-chain.md (L1820-1830)
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
        withdrawal_index += 1
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

**File:** specs/gloas/beacon-chain.md (L1969-1980)
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
```

**File:** tests/core/pyspec/eth_consensus_specs/test/gloas/sanity/test_blocks.py (L834-845)
```python
    # Build Block 2 with 2+ epochs of missed slots. During the slot advancement,
    # process_builder_pending_payments runs at each epoch boundary:
    #   1st boundary: shifts payment from second half to first half
    #   2nd boundary: checks quorum on first half — weight 0 < quorum → evicted
    # When Block 2 is processed, parent is FULL so apply_parent_execution_payload
    # runs. Since parent_epoch is older than previous_epoch, payment_index is None.
    # The fix creates the withdrawal directly from the bid in this case.
    block_1_epoch = spec.compute_epoch_at_slot(block_1.slot)
    block_2_slot = spec.compute_start_slot_at_epoch(block_1_epoch + 2) + 1
    block_2 = build_empty_block(spec, state, slot=block_2_slot)
    block_2.body.signed_execution_payload_bid.message.parent_block_hash = block_hash
    signed_block_2 = state_transition_and_sign_block(spec, state, block_2)
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
