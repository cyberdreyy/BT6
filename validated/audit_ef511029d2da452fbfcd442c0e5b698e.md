### Title
`get_builder_withdrawals` commits the full (uncapped) requested amount to the EL withdrawal while `apply_withdrawals` only deducts the balance-capped amount, minting Gwei out of thin air - ([File: specs/gloas/beacon-chain.md])

### Summary
This mirrors the Sense audit finding: two related code paths use different amounts for what should be the same value transfer. In `sponsorSeries()`, `safeTransferFrom` moved one amount while `safeApprove` recorded a different one. In `specs/gloas/beacon-chain.md`, `get_builder_withdrawals` puts the builder's *requested* withdrawal amount into the `Withdrawal` object that is committed to the execution layer, while `apply_withdrawals` decreases the builder's CL balance by only `min(withdrawal.amount, builder_balance)`.

### Finding Description
`get_builder_withdrawals` builds the canonical withdrawal list from `state.builder_pending_withdrawals`, copying `withdrawal.amount` (the originally requested/committed amount) verbatim into the `Withdrawal.amount` field: [1](#0-0) 

This list becomes `state.payload_expected_withdrawals`, which the execution layer is required to honor by crediting exactly `withdrawal.amount` Gwei to `withdrawal.address` (`fee_recipient`), per the standard withdrawal semantics inherited from Capella: [2](#0-1) 

However, when the consensus layer actually applies this withdrawal to state, it caps the balance deduction at whatever the builder currently has: [3](#0-2) 

So if `withdrawal.amount` (5 ETH, as committed to the EL) exceeds `builder_balance` (1 ETH, as tracked on the CL), the CL only removes 1 ETH from `state.builders[i].balance`, but the `Withdrawal` object — which the EL will execute as an unconditional balance credit of 5 ETH to `fee_recipient` — still declares the full 5 ETH. The repo's own test for this path documents the exact mismatch: it explicitly expects `withdrawal.amount == 5 ETH` (the full requested amount) in `payload_expected_withdrawals` while `builders[0].balance` only drops to 0 (a 1 ETH deduction): [4](#0-3) 

This is the exact bug class from the report: `safeTransferFrom`(EL credit amount) uses one number, `safeApprove`(CL debit) uses another. Here, the amount "approved for transfer" via the payload's committed withdrawal list (5 ETH, minted on the EL side to `fee_recipient`) is larger than the amount actually debited from the source (1 ETH from the builder's CL balance).

### Impact Explanation
This breaks the equality that every Gwei credited by a withdrawal must be backed by an equal Gwei debited from state. Whenever a builder's pending withdrawal amount (queued via `settle_builder_payment`/`process_builder_pending_payments`, or directly via `process_execution_payload_bid`'s bid `value`) exceeds the builder's balance at the time the withdrawal sweep actually processes it (balance can shrink between commitment and settlement, e.g. via other withdrawals, other bids consuming balance, or exits), the execution layer will credit the fee recipient the full requested amount while the beacon state only debits the smaller, capped amount. This is Gwei created out of thin air and paid to a possibly non-owner recipient (the `fee_recipient`), which is flagged in the rules as Critical impact ("Gwei created ... or paid to a non-owner").

### Likelihood Explanation
`can_builder_cover_bid` is checked only at bid-acceptance time (`process_execution_payload_bid`), not at withdrawal-settlement time, and a builder's balance can be reduced between those two points by unrelated events (additional bids being accepted and settled, or a builder-initiated exit sweep). The withdrawal capping logic in `apply_withdrawals` and the corresponding test explicitly acknowledge that `builder.balance` can be less than the requested withdrawal amount at settlement time, so this is a reachable, non-adversarial state — no malicious peer or coalition is required, just ordinary sequencing of builder deposits/bids/withdrawals across blocks and epochs.

### Recommendation
Ensure the amount embedded in the `Withdrawal` object that is submitted to the execution layer is capped in the same way the CL debit is capped, i.e. in `get_builder_withdrawals`, use `amount=min(withdrawal.amount, state.builders[builder_index].balance)` instead of the raw `withdrawal.amount`, so the value committed to the EL always equals the value actually removed from CL state.

### Proof of Concept
1. Builder `B` submits/accepts bids whose cumulative committed payments equal exactly `B`'s balance (`can_builder_cover_bid` passes at each individual check).
2. Before all pending withdrawals for `B` settle, one withdrawal is processed which reduces `B.balance` (e.g., via the builder sweep or an earlier pending withdrawal in the same `get_expected_withdrawals` call, since `builder_pending_withdrawals` is processed as a list and multiple entries for the same builder can exist as shown by `get_pending_balance_to_withdraw_for_builder` accounting for multiple withdrawals per builder).
3. When the next `BuilderPendingWithdrawal` for `B` is converted into a `Withdrawal` by `get_builder_withdrawals`, its `amount` is the originally recorded (now unaffordable) amount, not `min(amount, B.balance)`.
4. `apply_withdrawals` then debits only `min(withdrawal.amount, B.balance)` from `state.builders[B].balance` (per [5](#0-4) ), while `state.payload_expected_withdrawals` (and thus the EL payload's withdrawals list) still declares the full original `withdrawal.amount`.
5. The execution layer honors the payload and credits `fee_recipient` the full declared amount, exceeding what was actually deducted from `B` on the consensus layer — net supply inflation, exactly as reproduced by the repo's own `test_builder_withdrawal_insufficient_balance` test: [6](#0-5)

### Citations

**File:** specs/gloas/beacon-chain.md (L1821-1833)
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

**File:** specs/capella/beacon-chain.md (L497-501)
```markdown
```python
def apply_withdrawals(state: BeaconState, withdrawals: Sequence[Withdrawal]) -> None:
    for withdrawal in withdrawals:
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
