## Title
Builder pending withdrawal `amount` is not capped to available `builder.balance`, allowing committed `Withdrawal.amount` (paid out by the execution layer) to exceed the actual Gwei debited from the builder — ([File: specs/gloas/beacon-chain.md])

## Summary
`get_builder_withdrawals` in `specs/gloas/beacon-chain.md` builds each output `Withdrawal` using the raw, uncapped `amount` field taken from `state.builder_pending_withdrawals`, while `apply_withdrawals` only debits `min(withdrawal.amount, builder_balance)` from the builder's actual balance. Because the committed `Withdrawal.amount` (not the capped debit) is what gets placed into `state.payload_expected_withdrawals` — the value the execution layer is required to pay out to `withdrawal.address` — a shortfall in builder balance at settlement time results in the execution layer minting/paying more Gwei to `fee_recipient` than was ever subtracted from the builder's balance on the consensus layer.

## Finding Description
`get_builder_withdrawals` (specs/gloas/beacon-chain.md, lines 1802-1834) iterates `state.builder_pending_withdrawals` and unconditionally appends:
```
Withdrawal(index=…, validator_index=…, address=withdrawal.fee_recipient, amount=withdrawal.amount)
``` [1](#0-0) 

using the raw requested `amount`, with no capping to the builder's current balance. This differs from the sibling functions in the same file: `get_pending_partial_withdrawals` caps the amount via `min(balance - MIN_ACTIVATION_BALANCE, withdrawal.amount)` [2](#0-1) , and `get_builders_sweep_withdrawals` uses the builder's actual `balance` as the amount [3](#0-2) .

Meanwhile, `apply_withdrawals` only debits the capped amount from the builder's balance:
```
builder_balance = state.builders[builder_index].balance
state.builders[builder_index].balance -= min(withdrawal.amount, builder_balance)
``` [4](#0-3) 

The uncapped `withdrawal.amount` (not the capped debit) is what is committed into `state.payload_expected_withdrawals` via `update_payload_expected_withdrawals` [5](#0-4) , and this is the exact list the execution layer is bound to honor/pay out (as documented in the note directly above `process_withdrawals`: "any execution payload that has the corresponding block as parent beacon block is required to honor these withdrawals in the execution layer") [6](#0-5) .

This is structurally the same bug class as the Balancer report: a downstream accounting step (`apply_withdrawals`/CL balance bookkeeping) diverges from what is actually committed/paid out (`Withdrawal.amount` honored by the EL) — the "committed value" and the "true backing value" are computed independently and can disagree, breaking the equality "Gwei paid == Gwei debited from its owner."

The behavior is explicitly exercised by the existing spec test `test_builder_withdrawal_insufficient_balance_realistic_bounds`, which sets a builder with `balance = MIN_DEPOSIT_AMOUNT + 122` Gwei and a pending withdrawal requesting `MIN_DEPOSIT_AMOUNT + 123` Gwei, then asserts the resulting committed withdrawal still carries the full requested (uncapped) amount while the builder balance is only reduced to 0: [7](#0-6) 

I was not able to fully verify, within the available index, whether `can_builder_cover_bid` (invoked when a bid is accepted, at `specs/gloas/beacon-chain.md:2104`) accounts for the cumulative total of all currently outstanding `builder_pending_payments`/`builder_pending_withdrawals` for that builder, or only checks the builder's raw current balance against the single new bid. If it only checks the latter, a builder can accumulate several accepted bids/payments whose sum exceeds its real balance by the time they are settled (settlement is deferred by up to ~2 epochs via `process_builder_pending_payments`), reproducing exactly the insufficient-balance condition the test above demonstrates. This part of the root-cause chain (whether `can_builder_cover_bid` prevents accumulation, or only guards a single bid) could not be confirmed with the tools available in this session and should be verified directly against the function body in `specs/gloas/beacon-chain.md`.

## Impact Explanation
If the uncapped `Withdrawal.amount` reaches the execution layer while the consensus layer only debited a smaller (capped) amount from the builder, Gwei is effectively created and paid to `fee_recipient` — an address that is not the entity whose balance actually backed the payment. This matches the Critical-tier criterion "Gwei created, destroyed, or paid to a non-owner," since the payment is not backed by an equivalent debit from the builder account that is nominally paying it.

## Likelihood Explanation
The scenario is directly reachable and already covered by an existing spec test demonstrating the uncapped-amount / capped-debit mismatch (`test_builder_withdrawal_insufficient_balance_realistic_bounds`). Whether an honest builder can practically reach balance insufficiency at settlement time (via repeated bidding before prior payments settle) depends on the exact guard in `can_builder_cover_bid`, which I was unable to confirm in this session — this is the main open uncertainty limiting a firm likelihood assessment.

## Recommendation
Cap the `amount` used when constructing the `Withdrawal` object in `get_builder_withdrawals` to `min(withdrawal.amount, state.builders[builder_index].balance)` (consistent with how `get_pending_partial_withdrawals` and `get_builders_sweep_withdrawals` compute their amounts), so the value committed into `state.payload_expected_withdrawals` (and thus paid by the execution layer) can never exceed what `apply_withdrawals` actually debits from the builder. Alternatively, ensure `can_builder_cover_bid` accounts for the full outstanding sum of all pending builder payments/withdrawals (not just the single new bid) so balance can never fall short by settlement time.

## Proof of Concept
Using the existing test harness pattern from `test_builder_withdrawal_insufficient_balance_realistic_bounds`:
1. Set `state.builders[0].balance = X` (e.g., `MIN_DEPOSIT_AMOUNT + 122`).
2. Add `state.builder_pending_withdrawals` entry for builder 0 with `amount = X + 1` (e.g., `MIN_DEPOSIT_AMOUNT + 123`).
3. Run `process_withdrawals(state)`.
4. Observe: `state.payload_expected_withdrawals[0].amount == X + 1` (the full, uncapped requested amount, which the execution layer is bound to pay to `fee_recipient`), while `state.builders[0].balance == 0` (only `X` was ever debited).
5. The delta (`1` Gwei in this minimal example, unbounded in general) is Gwei paid out to `fee_recipient` that was never backed by a corresponding debit anywhere in consensus-layer state. [8](#0-7)

### Citations

**File:** specs/gloas/beacon-chain.md (L1821-1831)
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

**File:** specs/gloas/beacon-chain.md (L1926-1929)
```markdown
        if is_builder_index(withdrawal.validator_index):
            builder_index = convert_validator_index_to_builder_index(withdrawal.validator_index)
            builder_balance = state.builders[builder_index].balance
            state.builders[builder_index].balance -= min(withdrawal.amount, builder_balance)
```

**File:** specs/gloas/beacon-chain.md (L1937-1941)
```markdown
def update_payload_expected_withdrawals(
    state: BeaconState, withdrawals: Sequence[Withdrawal]
) -> None:
    state.payload_expected_withdrawals = Withdrawals(data=withdrawals)
```
```

**File:** specs/gloas/beacon-chain.md (L1969-1972)
```markdown
*Note*: This is modified to only take the `state` as parameter. Withdrawals are
deterministic given the beacon state, any execution payload that has the
corresponding block as parent beacon block is required to honor these
withdrawals in the execution layer. `process_withdrawals` must be called after
```

**File:** specs/electra/beacon-chain.md (L1384-1394)
```markdown
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
