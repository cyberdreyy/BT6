### Title
Builder-withdrawal amount is committed from the queued request, not capped to the builder's actual balance at withdrawal time - ([File: specs/gloas/beacon-chain.md])

### Summary
`get_builder_withdrawals` places the *requested* `withdrawal.amount` from `state.builder_pending_withdrawals` directly into the committed `Withdrawal` (which the execution layer is required to honor and pay to `fee_recipient`), while `apply_withdrawals` only debits `min(withdrawal.amount, builder_balance)` from the builder's CL-side balance. If the builder's balance has fallen below the queued amount by the time the withdrawal is swept (e.g. via an intervening slashing/exit, or because multiple pending withdrawals accumulated for the same builder beyond its balance), the amount actually paid out on the EL side exceeds the amount actually debited on the CL side — creating Gwei that is not backed by any decrease in stake.

### Finding Description
`get_builder_withdrawals` (specs/gloas/beacon-chain.md) builds the committed `Withdrawal` using the raw requested amount from the queue entry, with no cap against the builder's current balance: [1](#0-0) 

That withdrawal is included in `payload_expected_withdrawals`, which the execution layer is obligated to pay out in full to `withdrawal.address` (`fee_recipient`). On the CL side, `apply_withdrawals` (modified in Gloas) only decreases `state.builders[builder_index].balance` by `min(withdrawal.amount, builder_balance)`: [2](#0-1) 

This is the same equality-breaking pattern as the external report: a system component assumes that a "requested" quantity and the "actually available" quantity are always equal (1 PT = 1 underlying → here, requested withdrawal amount = available builder balance), and silently saturates the deduction instead of reconciling the shortfall. The spec's own commentary about `process_withdrawals` explicitly acknowledges this exact class of issue for validator withdrawals ("CL-side saturation creates a net supply inflation... since the execution layer mints the full committed amount regardless"), but that reasoning is not applied to the builder-balance cap in `apply_withdrawals`: [3](#0-2) 

The unit tests confirm the committed withdrawal amount is the full *requested* amount even when the builder cannot cover it, while the balance deduction silently saturates at zero: [4](#0-3) 

The invariant equality broken: Gwei paid out by the EL (per the committed `Withdrawal.amount`) must equal Gwei debited from the corresponding CL balance. Here, `min(amount, balance)` on the CL side vs. the full uncapped `amount` on the EL/commitment side breaks that equality whenever `amount > balance`, i.e. Gwei is created and paid to the `fee_recipient` without being backed by an equivalent balance reduction anywhere in the system.

### Impact Explanation
This falls under "Gwei created ... or paid to a non-owner": the execution layer pays the full requested amount to `fee_recipient`, while the builder's own stake is only reduced by the (possibly much smaller) available balance. The excess is minted on the EL side with no offsetting CL-side debit — a direct violation of total-supply conservation across the CL/EL boundary, and money paid out that was never actually available/owned by the builder to withdraw. Unlike the acknowledged validator-sweep saturation case (which is explicitly flagged as a spec-level supply-inflation risk with a documented rationale for it being an unavoidable design tradeoff for validators), the builder pending-withdrawal path admits committing amounts that are already known (or can trivially become known) to exceed balance, since the requested amount is taken from a caller-supplied bid/queue entry, not derived from `builder.balance` at commit time.

### Likelihood Explanation
Builder pending withdrawals are queued as bid refunds/payments over time (`state.builder_pending_withdrawals` can accumulate multiple entries against the same builder before they are swept — see `get_pending_balance_to_withdraw_for_builder` aggregating multiple queued withdrawals per builder). Between the time a withdrawal is queued and the time it is processed via `get_builder_withdrawals`/`apply_withdrawals`, the builder's balance can be reduced by other builder-balance-affecting operations (e.g. additional bids consumed, other pending withdrawals processed earlier in the same block reducing balance below what later entries assume, or an exit). No invariant in the visible spec text guarantees `builder.balance` stays ≥ the sum of all outstanding `builder_pending_withdrawals.amount` for that builder at sweep time; the test suite explicitly exercises and accepts the "insufficient balance" case as expected behavior rather than as an assertion failure, confirming this is a reachable state under normal (non-malicious, non-Byzantine) spec-following execution.

### Recommendation
`get_builder_withdrawals` should compute the committed `Withdrawal.amount` as `min(withdrawal.amount, builder.balance - <balance already committed to prior entries for this builder in the same block>)`, mirroring the approach used for `get_pending_partial_withdrawals`/`get_validators_sweep_withdrawals`, which compute `amount` from the *current available* balance rather than a stale requested value. This way the amount actually committed to the EL always equals the amount actually debited from `state.builders[...].balance`, preserving the supply-conservation invariant.

### Proof of Concept
1. Queue a `BuilderPendingWithdrawal` requesting `amount = 5 ETH` for `builder_index = 0` while `state.builders[0].balance = 1 ETH` (as in `test_builder_withdrawal_insufficient_balance`): [5](#0-4) 
2. `get_builder_withdrawals` emits `Withdrawal(validator_index=convert_builder_index_to_validator_index(0), address=fee_recipient, amount=5 ETH)` — the full requested amount, unconstrained by the 1 ETH balance: [6](#0-5) 
3. `apply_withdrawals` executes `state.builders[0].balance -= min(5 ETH, 1 ETH)`, leaving `builders[0].balance = 0`: [7](#0-6) 
4. The block commits `payload_expected_withdrawals` containing the 5 ETH withdrawal, which the execution layer must credit in full to `fee_recipient`. Only 1 ETH was ever debited from any CL-tracked balance — 4 ETH has been created and paid to `fee_recipient` with no corresponding burn, confirmed by the test's expected assertions (`builder_balances={builder_index: 0}` and `withdrawal_amounts_builders={builder_index: withdrawal_amount}` i.e. the full 5 ETH): [8](#0-7)

### Citations

**File:** specs/gloas/beacon-chain.md (L1815-1830)
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
```

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
