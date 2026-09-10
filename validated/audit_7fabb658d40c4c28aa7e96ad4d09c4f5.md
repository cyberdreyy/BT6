### Title
Builder pending withdrawal amount is minted uncapped on the EL while the CL only deducts the capped balance - ([File: specs/gloas/beacon-chain.md])

### Summary
`get_builder_withdrawals` commits, in `payload_expected_withdrawals`, the full requested `withdrawal.amount` from `state.builder_pending_withdrawals`, without capping it to the builder's current balance. `apply_withdrawals` then only decreases the builder's on-chain balance by `min(withdrawal.amount, builder_balance)`. Since the execution layer is required to honor the committed (uncapped) withdrawal amount, this creates Gwei that is not backed by any CL balance decrease whenever a pending withdrawal's amount exceeds the builder's balance at processing time.

### Finding Description
`get_builder_withdrawals` builds each `Withdrawal` using the raw, uncapped `amount` field of the queued `BuilderPendingWithdrawal`: [1](#0-0) 

This withdrawal is placed into `payload_expected_withdrawals` via `get_expected_withdrawals`/`update_payload_expected_withdrawals`, which the spec's own commentary states must be honored by the execution layer exactly: "any execution payload that has the corresponding block as parent beacon block is required to honor these withdrawals in the execution layer" [2](#0-1) .

However, `apply_withdrawals` caps the actual CL-side deduction to the builder's current balance: [3](#0-2) 

`min(withdrawal.amount, builder_balance)` is used only for the *deduction*, not for the *committed* `withdrawal.amount` that the EL will pay out to `withdrawal.fee_recipient`. The test suite explicitly documents and locks in this exact discrepancy: a builder with 1 ETH balance and a pending withdrawal requesting 5 ETH produces a withdrawal record with `amount = 5 ETH` (the full requested amount) while the builder's CL balance is only decreased by 1 ETH (capped to available balance): [4](#0-3) 

Because a `BuilderPendingWithdrawal.amount` is fixed at request time (see the "Requested amount. Actual = min(amount, builder.balance)" note in the input-space report) while the builder's balance can shrink afterward (e.g. from subsequent pending withdrawals, `process_builder_pending_payments`, or additional withdrawal requests processed earlier in the same sweep), it is entirely possible — under normal, honest state evolution, with no adversarial peer or client bug — for `withdrawal.amount` to exceed `builder.balance` by the time `apply_withdrawals` runs.

This breaks the equality that every Gwei paid out via an execution-layer withdrawal must be backed by an equal decrease somewhere in the beacon state: the EL mints/credits the full `withdrawal.amount` to the fee recipient, but the CL only removes `min(withdrawal.amount, builder_balance)` from `state.builders[builder_index].balance`. The difference is Gwei paid to a (non-owner) address with no corresponding balance decrease anywhere in the beacon state — a real net-supply inflation, not merely a documented/accepted tradeoff (unlike the validator-sweep saturation case, which the spec explicitly discusses and accepts as a known, bounded transient asymmetry at lines 1977-1989; here the discrepancy is unbounded and per-request, since `min()` is applied only to the deduction, not to the committed withdrawal amount).

### Impact Explanation
This is a Critical-class issue under the stated rubric ("Gwei created ... paid to a non-owner", and "a payload executed or paid that the block did not commit to" — inverted here as the block commits to paying more than it actually deducts). Every occurrence mints `withdrawal.amount - min(withdrawal.amount, builder_balance)` Gwei out of thin air on the execution layer, paid to the builder's designated `fee_recipient`, while the beacon state's builder balance is only reduced by the capped amount. This directly increases total supply without any corresponding stake decrease, breaking the fundamental Gwei-conservation invariant of the protocol.

### Likelihood Explanation
No adversarial coordination, malicious peer, or client bug is required. Any honest sequence of events where a builder's balance drops (via other withdrawals processed earlier in the same call, via `process_builder_pending_payments` penalties, or simply multiple queued pending withdrawals for the same builder exceeding its balance) after a `BuilderPendingWithdrawal` was queued but before it is processed triggers this exact code path, as confirmed directly by the existing spec test `test_builder_withdrawal_insufficient_balance` and `test_all_builder_withdrawals_zero_balance`.

### Recommendation
Cap the `amount` written into the `Withdrawal` record itself (used for both `payload_expected_withdrawals` and the EL payout commitment) to `min(withdrawal.amount, builder.balance)` inside `get_builder_withdrawals`, mirroring the cap already applied for the balance deduction in `apply_withdrawals`, so the amount committed to the execution layer always matches the amount actually removed from the builder's stake.

### Proof of Concept
1. Builder `B` has `balance = 1 ETH`.
2. A `BuilderPendingWithdrawal` for `B` with `amount = 5 ETH` is queued (legitimately possible since the requested amount is not bounded to current balance at request time).
3. At the next `process_withdrawals` call, `get_builder_withdrawals` emits `Withdrawal(validator_index=B, amount=5 ETH, address=fee_recipient)` into `payload_expected_withdrawals` [5](#0-4) .
4. `apply_withdrawals` deducts only `min(5 ETH, 1 ETH) = 1 ETH` from `state.builders[B].balance`, leaving it at 0 [6](#0-5) .
5. The execution layer, honoring the committed `payload_expected_withdrawals`, credits `fee_recipient` with the full 5 ETH.
6. Net effect: 4 ETH created with no corresponding balance decrease anywhere in the beacon state — exactly as demonstrated by the spec's own test `test_builder_withdrawal_insufficient_balance` [4](#0-3) .

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
