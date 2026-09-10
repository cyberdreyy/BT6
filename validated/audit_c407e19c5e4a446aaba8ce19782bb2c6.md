### Title
Builder withdrawal amount is not capped to available balance before being committed in the execution payload's withdrawal list, allowing Gwei to be created out of thin air - (File: specs/gloas/beacon-chain.md)

### Summary
`get_builder_withdrawals` builds the `Withdrawal` entries that are placed in `state.payload_expected_withdrawals`/`ExpectedWithdrawals` — the list the execution layer is required to honor by crediting the stated `amount` to the stated `address` — using the raw, uncapped `withdrawal.amount` taken from `state.builder_pending_withdrawals`. `apply_withdrawals`, which is the function that actually debits the CL-side builder balance, caps the deduction with `min(withdrawal.amount, builder_balance)`. If a builder's actual balance is smaller than the pending withdrawal amount, the CL only removes the smaller (capped) amount from the builder, while the execution payload still commits to paying out the full, larger amount to the recipient.

### Finding Description
`get_builder_withdrawals` copies the requested amount verbatim into the `Withdrawal` object without checking it against `state.builders[builder_index].balance`: [1](#0-0) 

That `Withdrawal` list becomes part of `ExpectedWithdrawals`/`state.payload_expected_withdrawals`, which the execution layer is contractually required to apply exactly as specified (crediting the `address` with `amount`).

Meanwhile, the actual balance-state mutation for that same withdrawal happens in `apply_withdrawals`, which explicitly caps the deduction at the builder's current balance: [2](#0-1) 

So whenever `builder.balance < withdrawal.amount` at processing time, two different numbers are used for the same withdrawal: the (larger) uncapped `amount` that the EL will pay out to the recipient, and the (smaller) capped amount actually removed from the sending builder's CL balance. This breaks the fundamental equality that the total Gwei removed from the system state must equal the Gwei paid out to a recipient — Gwei is effectively minted and paid to the withdrawal recipient without a matching debit anywhere in the beacon state. This is structurally the same class of bug as the Mcdex report: a value that should be constrained by (in Mcdex's case, funded from) the correct source is instead computed independently and can exceed what is actually backed, silently producing a shortfall/excess that the "insurance"/accounting layer does not reconcile.

This behavior is also directly confirmed by the test suite, which explicitly documents and asserts this exact discrepancy as expected behavior: [3](#0-2) 

### Impact Explanation
If a builder's `builder_pending_withdrawals` queue can ever contain a requested `amount` greater than the builder's current `balance` (e.g., because balance dropped between request and processing — for instance due to slashing-adjacent balance reduction, multiple concurrent pending withdrawals draining the balance, or fee/penalty deductions applied between the time the withdrawal was queued and the time it is processed), the execution layer will credit the recipient address with the full, uncapped `amount` while the beacon state only removes the smaller, capped amount from the builder. This creates Gwei that is not backed by any corresponding CL-side deduction and pays it to a recipient — a direct violation of Gwei conservation and a Critical-class impact per the rules (Gwei created and paid to a non-owner).

### Likelihood Explanation
This requires builder balance to fall below the sum of its outstanding pending withdrawal amounts by the time those withdrawals are processed. Because `builder_pending_withdrawals` entries are amounts fixed at request time while a builder's balance can continue to change afterward (via subsequent withdrawal requests, further balance-affecting operations, or multiple pending withdrawals compounding against the same balance), this condition is plausible without needing a malicious peer/coalition — it can arise from ordinary sequences of otherwise-valid builder actions, similar to how the original Mcdex bug occurs from ordinary bankrupt-position liquidations rather than a contrived attack.

### Recommendation
Cap the `amount` field written into the `Withdrawal` object itself in `get_builder_withdrawals` (and any sweep-style builder withdrawal function) to `min(withdrawal.amount, state.builders[builder_index].balance)`, so the value committed to the execution payload always matches what `apply_withdrawals` actually removes from the builder's balance. Alternatively, disallow enqueuing a `builder_pending_withdrawals` entry whose amount can no longer be fully covered, or reduce/cancel it at enqueue-adjustment time so the invariant `sum(withdrawal.amount for withdrawal in payload_expected_withdrawals) == sum(actual balance decreases)` always holds.

### Proof of Concept
Using the existing test as the concrete PoC (already present in the repo, demonstrating the discrepancy as "expected" behavior): [4](#0-3) 

- Builder 0 balance = 1 ETH.
- `builder_pending_withdrawals` contains one entry requesting 5 ETH for builder 0.
- After processing: `payload_expected_withdrawals` contains a `Withdrawal` with `amount = 5 ETH` (the requested amount) addressed to the builder's recipient — this is what the execution layer will pay out.
- `state.builders[0].balance` becomes `0` (only 1 ETH, the actual available balance, was deducted).
- Net effect: 4 ETH is paid to the recipient address by the execution layer with no corresponding balance ever existing in the beacon state to back it — Gwei created from nothing and paid to a non-owner.

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
