### Title
Builder withdrawal pays the recorded pending amount while only debiting the builder's actual (possibly lower) balance, minting Gwei — (File: `specs/gloas/beacon-chain.md`)

### Summary
In Gloas, a builder payment is recorded once (`settle_builder_payment`) and queued into `state.builder_pending_withdrawals` with a fixed `amount` taken from the `BuilderPendingPayment.withdrawal` that was computed at bid-acceptance time. When the withdrawal is later executed, the amount that is *paid out* on the execution layer is this stored, fixed amount, but the amount *debited* from `state.builders[builder_index].balance` is capped to whatever balance the builder still has at withdrawal time. If the builder's on-chain balance has fallen below the recorded `amount` by the time the withdrawal is swept, the payout to `fee_recipient` exceeds the debit taken from the builder, breaking the invariant that every Gwei paid out must be matched by an equal decrease somewhere in the state.

### Finding Description
`settle_builder_payment` blindly re-emits the amount cached in the payment record without re-checking it against the builder's current balance: [1](#0-0) 

That queued `BuilderPendingWithdrawal.amount` is later executed during withdrawal sweeping. The generated pyspec test suite explicitly documents (and asserts) that when the builder's balance is insufficient to cover the recorded amount, the balance debit is capped to whatever is available, while the withdrawal amount paid out remains the full, originally recorded (higher) amount: [2](#0-1) 

This is structurally identical to the reported bug class: a stored/cached value (`BACKUP_KERNEL_ROOT_HASH`/here, the pre-computed `withdrawal.amount`) is copied/applied to the "live" location (the actual payout to `fee_recipient`) without validating that it is still consistent with the current, authoritative state (`state.builders[i].balance`) at the time of use. In the Etherlink case, a stale backup could restore wrong kernel data; here, a stale withdrawal record pays out more than the builder actually has, so the excess amount is paid to `fee_recipient` without ever having been debited from anyone — i.e., Gwei is created out of thin air and paid to a party (the `fee_recipient`) that did not have a legitimate claim to it.

### Impact Explanation
This breaks the fundamental accounting equality that total Gwei in the system is conserved across state transitions: `sum(balances_before) == sum(balances_after) + net_payouts`. If the payout (`withdrawal.amount`) exceeds the actual debit taken from the builder's balance, the delta is unbacked Gwei credited to an external `fee_recipient` address. Per the stated impact classes, this is a Critical finding ("Gwei created, destroyed or paid to a non-owner").

### Likelihood Explanation
The scenario requires a builder's balance to drop below the amount recorded in a still-pending `BuilderPendingPayment`/`BuilderPendingWithdrawal` before that withdrawal is swept — e.g., via slashing, other withdrawals, or additional payment obligations accruing against the same builder balance between payment settlement and withdrawal execution. This does not require a malicious peer or client bug; it can occur under ordinary, spec-compliant operation given several builder payments/queued withdrawals outstanding at once, since `settle_builder_payment` never checks the builder's balance before queuing the payout amount, and the withdrawal-processing step (as demonstrated by the referenced test) explicitly caps the *debit* but not the *paid amount*.

### Recommendation
Before crediting a builder withdrawal (or at the point of capping the balance debit due to insufficient available balance), cap the withdrawal `amount` itself to the actual amount debited from the builder's balance, so the value paid to `fee_recipient` can never exceed what was actually removed from `state.builders[builder_index].balance`. Alternatively, verify sufficient balance at `settle_builder_payment` time and fail/postpone the payment rather than queuing an amount that can later exceed the builder's real balance.

### Proof of Concept
Using the exact scenario from the generated test suite (which exercises the underlying `process_withdrawals`/`settle_builder_payment` logic in `specs/gloas/beacon-chain.md`): [3](#0-2) 

1. `state.builders[0].balance = MIN_DEPOSIT_AMOUNT + 122`.
2. A pending builder withdrawal of `MIN_DEPOSIT_AMOUNT + 123` is queued (i.e., 1 Gwei more than the builder's actual balance).
3. After `process_withdrawals` runs: `builders[0].balance == 0` (only 122 Gwei worth was ever debited) while the emitted withdrawal to `fee_recipient` carries `amount == MIN_DEPOSIT_AMOUNT + 123`.
4. Net result: 1 Gwei is paid out to `fee_recipient` that was never subtracted from any validator/builder balance — Gwei created from nothing.

### Citations

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
