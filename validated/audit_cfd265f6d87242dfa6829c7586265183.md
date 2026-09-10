This confirms the vulnerability. The `get_builder_withdrawals` function creates a `Withdrawal` object with `amount=withdrawal.amount` (the full requested amount from `BuilderPendingWithdrawal`), regardless of whether the builder actually has that much balance [1](#0-0) . But `apply_withdrawals` only deducts `min(withdrawal.amount, builder_balance)` from the builder's internal `state.builders[builder_index].balance` [2](#0-1) . Since `process_withdrawals` output (`payload_expected_withdrawals`) is binding on the execution layer — "any execution payload that has the corresponding block as parent beacon block is required to honor these withdrawals in the execution layer" [3](#0-2)  — the EL will credit the fee_recipient the full (uncapped) `withdrawal.amount`, while the CL only debits the capped amount from the builder's balance. This is confirmed directly by the spec's own test comments: `withdrawal.amount: 5 ETH (requested amount)` vs `builders[0].balance: 0 (deduction capped to available balance)` [4](#0-3) .

This is the exact analog of the Rubicon bug class: the amount recorded/committed (like `info.pay_amt`) diverges from the amount actually moved, breaking a supply/balance equality — except here it manifests as inflation (EL pays out more than CL deducts) rather than the FoT deflation case.

### Title
Builder pending withdrawal pays out the uncapped requested amount while only the available (capped) balance is deducted, minting Gwei out of thin air - (File: specs/gloas/beacon-chain.md)

### Summary
`get_builder_withdrawals` builds the `Withdrawal` object sent to the execution layer using the raw, uncapped `BuilderPendingWithdrawal.amount`, while `apply_withdrawals` only deducts `min(withdrawal.amount, builder.balance)` from the builder's internal ledger. When a builder's balance is less than the pending withdrawal's requested amount, the EL is instructed (and bound) to pay out the full requested amount to `fee_recipient`, but the CL only removes the smaller, capped amount from the builder's balance.

### Finding Description
`get_builder_withdrawals` iterates `state.builder_pending_withdrawals` and emits, for each entry, a `Withdrawal(amount=withdrawal.amount, ...)` using the full requested amount with no clamping to the builder's current balance [5](#0-4) .

These withdrawals become `state.payload_expected_withdrawals`, and per spec commentary, "any execution payload that has the corresponding block as parent beacon block is required to honor these withdrawals in the execution layer" [6](#0-5) . That means the EL mints/transfers the full `withdrawal.amount` (e.g., 5 ETH) to the withdrawal address regardless of what the builder's actual on-chain balance is.

Meanwhile, `apply_withdrawals` deducts only the capped amount from the builder's CL-tracked balance:
```python
def apply_withdrawals(state: BeaconState, withdrawals: Sequence[Withdrawal]) -> None:
    for withdrawal in withdrawals:
        if is_builder_index(withdrawal.validator_index):
            builder_index = convert_validator_index_to_builder_index(withdrawal.validator_index)
            builder_balance = state.builders[builder_index].balance
            state.builders[builder_index].balance -= min(withdrawal.amount, builder_balance)
        else:
            decrease_balance(state, withdrawal.validator_index, withdrawal.amount)
``` [7](#0-6) 

So if a builder has only 1 ETH but a queued pending withdrawal requests 5 ETH (which can arise e.g. from `process_execution_payload_bid`'s pending-payment accounting or any code path that appends to `builder_pending_withdrawals` without verifying it never exceeds balance at settlement time), the emitted `Withdrawal.amount` is 5 ETH (paid out by EL) while only 1 ETH is deducted from the CL ledger — a 4 ETH mismatch. This is exactly analogous to the Rubicon `info.pay_amt` vs. actual-transferred-amount inconsistency: the committed/recorded amount diverges from the amount actually applied to the internal ledger, breaking the equality between what leaves the system (EL payment) and what is debited (CL balance).

The spec's own generated tests explicitly confirm this behavior is intended as specified (not merely a test artifact): `test_builder_withdrawal_insufficient_balance` and `test_builder_withdrawal_insufficient_balance_realistic_bounds` assert `withdrawal_amounts_builders={builder_index: withdrawal_amount}` (the full 5 ETH / uncapped amount) is what appears in `payload_expected_withdrawals`, while `builder_balances={builder_index: 0}` shows only the capped deduction [4](#0-3) .

### Impact Explanation
This breaks the Gwei conservation invariant: value is paid out on the execution layer (to `fee_recipient`) in excess of what is deducted from the builder's consensus-layer balance. This is a form of Gwei creation — an amount that is not actually escrowed/available is nonetheless withdrawn to an external address. Under the Critical impact bucket ("Gwei created, destroyed, or paid to a non-owner" / "a payload executed or paid that the block did not commit to" in effect, though here it's committed-but-inflated), this represents unauthorized minting of value that no validator/builder authorized and that isn't backed by any actual stake.

### Likelihood Explanation
Whether this is reachable depends on whether any code path can cause a `BuilderPendingWithdrawal.amount` to exceed the builder's balance at the time `get_builder_withdrawals` runs. `builder_pending_withdrawals` entries are created e.g. via `settle_builder_payment` / from `execution_payload_bid` processing, and the balance can decrease between when the pending withdrawal is queued and when it is dequeued (e.g., other withdrawals draining the same builder, or being charged for another bid) since builder balance is a single shared value against which multiple pending payments/withdrawals may be recorded concurrently. The test suite itself demonstrates and asserts this exact scenario is normal, spec-following behavior, not a fringe case guarded against elsewhere.

### Recommendation
`get_builder_withdrawals` should clamp `amount` to `min(withdrawal.amount, state.builders[builder_index].balance)` at the point the `Withdrawal` object is constructed (mirroring the capping done in `apply_withdrawals`), so the amount promised to the EL always matches the amount actually deducted from the builder's balance. Alternatively, ensure builder balance can never fall below the sum of its still-pending withdrawals so that the `min()` clamp in `apply_withdrawals` never actually engages.

### Proof of Concept
1. A builder has balance = 1 ETH.
2. A `BuilderPendingWithdrawal` with `amount = 5 ETH` for that builder is queued in `state.builder_pending_withdrawals` (e.g., due to balance draining from a concurrent event between when the pending withdrawal amount was fixed and settlement).
3. `process_withdrawals` → `get_expected_withdrawals` → `get_builder_withdrawals` emits `Withdrawal(amount=5 ETH, address=fee_recipient, ...)` into `payload_expected_withdrawals` [8](#0-7) .
4. `apply_withdrawals` deducts only `min(5 ETH, 1 ETH) = 1 ETH` from `state.builders[builder_index].balance`, leaving it at 0 [9](#0-8) .
5. The execution layer, bound to honor `payload_expected_withdrawals`, pays out the full 5 ETH to `fee_recipient`.
6. Net result: 4 ETH paid out on the EL with no corresponding CL deduction — Gwei created out of thin air, exactly mirroring the spec's own test assertions for `test_builder_withdrawal_insufficient_balance` [10](#0-9) .

### Citations

**File:** specs/gloas/beacon-chain.md (L1815-1834)
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
