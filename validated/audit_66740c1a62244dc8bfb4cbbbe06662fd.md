This confirms the finding: `get_builder_withdrawals` in `specs/gloas/beacon-chain.md` builds the `Withdrawal.amount` field directly from `withdrawal.amount` (the requested amount), [1](#0-0)  while `apply_withdrawals` deducts only `min(withdrawal.amount, builder_balance)` from the builder's on-chain balance [2](#0-1) . This is confirmed by the test explicitly asserting a withdrawal amount of 5 ETH while the builder balance is only ever decreased to 0 from a 1 ETH balance [3](#0-2) .

### Title
Uncapped `Withdrawal.amount` in `get_builder_withdrawals` mints Gwei on the execution layer beyond the builder's actual balance deduction - (File: specs/gloas/beacon-chain.md)

### Summary
When a `BuilderPendingWithdrawal.amount` exceeds the requesting builder's current `balance`, `get_builder_withdrawals` places the full, uncapped requested `amount` into the `Withdrawal` object that is committed to `payload_expected_withdrawals` and ultimately into the execution payload's `withdrawals` list. The execution layer mints/credits the recipient the full `amount` field of each `Withdrawal` unconditionally (this is the entire point of withdrawals: EL trusts CL's committed amount). Meanwhile `apply_withdrawals` only decreases the builder's CL-side `balance` by `min(withdrawal.amount, builder_balance)`, i.e., it saturates the deduction at zero instead of matching the amount actually credited on the EL side.

### Finding Description
The relevant functions are:

```python
def get_builder_withdrawals(...):
    ...
    withdrawals.append(
        Withdrawal(
            index=withdrawal_index,
            validator_index=convert_builder_index_to_validator_index(builder_index),
            address=withdrawal.fee_recipient,
            amount=withdrawal.amount,   # <-- uncapped, requested amount
        )
    )
``` [4](#0-3) 

```python
def apply_withdrawals(state: BeaconState, withdrawals: Sequence[Withdrawal]) -> None:
    for withdrawal in withdrawals:
        if is_builder_index(withdrawal.validator_index):
            builder_index = convert_validator_index_to_builder_index(withdrawal.validator_index)
            builder_balance = state.builders[builder_index].balance
            state.builders[builder_index].balance -= min(withdrawal.amount, builder_balance)  # <-- capped deduction
        else:
            decrease_balance(state, withdrawal.validator_index, withdrawal.amount)
``` [2](#0-1) 

`process_withdrawals` asserts equality only on the withdrawals *list content* between what `get_expected_withdrawals` computes and what's committed to the payload — it never re-checks that each withdrawal's `amount` is actually backed by a corresponding balance decrease of the same magnitude:

```python
def process_withdrawals(state: BeaconState) -> None:
    if not is_parent_block_full(state):
        return
    expected = get_expected_withdrawals(state)
    apply_withdrawals(state, expected.withdrawals)
    update_next_withdrawal_index(state, expected.withdrawals)
    ...
```
(gloas variant, analogous to the Capella version) [5](#0-4) 

Because `payload_expected_withdrawals` is hash-tree-rooted and cross-checked against the execution payload's withdrawals commitment (`hash_tree_root(payload.withdrawals) == hash_tree_root(state.payload_expected_withdrawals)`) [6](#0-5) , the execution layer will mint/credit the full, uncapped `amount` to `fee_recipient`, while the CL's `builders[builder_index].balance` was only reduced to zero — a smaller amount. The test suite documents this exact behavior as intended ("deduction capped to available balance," "withdrawal.amount: 5 ETH (requested amount)") [7](#0-6)  and the input/output specification explicitly notes "Actual = `min(amount, builder.balance)`" for the CL-side balance decrease while the withdrawal's committed `amount` remains the full requested value [8](#0-7) .

This breaks the fundamental CL/EL supply equality: every Gwei credited by a withdrawal on the EL side must correspond to an equal Gwei debited on the CL side. Here, a builder (or any actor able to enqueue an overly large `BuilderPendingWithdrawal`, e.g. via `process_execution_payload_bid` recording a payment larger than the builder's balance at settlement time, or via the direct-append path in `apply_parent_execution_payload` when a payment entry is evicted) can have EL mint `amount` Gwei to `fee_recipient` while CL only ever debits up to `builder_balance` (which can be far less, even zero if already drained by a prior withdrawal in the same block).

### Impact Explanation
This is a Critical-severity issue: Gwei is created out of thin air and paid to a non-owner-controlled recipient (`fee_recipient`, which is builder-chosen and can differ from `execution_address`) [9](#0-8) . It inflates the total ETH supply on the execution layer without a corresponding consensus-layer debit, directly breaking the invariant that CL balance decreases must equal EL balance increases for withdrawals.

### Likelihood Explanation
The scenario is directly reachable in-protocol: a builder's bid amount recorded via `process_execution_payload_bid`/`settle_builder_payment` can exceed the builder's balance at the time `apply_withdrawals` runs, particularly since multiple pending payments/withdrawals for the same builder can be queued and processed in the same or nearby blocks (as shown by the test scenarios with existing pending payments/withdrawals) [10](#0-9) . No malicious peer/coalition is required — a single builder queuing/losing balance between bid-time and withdrawal-settlement time triggers this.

### Recommendation
`get_builder_withdrawals` (and any code path that constructs a `Withdrawal` for a builder) should cap the committed `Withdrawal.amount` to `min(withdrawal.amount, state.builders[builder_index].balance)` at the time the expected withdrawals list is computed, matching the capped amount that `apply_withdrawals` will actually deduct, so that the amount minted on the EL side always equals the amount debited on the CL side.

### Proof of Concept
1. Builder `B` accumulates balance of 1 ETH.
2. A `BuilderPendingWithdrawal` for `B` requests 5 ETH (e.g., because two separate payments/pending withdrawals of 3 ETH and 2 ETH each were valid individually against balance at queuing time, but balance dropped before settlement, or because a single pending payment simply exceeds current balance due to intervening deductions).
3. `get_builder_withdrawals` emits `Withdrawal(address=fee_recipient, amount=5 ETH)` uncapped, which becomes part of `payload_expected_withdrawals` and is committed into the execution payload.
4. `apply_withdrawals` deducts `min(5 ETH, 1 ETH) = 1 ETH` from `builders[B].balance`, leaving it at 0.
5. The execution layer credits `fee_recipient` the full 5 ETH per the committed withdrawal, while only 1 ETH was ever debited from the CL-tracked builder balance — 4 ETH is created without any offsetting debit, exactly matching the test's documented behavior [3](#0-2) .

### Citations

**File:** specs/gloas/beacon-chain.md (L1805-1834)
```markdown
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

**File:** tests/core/pyspec/eth_consensus_specs/test/gloas/block_processing/test_process_withdrawals.py:126 (L126-129)
```python
        - payload_expected_withdrawals: Contains 1 withdrawal
        - withdrawal.amount: 5 ETH (requested amount)
        - builders[0].balance: 0 (deduction capped to available balance)
        - builder_pending_withdrawals: Reduced by 1 (processed even if capped)
```

**File:** specs/capella/beacon-chain.md (L531-545)
```markdown
#### New `process_withdrawals`

```python
def process_withdrawals(state: BeaconState, payload: ExecutionPayload) -> None:
    # Get expected withdrawals
    expected = get_expected_withdrawals(state)
    assert list(payload.withdrawals) == expected.withdrawals

    # Apply expected withdrawals
    apply_withdrawals(state, expected.withdrawals)

    # Update withdrawals fields in the state
    update_next_withdrawal_index(state, expected.withdrawals)
    update_next_withdrawal_validator_index(state, expected.withdrawals)
```
```

**File:** specs/gloas/fork-choice.md (L684-688)
```markdown
    # Verify the execution payload is valid
    assert payload.slot_number == state.slot
    assert payload.parent_hash == state.latest_block_hash
    assert payload.timestamp == compute_time_at_slot(state, state.slot)
    assert hash_tree_root(payload.withdrawals) == hash_tree_root(state.payload_expected_withdrawals)
```

**File:** tests/core/pyspec/eth_consensus_specs/test/gloas/block_processing/test_process_withdrawals.py:116 (L116-156)
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

**File:** tests/core/pyspec/eth_consensus_specs/test/gloas/block_processing/test_process_withdrawals.md (L34-34)
```markdown
| (in `builder_pending_withdrawals`) `.amount`             | `Gwei`                                                              | `Uint64`. Requested amount. Actual = `min(amount, builder.balance)`.                                                                                                 | Withdrawal amount                       |
```

**File:** tests/core/pyspec/eth_consensus_specs/test/gloas/block_processing/test_process_withdrawals.py:696 (L696-723)
```python
@with_gloas_and_later
@spec_state_test
def test_builder_uses_fee_recipient_address(spec, state):
    """
    Builder withdrawal should use fee_recipient address from BuilderPendingWithdrawal.

    Input State Configured:
        - state.builders[0]: Builder exists with custom execution_address (0xab * 20)
        - builders[0].balance: Sufficient for withdrawal
        - builder_pending_withdrawals[0].fee_recipient: Custom address from builder

    Output State Verified:
        - payload_expected_withdrawals: Contains 1 builder withdrawal
        - payload_expected_withdrawals[0].address: Custom fee_recipient address (0xab * 20)
        - Note: Withdrawal uses fee_recipient from BuilderPendingWithdrawal
    """
    builder_index = 0
    custom_address = b"\xab" * 20
    withdrawal_amount = spec.MIN_ACTIVATION_BALANCE

    prepare_process_withdrawals(
        spec,
        state,
        builder_indices=[builder_index],
        builder_withdrawal_amounts={builder_index: withdrawal_amount},
        builder_balances={builder_index: withdrawal_amount + spec.MIN_DEPOSIT_AMOUNT},
        builder_execution_addresses={builder_index: custom_address},
    )
```

**File:** tests/core/pyspec/eth_consensus_specs/test/gloas/block_processing/test_process_execution_payload_bid.py:426 (L426-497)
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
