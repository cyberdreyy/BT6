## Title
Builder withdrawal amount is not capped to available balance before being committed, allowing Gwei to be created without a matching CL-side deduction — (File: `specs/gloas/beacon-chain.md`)

## Summary
In the Gloas fork, `get_builder_withdrawals` builds the `Withdrawal` records that the block commits to (via `state.payload_expected_withdrawals`, which the execution layer is obligated to credit) using the *full requested* `withdrawal.amount` from `state.builder_pending_withdrawals`, without capping it to the builder's actual balance. `apply_withdrawals`, however, only deducts `min(withdrawal.amount, builder_balance)` from the builder's CL balance. This breaks the equality "every Gwei credited on the EL side must be destroyed on the CL side," analogous to the reported ERC20-bridge bug where a `Withdrawal` event is emitted for an amount that was not actually transferred.

## Finding Description
`get_builder_withdrawals` unconditionally copies the pending withdrawal's requested amount into the committed `Withdrawal` object: [1](#0-0) 

That committed list becomes `state.payload_expected_withdrawals`, which is the CL's binding commitment that the execution layer must honor when it credits `fee_recipient` addresses for the corresponding amounts.

Meanwhile, `apply_withdrawals` — called from the *same* `process_withdrawals` invocation, on the same state — caps the actual balance deduction to whatever the builder currently has: [2](#0-1) 

So if `builder_pending_withdrawal.amount > builder.balance` at processing time, the committed `Withdrawal.amount` (which the EL will pay out) is the full requested amount, while only the smaller `builder.balance` is actually subtracted from `state.builders[builder_index].balance`. This is exactly the class of bug described in the external report: a `transfer`/payout is committed/recorded without checking that the amount actually deducted matches, silently creating value.

By contrast, the analogous validator logic in `get_pending_partial_withdrawals` deliberately caps the withdrawal amount to `balance - MIN_ACTIVATION_BALANCE` before recording it (per the documented invariant: "Actual = `min(balance - MIN_ACTIVATION_BALANCE, amount)`"), and full validator withdrawals record `amount=balance` (the whole balance), so `decrease_balance`'s own saturation-at-zero logic never triggers for validators. Builder pending withdrawals are the sole withdrawal path where the *recorded/committed* amount and the *actually-deducted* amount are allowed to diverge.

This divergence is not merely theoretical — it is explicitly exercised by the existing spec test suite: [3](#0-2) 

The test explicitly documents: "withdrawal.amount: 5 ETH (requested amount)" while "builders[0].balance: 0 (deduction capped to available balance)" — i.e., the spec's own test suite confirms the committed withdrawal amount is 5x greater than what was actually removed from the builder's balance.

A `builder_pending_withdrawals` entry with an amount exceeding the builder's *current* balance is readily reachable: a builder payment is queued via `settle_builder_payment` after `can_builder_cover_bid` was checked at bid time, but the builder's balance can decrease between the bid and settlement (e.g., other pending withdrawals/exits settling first, or multiple bids across slots in the same epoch consuming the same balance). No malicious peer, coalition, or client bug is required — it is a legitimate sequencing scenario within the honest spec state machine.

## Impact Explanation
This breaks a Critical-tier equality: "Gwei created, destroyed, or paid to a non-owner" and "a payload or payment applied that the block did not commit to" in the wrong direction (the block commits to *more* payment than it backs with an actual balance decrease). Since `payload_expected_withdrawals` is the binding contract with the execution layer, the EL will mint/credit the full requested amount to the `fee_recipient`, while the CL only ever removed the smaller, capped amount from the builder's balance — a net, unbacked increase in circulating Gwei that is paid to the fee_recipient without a corresponding source deduction.

## Likelihood Explanation
This requires no adversarial coordination: it can occur any time a builder's pending withdrawal queue accumulates entries whose requested amount, by the time of processing, exceeds the builder's live balance (e.g., due to other withdrawals or payments settling first in the same block/epoch). This is a normal, spec-following state transition path, not an attack requiring a malicious peer or coalition.

## Recommendation
In `get_builder_withdrawals`, cap the recorded `Withdrawal.amount` to `min(withdrawal.amount, state.builders[builder_index].balance)` (mirroring how `apply_withdrawals` computes the actual deduction), so the amount committed to the execution layer always matches the amount actually removed from the builder's CL balance.

## Proof of Concept
1. Set `state.builders[0].balance = 1_000_000_000` (1 ETH).
2. Append a `BuilderPendingWithdrawal(builder_index=0, amount=5_000_000_000, fee_recipient=...)` to `state.builder_pending_withdrawals` (reachable via normal `settle_builder_payment`/queued-payment sequencing where balance drops before settlement).
3. Call `process_withdrawals(state)`.
4. Observe: `state.payload_expected_withdrawals[0].amount == 5_000_000_000` (committed to EL) while `state.builders[0].balance == 0` (only 1 ETH actually deducted) — as confirmed by `test_builder_withdrawal_insufficient_balance` in `tests/core/pyspec/eth_consensus_specs/test/gloas/block_processing/test_process_withdrawals.py:114-156`. The execution layer will credit 5 ETH to `fee_recipient` while only 1 ETH was destroyed on the CL side — 4 ETH of unbacked Gwei created.

### Citations

**File:** specs/gloas/beacon-chain.md (L1821-1829)
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
