### Title
Builder Pending Withdrawals Commit Uncapped Amounts to the Execution Layer, Allowing Gwei to Be Minted Without a Matching CL Balance Deduction - (File: specs/gloas/beacon-chain.md)

### Summary
`get_builder_withdrawals` unconditionally forwards the *requested* `BuilderPendingWithdrawal.amount` into the `Withdrawal` object that the execution layer is contractually bound to honor, while `apply_withdrawals` only debits `min(withdrawal.amount, builder_balance)` from the CL-tracked `state.builders[i].balance`. Unlike the analogous validator-withdrawal helpers, no function caps the committed amount to the builder's actual, available balance before it is queued for the EL. This breaks Gwei conservation: the EL can mint/pay more ETH to a `fee_recipient` than was ever debited from any account on the consensus layer.

### Finding Description
Compare the two withdrawal-amount computation paths in the same spec:

- Validator partial withdrawals cap the committed amount at computation time: [1](#0-0) 

- Builder pending withdrawals do **not** cap the amount; the full requested amount is copied verbatim into the committed `Withdrawal`: [2](#0-1) 

The only place a "cap" is applied is later, when the balance is actually decremented — but by then the withdrawal amount has already been placed into `payload_expected_withdrawals`, which is what the execution layer is obligated to pay out: [3](#0-2) 

Crucially, `apply_withdrawals` re-reads `state.builders[builder_index].balance` on each loop iteration. If the same builder has **multiple** `BuilderPendingWithdrawal` entries queued for the same `process_withdrawals` call (e.g. multiple bids from that builder settled together via `process_builder_pending_payments`, or one entry deferred via the "evicted" branch of `apply_parent_execution_payload`), only the first entry actually reduces the CL-tracked balance; every subsequent entry, still carrying its own full uncapped `amount`, is added unchanged to `payload_expected_withdrawals`: [4](#0-3) [5](#0-4) 

Each such entry originates from `parent_bid.value`, a value the builder itself chose when submitting its bid: [6](#0-5) 

Because bid submission does not itself debit the builder's balance (the debit only happens later at withdrawal-settlement time), a builder can win several slots in the same processing window with bids each close to its full balance. When these settle together, `get_builder_withdrawals` emits several `Withdrawal` entries whose amounts sum to a multiple of the builder's actual balance, but `apply_withdrawals`'s per-iteration `min()` only ever debits the balance once (down to zero) — the remaining entries are capped to a `min(amount, 0) = 0` CL debit yet still carry their full, uncapped `amount` in the object handed to the execution layer.

The spec authors are aware that CL-side saturation combined with an EL that "mints the full committed amount regardless" produces "net supply inflation," and explicitly designed around this for ordinary validator withdrawals by applying deductions immediately and (per the note) relying on the fact that `get_validators_sweep_withdrawals`/`get_pending_partial_withdrawals` already track cumulative prior withdrawals via `get_balance_after_withdrawals`: [7](#0-6) 

The builder-pending-withdrawal path has no equivalent accumulator (no `get_balance_after_withdrawals`-style cross-withdrawal tracking is used in `get_builder_withdrawals`), so the very inflation the authors flagged as unacceptable for validators is left unguarded for builders.

The test suite even documents the uncapped-commitment behavior as expected, confirming this is not a transcription error but the actual spec behavior: [8](#0-7) 

### Impact Explanation
This breaks the equality "Gwei created, destroyed, or paid to a non-owner": the execution layer's `Withdrawal.amount` (the value it will credit to `address`) is not bound to equal the Gwei actually removed from `state.builders[i].balance` on the consensus layer. A builder can arrange for several of its own pending withdrawals to be settled together, causing the total ETH minted on the EL side to exceed what was ever backed by its CL balance — net supply inflation paid to an address the builder controls (`fee_recipient`/`execution_address`), at the expense of the protocol's total-supply invariant that every other validator's balance implicitly depends on. This is a Critical-class impact per the rubric ("Gwei created ... or paid to a non-owner").

### Likelihood Explanation
No coalition, client bug, or malicious peer is required — only a single builder acting alone, choosing its own bid values and bidding across multiple slots that settle in the same epoch/payment-index window, which is ordinary, permitted builder behavior (winning multiple slots is a function of the builder market, not a special privilege). The bug is directly reachable through the documented spec functions (`apply_parent_execution_payload` → `settle_builder_payment`/`process_builder_pending_payments` → `get_builder_withdrawals` → `apply_withdrawals`), all in-scope `.md` pseudocode.

### Recommendation
Cap the committed withdrawal amount at the point it is generated, mirroring what `get_pending_partial_withdrawals` already does for validators: track a running "amount already committed against this builder in the current batch" (e.g. via a `get_balance_after_withdrawals`-style accumulator keyed by `builder_index`) inside `get_builder_withdrawals`, and set `amount = min(withdrawal.amount, remaining_builder_balance)` before appending to the output list, rather than deferring the cap to `apply_withdrawals`.

### Proof of Concept
1. Builder `B` has `balance = X`.
2. `B` wins the block-building slot for two separate slots that settle in the same `process_builder_pending_payments`/`process_parent_execution_payload` window (or one settles via the epoch-batch path and another via the "evicted" `parent_bid.value > 0` branch), submitting a bid of value `X` in each.
3. Both settlements append a `BuilderPendingWithdrawal(amount=X, builder_index=B, ...)` to `state.builder_pending_withdrawals` (per `settle_builder_payment` / `apply_parent_execution_payload`, specs/gloas/beacon-chain.md L1518-1527 and L1761-1770).
4. In the next `process_withdrawals` call, `get_builder_withdrawals` (specs/gloas/beacon-chain.md L1802-1834) emits two `Withdrawal` objects, each with `amount=X`, into `payload_expected_withdrawals` — total committed to the EL: `2X`.
5. `apply_withdrawals` (specs/gloas/beacon-chain.md L1920-1932) processes them in order: first iteration debits `min(X, X) = X`, leaving `balance = 0`; second iteration debits `min(X, 0) = 0`.
6. Result: CL removed only `X` from `B`'s balance, but the execution layer is committed to paying out `2X` total to `B`'s fee recipients — `X` of Gwei created without any corresponding stake reduction, exactly the scenario documented (for a single-withdrawal case) in `test_builder_withdrawal_insufficient_balance_realistic_bounds` (tests/.../test_process_withdrawals.py L159-207), generalized to multiple simultaneous entries.

### Citations

**File:** specs/electra/beacon-chain.md (L1381-1393)
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
```

**File:** specs/gloas/beacon-chain.md (L1518-1527)
```markdown
#### New `settle_builder_payment`

```python
def settle_builder_payment(state: BeaconState, payment_index: Uint64) -> None:
    assert payment_index < len(state.builder_pending_payments)
    payment = state.builder_pending_payments[payment_index]
    if payment.withdrawal.amount > 0:
        state.builder_pending_withdrawals.append(payment.withdrawal)
    state.builder_pending_payments[payment_index] = BuilderPendingPayment.empty()
```
```

**File:** specs/gloas/beacon-chain.md (L1661-1677)
```markdown
#### New `process_builder_pending_payments`

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

**File:** specs/gloas/beacon-chain.md (L1754-1770)
```markdown
    # Settle the builder payment
    if parent_epoch == get_current_epoch(state):
        payment_index = SLOTS_PER_EPOCH + parent_slot % SLOTS_PER_EPOCH
        settle_builder_payment(state, payment_index)
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

**File:** specs/gloas/beacon-chain.md (L1802-1834)
```markdown
##### New `get_builder_withdrawals`

```python
def get_builder_withdrawals(
    state: BeaconState,
    withdrawal_index: WithdrawalIndex,
    prior_withdrawals: Sequence[Withdrawal],
) -> Tuple[Sequence[Withdrawal], WithdrawalIndex, Uint64]:
    withdrawals_limit = MAX_WITHDRAWALS_PER_PAYLOAD - 1
    assert len(prior_withdrawals) <= withdrawals_limit

    processed_count = Uint64(0)
    withdrawals: list[Withdrawal] = []
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

**File:** specs/gloas/beacon-chain.md (L1977-1989)
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

**File:** tests/core/pyspec/eth_consensus_specs/test/gloas/block_processing/test_process_withdrawals.py (L159-207)
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
