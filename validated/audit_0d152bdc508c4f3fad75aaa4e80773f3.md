### Title
Builder pending withdrawal commits the full requested amount to the execution payload while capping only the beacon-state balance deduction, creating Gwei out of thin air — (File: specs/gloas/beacon-chain.md)

### Summary
In the Gloas fork, `get_builder_withdrawals` builds each `Withdrawal` object (the record that is committed to, and paid out by, the execution layer) using the *raw requested* `withdrawal.amount` taken from `state.builder_pending_withdrawals`, without capping it to the builder's actual `balance`. `apply_withdrawals`, however, only *deducts* `min(withdrawal.amount, builder_balance)` from `state.builders[builder_index].balance`. When a builder's balance has fallen below the amount it queued for withdrawal, the execution layer is instructed to pay the full uncapped amount to `fee_recipient`, while the beacon state only removes the smaller, capped amount from the builder's stake. The difference is Gwei created without any corresponding decrease in stake — breaking the "Gwei created/destroyed/paid to non-owner" invariant. This is analogous to, but more severe than, the reported NFT bug: instead of merely erasing an owed-but-unpaid remainder, the mismatch here fabricates and pays out ETH that was never backed by any deducted balance, and the withdrawal queue entry is unconditionally deleted afterward regardless of the shortfall.

### Finding Description
`get_builder_withdrawals` iterates `state.builder_pending_withdrawals` and emits: [1](#0-0) 

Note that `amount=withdrawal.amount` is the value stored in the queue entry, not `min(withdrawal.amount, state.builders[builder_index].balance)`.

`apply_withdrawals` then deducts only the capped amount from the builder's balance: [2](#0-1) 

Contrast this with `get_builders_sweep_withdrawals`, which correctly commits `amount=builder.balance` (the *actual* balance) rather than an independently-tracked requested amount: [3](#0-2) 

This shows the spec's intended invariant — the amount committed in the `Withdrawal` (and thus paid by the EL) must never exceed the balance actually deducted in the CL. `get_builder_withdrawals` violates this invariant for the builder-pending-withdrawal path.

Finally, regardless of whether the amount was honored, the queue entry is unconditionally consumed: [4](#0-3) 

The repo's own test suite explicitly documents and asserts this exact mismatch as expected behavior: [5](#0-4) 

The test confirms: builder balance is 1 ETH, the queued withdrawal requests 5 ETH, and the resulting `payload_expected_withdrawals` entry has `amount = 5 ETH` (the requested amount) while `builders[0].balance` only decreases by 1 ETH (capped). The queue entry is still removed ("processed even if capped").

Root cause: the requested `amount` field of `BuilderPendingWithdrawal` is set at the time a builder bid payment reaches quorum (`process_builder_pending_payments`), potentially epochs before the withdrawal is actually swept out via `process_withdrawals`: [6](#0-5) 

Between payment-quorum time and withdrawal-processing time, the builder's balance can decrease for unrelated reasons (further withdrawals, other queued payments draining balance first, etc. — as demonstrated by `get_pending_balance_to_withdraw_for_builder` needing to sum multiple pending amounts against a single balance): [7](#0-6) 

So a builder can legitimately have several separate `builder_pending_withdrawals` entries whose amounts collectively exceed its current balance by the time they're processed, and each of them will unconditionally emit the full uncapped `amount` into the execution payload.

### Impact Explanation
This breaks a Critical equality: "Gwei created, destroyed or paid to a non-owner." When `builder.balance < withdrawal.amount`, the execution layer pays the fee_recipient the full requested amount, but the CL beacon state deducts only the builder's actual balance. The excess (`withdrawal.amount - builder.balance`) is Gwei minted with no corresponding stake reduction anywhere in the system, breaking the total-supply invariant the spec elsewhere explicitly cares about (see the note on why withdrawal deduction cannot be deferred, right above this code, which stresses total-supply consistency): [8](#0-7) 

### Likelihood Explanation
This is not a hypothetical/adversarial-only path — it is directly exercised and validated as expected behavior by the spec's own conformance tests (`test_builder_withdrawal_insufficient_balance`, `test_builder_withdrawal_insufficient_balance_realistic_bounds`, `test_all_builder_withdrawals_zero_balance`), meaning any spec-following client will reproduce it whenever a builder's balance drops below a previously-queued withdrawal amount before that entry is processed — a state reachable purely through normal builder payment/withdrawal queue mechanics, requiring no malicious peer or coalition.

### Recommendation
In `get_builder_withdrawals`, cap the emitted `Withdrawal.amount` to the builder's current balance (accounting for any prior withdrawals already queued in the same payload, similar to how `get_pending_partial_withdrawals` computes `balance` via `get_balance_after_withdrawals`), e.g.:
```python
actual_amount = min(withdrawal.amount, builder_balance_after_prior_withdrawals)
```
so the committed `Withdrawal.amount` never exceeds what `apply_withdrawals` actually deducts.

### Proof of Concept
1. A builder's payment reaches quorum in `process_builder_pending_payments`, queuing a `BuilderPendingWithdrawal` for amount `A` while `builder.balance >= A`.
2. Before this entry is swept in `process_withdrawals`, the builder's balance is reduced below `A` (e.g., another higher-priority builder withdrawal, or a bid coverage draw, consumes balance first — exactly as shown in `test_all_builder_withdrawals_zero_balance` / `test_builder_withdrawal_insufficient_balance`).
3. `get_builder_withdrawals` still emits `Withdrawal(amount=A, address=fee_recipient, ...)` into `payload_expected_withdrawals`.
4. `apply_withdrawals` deducts only `min(A, builder.balance)` (e.g. `1 ETH`) from `state.builders[builder_index].balance`, per [2](#0-1) .
5. The execution layer, honoring the committed `payload.withdrawals`, credits `fee_recipient` with the full `A` (`5 ETH` in the test), while the CL only ever removed `1 ETH` of builder stake — net Gwei creation of `A - builder.balance` (`4 ETH`), exactly as asserted by [9](#0-8) .

### Citations

**File:** specs/gloas/beacon-chain.md (L1154-1179)
```markdown
def get_pending_balance_to_withdraw_for_builder(
    state: BeaconState, builder_index: BuilderIndex
) -> Gwei:
    balance = Gwei(0)
    for withdrawal in state.builder_pending_withdrawals:
        if withdrawal.builder_index == builder_index:
            balance += withdrawal.amount
    for payment in state.builder_pending_payments:
        if payment.withdrawal.builder_index == builder_index:
            balance += payment.withdrawal.amount
    return balance
```

#### New `can_builder_cover_bid`

```python
def can_builder_cover_bid(
    state: BeaconState, builder_index: BuilderIndex, bid_amount: Gwei
) -> bool:
    builder_balance = state.builders[builder_index].balance
    pending_withdrawals_amount = get_pending_balance_to_withdraw_for_builder(state, builder_index)
    min_balance = MIN_DEPOSIT_AMOUNT + pending_withdrawals_amount
    if builder_balance < min_balance:
        return False
    return builder_balance - min_balance >= bid_amount
```
```

**File:** specs/gloas/beacon-chain.md (L1663-1677)
```markdown
```python
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

**File:** specs/gloas/beacon-chain.md (L1815-1831)
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
```

**File:** specs/gloas/beacon-chain.md (L1858-1867)
```markdown
        builder = state.builders[builder_index]
        if builder.withdrawable_epoch <= epoch and builder.balance > 0:
            withdrawals.append(
                Withdrawal(
                    index=withdrawal_index,
                    validator_index=convert_builder_index_to_validator_index(builder_index),
                    address=builder.execution_address,
                    amount=builder.balance,
                )
            )
```

**File:** specs/gloas/beacon-chain.md (L1922-1931)
```markdown
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

**File:** specs/gloas/beacon-chain.md (L1946-1951)
```markdown
def update_builder_pending_withdrawals(
    state: BeaconState, processed_builder_withdrawals_count: Uint64
) -> None:
    state.builder_pending_withdrawals = state.builder_pending_withdrawals[
        processed_builder_withdrawals_count:
    ]
```

**File:** specs/gloas/beacon-chain.md (L1971-1982)
```markdown
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
```

**File:** tests/core/pyspec/eth_consensus_specs/test/gloas/block_processing/test_process_withdrawals.py (L114-156)
```python
@with_gloas_and_later
@spec_state_test
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
