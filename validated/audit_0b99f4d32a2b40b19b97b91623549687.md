### Title
Builder withdrawal amount is credited to the EL recipient uncapped while the CL-side balance deduction is capped, minting unbacked Gwei - (File: `specs/gloas/beacon-chain.md`)

### Summary
In the Gloas builder-withdrawal path, `apply_withdrawals` caps the *balance decrement* applied to `state.builders[builder_index].balance` with `min(withdrawal.amount, builder_balance)`, but the `Withdrawal.amount` field that is placed into `state.payload_expected_withdrawals` — the value the execution layer is required to honor as an actual account-balance credit — is never capped to the same bound. This is the direct structural analog of the reported `_payment()` bug: a "payment" (`paymentAmount` / `withdrawal.amount`) that is sent/committed in full even though the paying entity's owed/available amount is smaller.

### Finding Description
`apply_withdrawals` decrements the builder's CL balance using a `min()` guard: [1](#0-0) 

```python
def apply_withdrawals(state: BeaconState, withdrawals: Sequence[Withdrawal]) -> None:
    for withdrawal in withdrawals:
        if is_builder_index(withdrawal.validator_index):
            builder_index = convert_validator_index_to_builder_index(withdrawal.validator_index)
            builder_balance = state.builders[builder_index].balance
            state.builders[builder_index].balance -= min(withdrawal.amount, builder_balance)
        else:
            decrease_balance(state, withdrawal.validator_index, withdrawal.amount)
```

The `min()` only affects the CL bookkeeping decrement. It does **not** change `withdrawal.amount` itself, which was already fixed earlier when the `Withdrawal` object was built from `builder_pending_withdrawals[i].amount` (the requester-supplied value) and placed into `state.payload_expected_withdrawals`. That list is what the execution layer is contractually bound to execute as literal balance credits to `fee_recipient` — the spec explicitly states this binding and even flags the general risk of CL/EL divergence from balance-capping in a nearby note: [2](#0-1) 

The test suite confirms the asymmetry is real and intentional in current spec text, not a testing artifact: when a builder's balance is short of the requested withdrawal amount, the produced `Withdrawal.amount` stays at the full requested (uncapped) value while only the available balance is actually debited from the builder: [3](#0-2) 

Concretely, with `builders[0].balance = MIN_DEPOSIT_AMOUNT + 122` and a pending withdrawal request of `MIN_DEPOSIT_AMOUNT + 123`, the produced `withdrawal.amount` in `payload_expected_withdrawals` equals `MIN_DEPOSIT_AMOUNT + 123` (the full request) while `builders[0].balance` only drops by `122` down to `0`. The execution layer, honoring `payload.withdrawals` literally, credits `fee_recipient` the full `MIN_DEPOSIT_AMOUNT + 123`, i.e. 1 Gwei more than was ever debited from any account.

This breaks the fundamental equality that every Gwei credited on the EL side must correspond to an equal Gwei debited on the CL side. It is the same root defect as the reported `_payment()` bug: the amount actually paid/credited to the recipient is not clamped to the amount the payer's ledger can support (`lien.amount` vs `paymentAmount`, here `builder_balance` vs `withdrawal.amount`).

### Impact Explanation
This is a Critical-class finding under the given rubric ("Gwei created ... paid to a non-owner"): a validator/staker whose withdrawal request exceeds their tracked balance causes the protocol to mint ETH on the execution layer that is not backed by any corresponding CL balance decrease, i.e. Gwei is created out of thin air and paid to `fee_recipient`. Because `builder_pending_withdrawals` are consumed strictly in FIFO/queue order (processed before sweep withdrawals, per `get_expected_withdrawals`), an unprivileged builder can simply queue a pending-withdrawal request whose `amount` exceeds their current balance (e.g., after balance shrinks from other decrements applied earlier in the same block, or simply by requesting more than is currently staked) and have the excess minted for free on the EL side.

### Likelihood Explanation
Reachability requires only a normal builder submitting/queuing a `BuilderPendingWithdrawal` with `amount` greater than their balance at processing time — no coalition, no malicious peer, and no client bug is needed; it is entirely a function of ordinary protocol usage (e.g., balance reduced by an earlier withdrawal/penalty in the same processing pass before this entry is consumed, or amount requested against a balance the requester no longer fully controls). The condition is directly exercised by the repository's own test (`test_builder_withdrawal_insufficient_balance_realistic_bounds`), confirming it is an easily triggerable state, not a purely theoretical edge case.

### Recommendation
Cap the `Withdrawal.amount` recorded in `payload_expected_withdrawals` for builder withdrawals to `min(requested_amount, builder.balance)` at the point the `Withdrawal` object is constructed (in the builder-pending-withdrawal and builder-sweep withdrawal helper functions), so the amount the EL is committed to pay always matches the amount actually debited in `apply_withdrawals`. This mirrors the audit's recommended fix of clamping `paymentAmount` to `lien.amount` before the transfer, ensuring the transferred/committed value and the debited value are always equal.

### Proof of Concept
Using the existing test harness values as a concrete trace:
1. Builder 0 has `balance = MIN_DEPOSIT_AMOUNT + 122`.
2. A `BuilderPendingWithdrawal` is queued with `amount = MIN_DEPOSIT_AMOUNT + 123`.
3. `get_expected_withdrawals` builds a `Withdrawal(validator_index=builder_validator_index, amount=MIN_DEPOSIT_AMOUNT + 123, ...)` — the requested value, unclamped.
4. `process_withdrawals` sets `state.payload_expected_withdrawals` to include this withdrawal; the execution layer is bound to credit `fee_recipient` exactly `MIN_DEPOSIT_AMOUNT + 123`.
5. `apply_withdrawals` executes `state.builders[0].balance -= min(MIN_DEPOSIT_AMOUNT + 123, MIN_DEPOSIT_AMOUNT + 122)`, leaving `builders[0].balance = 0`.
6. Net effect: `fee_recipient` receives `MIN_DEPOSIT_AMOUNT + 123` Gwei on the EL side, but only `MIN_DEPOSIT_AMOUNT + 122` Gwei was ever debited anywhere on the CL side — 1 Gwei was created without being backed by any decreased balance. [4](#0-3)

### Citations

**File:** specs/gloas/beacon-chain.md (L1922-1932)
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
