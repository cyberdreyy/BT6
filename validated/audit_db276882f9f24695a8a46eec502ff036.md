## Title
Builder withdrawal creates unbacked Gwei when `withdrawal.amount` exceeds `builder.balance` - ([File: specs/gloas/beacon-chain.md])

## Summary
`get_builder_withdrawals` emits a `Withdrawal` object whose `amount` is the *requested* `BuilderPendingWithdrawal.amount`, unconditionally, without capping it to the builder's actual balance. That `Withdrawal` is placed into `state.payload_expected_withdrawals`, which the execution layer is contractually required to honor by crediting `withdrawal.amount` to `withdrawal.address`. Meanwhile `apply_withdrawals` only debits `min(withdrawal.amount, builder_balance)` from `state.builders[builder_index].balance`. If the requested amount exceeds the builder's balance, the CL deducts less than what the EL actually pays out — this is the same "recorded amount vs. actually moved amount" mismatch as the deflationary-token bug class in the referenced report, but here it manufactures unbacked Gwei instead of losing funds.

## Finding Description
`get_builder_withdrawals` in `specs/gloas/beacon-chain.md` builds withdrawal entries directly from the pending withdrawal's `amount` field with no balance check: [1](#0-0) 

That same uncapped `amount` is what gets executed by the EL: `state.payload_expected_withdrawals` is defined to be exactly what the EL must honor per withdrawal (see the spec note on `process_withdrawals`, "any execution payload that has the corresponding block as parent beacon block is required to honor these withdrawals in the execution layer"). [2](#0-1) 

However, `apply_withdrawals` deducts only the capped amount from the CL-side builder balance: [3](#0-2) 

So for a builder with balance `B` and a pending withdrawal requesting `A > B`:
- Before: `builder.balance = B`.
- After `process_withdrawals`: `builder.balance = 0` (deducted by `min(A,B) = B`), but the `Withdrawal` object recorded in `payload_expected_withdrawals` (and thus committed to and executed on the EL) has `amount = A`.
- The EL will credit `A` Gwei to `fee_recipient`, while the CL only ever removed `B` Gwei from the builder. The difference `A - B` is Gwei created out of nothing and paid to a non-owner (the fee recipient), breaking the "Gwei created/destroyed/paid to a non-owner" equality directly.

This is confirmed by the repo's own test suite, which explicitly documents and asserts this exact behavior as the expected outcome rather than flagging it as an error: [4](#0-3) [5](#0-4) 

The AI-generated report on `process_withdrawals` also documents this as an accepted design property ("Actual = `min(amount, builder.balance)`"), i.e. builder pending withdrawals can be created with amounts that are only realized up to available balance: [6](#0-5) 

Whether `A > B` is actually reachable depends on how `builder_pending_withdrawals`/`builder_pending_payments` entries are created. `process_execution_payload_bid` requires `can_builder_cover_bid` to hold when the bid is accepted, and multiple pending payments/withdrawals for the same builder can accumulate (see `test_process_execution_payload_bid_sufficient_balance_with_pending_payments`, which explicitly sums existing pending amounts against balance): [7](#0-6) 
This check happens only at bid-acceptance time, against the balance *at that time*, including then-current pending payments/withdrawals. If the builder's balance is legitimately reduced afterward via a `builder_exit` withdrawal (builder sweep withdraws full balance, or another pending withdrawal is processed first in the same `payload_expected_withdrawals` list — builder pending withdrawals are processed strictly before the builder sweep and can stack up to `MAX_WITHDRAWALS_PER_PAYLOAD - 1` in one payload), a subsequent pending withdrawal's committed amount can end up exceeding the then-remaining balance, triggering the capped-deduction/uncapped-payment mismatch described above.

## Impact Explanation
This falls squarely under Critical impact per the stated criteria: "Gwei created, destroyed, or paid to a non-owner" and "a payload executed or paid that the block did not commit to accurately" — here, the block commits the CL state (`payload_expected_withdrawals`) to a value the EL will pay out, but the CL's own balance accounting does not match that committed value, creating an inflationary supply mismatch between CL bookkeeping and EL execution.

## Likelihood Explanation
Reachability depends on whether the protocol can validly produce a state where a `builder_pending_withdrawals` entry's `amount` exceeds the builder's balance at processing time (multiple stacked pending withdrawals for the same builder, or balance reduced by an intervening builder-sweep/exit before the pending withdrawal is processed). The `can_builder_cover_bid` check only gates bid acceptance time, not the eventual processing time of `process_withdrawals`, and the test suite explicitly exercises and accepts the insufficient-balance case as valid, non-error behavior, suggesting it is a reachable, accepted state rather than a hypothetical one. However, I could not fully trace every code path that populates `builder_pending_withdrawals` with amounts systematically decoupled from balance sufficiency (e.g., whether `builder_exit`/withdrawal-request processing can race with pending bid settlements to produce `A > B`), so likelihood of a concrete on-chain trigger is not fully proven end-to-end from this analysis alone.

## Recommendation
In `apply_withdrawals` (specs/gloas/beacon-chain.md), the `Withdrawal.amount` placed into `payload_expected_withdrawals` for builder withdrawals should be capped to `min(requested_amount, builder.balance)` at the point `get_builder_withdrawals` constructs the withdrawal (mirroring how `get_pending_partial_withdrawals` already caps `withdrawal_amount = min(balance - MIN_ACTIVATION_BALANCE, withdrawal.amount)` for validators), so the amount committed to the EL always equals the amount actually deducted from CL state.

## Proof of Concept
1. Register builder `B` with `balance = 1 ETH`.
2. Get two pending builder payments/withdrawals queued for `B` whose combined `amount` requested totals `5 ETH` (e.g., via two accepted bids over separate slots, each individually passing `can_builder_cover_bid` against balance at bid time, before either is settled) — or use the existing test harness value directly, as in the repo's own test: [8](#0-7) 
3. Run `process_withdrawals(state)`. Observe:
   - `state.payload_expected_withdrawals[0].amount == 5 ETH` (committed to EL).
   - `state.builders[0].balance == 0` (only 1 ETH actually deducted).
4. The EL, per protocol rules, must credit `5 ETH` to `fee_recipient`, while only `1 ETH` was ever removed from CL-tracked builder balance — 4 ETH is created and paid to a non-owner.

### Citations

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

**File:** tests/core/pyspec/eth_consensus_specs/test/gloas/block_processing/test_process_withdrawals.py (L159-206)
```python
@with_gloas_and_later
@spec_state_test
def test_builder_withdrawal_insufficient_balance_realistic_bounds(spec, state):
    """
    Test builder withdrawal with insufficient balance using realistic bounds.

    This test uses MIN_DEPOSIT_AMOUNT-based values to test edge cases with
    realistic builder balances (builders need MIN_DEPOSIT_AMOUNT to be active).

    Input State Configured:
        - state.builders[0]: Builder with balance = MIN_DEPOSIT_AMOUNT + 122 Gwei
        - builder_pending_withdrawals: Contains 1 entry requesting MIN_DEPOSIT_AMOUNT + 123 Gwei
        - builders[0].balance: Insufficient by exactly 1 Gwei

    Output State Verified:
        - payload_expected_withdrawals: Contains 1 withdrawal
        - withdrawal.amount: MIN_DEPOSIT_AMOUNT + 123 Gwei (requested amount)
        - builders[0].balance: 0 (deduction capped to available balance)
        - builder_pending_withdrawals: Reduced by 1 (processed even if capped)
        - next_withdrawal_index: Incremented by 1
    """
    builder_index = 0
    withdrawal_amount = spec.MIN_DEPOSIT_AMOUNT + spec.Gwei(123)
    available_balance = spec.MIN_DEPOSIT_AMOUNT + spec.Gwei(122)

    assert withdrawal_amount > available_balance, "Test requires insufficient balance"

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

**File:** tests/core/pyspec/eth_consensus_specs/test/gloas/block_processing/test_process_withdrawals.md (L34-34)
```markdown
| (in `builder_pending_withdrawals`) `.amount`             | `Gwei`                                                              | `Uint64`. Requested amount. Actual = `min(amount, builder.balance)`.                                                                                                 | Withdrawal amount                       |
```

**File:** tests/core/pyspec/eth_consensus_specs/test/gloas/block_processing/test_process_execution_payload_bid.py (L426-497)
```python
@with_gloas_and_later
@spec_state_test
def test_process_execution_payload_bid_sufficient_balance_with_pending_payments(spec, state):
    """
    Test builder with sufficient balance for both bid and existing pending payments
    """
    next_epoch_with_full_participation(spec, state)
    next_epoch_with_full_participation(spec, state)
    next_epoch_with_full_participation(spec, state)
    next_epoch_with_full_participation(spec, state)
    assert state.finalized_checkpoint.epoch == 2

    block, builder_index = prepare_block_with_non_proposer_builder(spec, state)
    assert spec.is_active_builder(state, builder_index) is True

    # Set up scenario: balance=2000 ETH, bid=600, existing_pending=500, min_activation=32ETH
    # Total needed: 600 + 500 + 32000000000 = ~32.0011 ETH < 2000 ETH (should pass)
    balance = spec.Gwei(2000000000000)  # 2000 ETH
    bid_amount = spec.Gwei(600)
    existing_pending = spec.Gwei(500)

    state.builders[builder_index].balance = balance

    # Create existing pending payment for this builder
    slot_index = 5  # Some slot in first epoch
    state.builder_pending_payments[slot_index] = spec.BuilderPendingPayment(
        weight=spec.Gwei(0),
        withdrawal=spec.BuilderPendingWithdrawal(
            fee_recipient=spec.ExecutionAddress(),
            amount=existing_pending,
            builder_index=builder_index,
        ),
    )

    pre_balance = state.builders[builder_index].balance
    pre_pending_payments_len = len(
        [p for p in state.builder_pending_payments if p.withdrawal.amount > 0]
    )

    # Create bid with this non-proposer builder
    signed_bid = prepare_signed_execution_payload_bid(
        spec,
        state,
        builder_index=builder_index,
        value=bid_amount,
        slot=block.slot,
        parent_block_root=block.parent_root,
    )

    block.body.signed_execution_payload_bid = signed_bid

    yield from run_execution_payload_bid_processing(spec, state, block)

    # Verify state updates
    assert state.latest_execution_payload_bid == signed_bid.message

    # Verify builder balance is still the same (payment is pending)
    assert state.builders[builder_index].balance == pre_balance

    # Verify new pending payment was recorded
    slot_index_new = spec.SLOTS_PER_EPOCH + (signed_bid.message.slot % spec.SLOTS_PER_EPOCH)
    pending_payment = state.builder_pending_payments[slot_index_new]
    assert pending_payment.withdrawal.amount == bid_amount
    assert pending_payment.withdrawal.builder_index == builder_index
    assert pending_payment.weight == 0

    # Verify pending payments count increased by 1 (now we have 2 total)
    post_pending_payments_len = len(
        [p for p in state.builder_pending_payments if p.withdrawal.amount > 0]
    )
    assert post_pending_payments_len == pre_pending_payments_len + 1

```
