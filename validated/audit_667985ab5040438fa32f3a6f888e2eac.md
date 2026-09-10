### Title
Builder pending withdrawals commit an uncapped payload amount while the CL-side balance deduction is capped, minting unbacked Gwei - ([File: specs/gloas/beacon-chain.md])

### Summary
`get_builder_withdrawals` builds each `Withdrawal.amount` directly from the queued `BuilderPendingWithdrawal.amount`, with no check against the builder's current `balance`. `apply_withdrawals`, however, deducts only `min(withdrawal.amount, builder_balance)` from `state.builders[builder_index].balance`. The `Withdrawal` (with the full, uncapped amount) is what is placed into `state.payload_expected_withdrawals`, which the execution layer is required to honor by minting that exact amount to `fee_recipient`. If a builder's balance is insufficient at processing time, the EL mints and pays out more Gwei than was ever backed by the builder's stake, while the corresponding queue entry is unconditionally removed via `update_builder_pending_withdrawals` with no mechanism to reconcile or claw back the shortfall — the CL-visible "debt" simply vanishes once the entry is dequeued, exactly analogous to `nftInfo`/`unpaidRewards` being erased in the referenced report, except here the unbacked amount is actually paid out rather than merely un-refundable.

### Finding Description
- `get_builder_withdrawals` (specs/gloas/beacon-chain.md, ~lines 1802-1834) iterates `state.builder_pending_withdrawals` and appends `Withdrawal(..., amount=withdrawal.amount)` verbatim — it never clamps to `state.builders[builder_index].balance`. [1](#0-0) 

- `apply_withdrawals` (specs/gloas/beacon-chain.md, ~lines 1923-1931) applies the CL-side deduction with an explicit cap: `state.builders[builder_index].balance -= min(withdrawal.amount, builder_balance)`. [2](#0-1) 

- `update_builder_pending_withdrawals` then unconditionally slices off the processed entries from `state.builder_pending_withdrawals`, with no residual/postponement mechanism for any unfunded portion. [3](#0-2) 

By contrast, the analogous validator-side function `get_pending_partial_withdrawals` (electra) *does* cap the committed amount to the validator's actual excess balance: `withdrawal_amount = min(balance - MIN_ACTIVATION_BALANCE, withdrawal.amount)` before it is placed in the payload-committed withdrawal. [4](#0-3) 

Builders are non-validator actors whose balance can be depleted independently of the `builder_pending_withdrawals` queue (e.g., other bid payments, other queued withdrawals, or state transitions between when a `BuilderPendingWithdrawal` is enqueued via `process_builder_pending_payments` and when it is actually dequeued in `get_builder_withdrawals`), so the balance at enqueue-time is not guaranteed to still cover the amount at dequeue-time. [5](#0-4) 

This exact scenario is documented and accepted as expected behavior by the existing test suite, which explicitly asserts the payload withdrawal amount is the *full requested amount* even though the builder's on-chain balance is far smaller, and that the queue entry is fully dequeued regardless: [6](#0-5) [7](#0-6) 

The spec text itself acknowledges the broader architectural risk that CL-side saturation of a withdrawal deduction combined with an EL that "mints the full committed amount regardless" produces "a net supply inflation," in the note preceding `process_withdrawals`. [8](#0-7) 

### Impact Explanation
This breaks the equality "Gwei paid out on the execution layer must equal Gwei actually deducted from the paying party's balance on the consensus layer." Because `payload_expected_withdrawals` commits the EL to mint the full, uncapped `withdrawal.amount` regardless of the builder's true balance, a shortfall results in Gwei being created and paid to `fee_recipient` that was never backed by any staked balance — a direct violation of the total-supply invariant, and the created value is paid to a party (the withdrawal's fee recipient) that did not have a claim to it. This matches the Critical impact category: "Gwei created ... and paid to a non-owner," since honest execution-layer clients following the committed payload are forced to mint unbacked ETH.

### Likelihood Explanation
No malicious peer, coalition, or client bug is required — this is a pure spec-following-node interaction. It only requires that a builder's `balance` fall below the sum of its outstanding `builder_pending_withdrawals`/`builder_pending_payments` by the time `get_builder_withdrawals` runs, which the spec's own test suite demonstrates as a normal, exercised code path (`test_builder_withdrawal_insufficient_balance`). Since `can_builder_cover_bid` is only checked at bid-acceptance time and balance can be consumed by subsequently accepted bids/payments before earlier pending withdrawals are dequeued, the insufficiency condition is reachable through ordinary protocol operation, not an edge case requiring an attacker to break any cryptographic assumption.

### Recommendation
Cap the committed `Withdrawal.amount` in `get_builder_withdrawals` to `min(withdrawal.amount, state.builders[builder_index].balance)`, consistent with how `get_pending_partial_withdrawals` and `get_validators_sweep_withdrawals` cap validator withdrawal amounts to actual available balance before committing them to the payload. If a residual (unfunded) balance must still be tracked for the builder, define an explicit "unpaid" carry-over field (analogous to `unpaidRewards`) rather than silently discarding the difference when the entry is dequeued via `update_builder_pending_withdrawals`.

### Proof of Concept
Using the existing spec test as a concrete trace (specs/gloas/beacon-chain.md logic, test at tests/gloas/block_processing/test_process_withdrawals.py:114-156):
1. `state.builders[0].balance = 1_000_000_000` (1 ETH).
2. `state.builder_pending_withdrawals = [BuilderPendingWithdrawal(builder_index=0, fee_recipient=X, amount=5_000_000_000)]` (5 ETH requested).
3. `process_withdrawals(state)` runs:
   - `get_builder_withdrawals` produces `Withdrawal(validator_index=<builder 0>, address=X, amount=5_000_000_000)` — the full 5 ETH, uncapped.
   - This is placed into `state.payload_expected_withdrawals`, which the execution layer is bound to honor by minting 5 ETH to address `X`.
   - `apply_withdrawals` deducts `min(5_000_000_000, 1_000_000_000) = 1_000_000_000` from `state.builders[0].balance`, leaving it at 0.
   - `update_builder_pending_withdrawals` removes the entry entirely.
4. Net effect: CL state shows only 1 ETH was ever debited from builder 0, but the EL is committed to minting 5 ETH to `X` — 4 ETH of Gwei is created without any backing balance and paid to `X`, and there is no remaining record anywhere in state of this shortfall.

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

**File:** specs/gloas/beacon-chain.md (L1946-1952)
```markdown
def update_builder_pending_withdrawals(
    state: BeaconState, processed_builder_withdrawals_count: Uint64
) -> None:
    state.builder_pending_withdrawals = state.builder_pending_withdrawals[
        processed_builder_withdrawals_count:
    ]
```
```

**File:** specs/gloas/beacon-chain.md (L1977-1990)
```markdown
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

**File:** specs/electra/beacon-chain.md (L1381-1394)
```markdown
        validator_index = withdrawal.validator_index
        validator = state.validators[validator_index]
        balance = get_balance_after_withdrawals(state, validator_index, all_withdrawals)
        if is_eligible_for_partial_withdrawals(validator, balance):
            withdrawal_amount = min(balance - MIN_ACTIVATION_BALANCE, withdrawal.amount)
            withdrawals.append(
                Withdrawal(
                    index=withdrawal_index,
                    validator_index=validator_index,
                    address=ExecutionAddress(validator.withdrawal_credentials[12:]),
                    amount=withdrawal_amount,
                )
            )
            withdrawal_index += 1
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
