### Title
Builder withdrawal amount sent to the execution layer is not capped to the builder's actual balance, minting Gwei out of thin air - ([File: specs/gloas/beacon-chain.md])

### Summary
`apply_withdrawals()` in the Gloas fork deducts a builder's CL-side balance capped at `min(withdrawal.amount, builder_balance)`, but the `Withdrawal.amount` field that is placed into `state.payload_expected_withdrawals` — which the execution layer is required to honor and actually mint/pay out to `withdrawal.address` — is never capped to that same available balance. This is the same class of bug as the reported `transferPlayerRewards()` flaw: a value that is deducted/limited on one side of an accounting operation is not applied consistently to the value that is actually paid out, so the amount "promised" to the payee diverges from the amount actually backed by CL state.

### Finding Description
`apply_withdrawals` reads: [1](#0-0) 

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

Only `state.builders[builder_index].balance` is reduced by the *capped* amount `min(withdrawal.amount, builder_balance)`. The `Withdrawal` object itself — the one placed into `state.payload_expected_withdrawals`, which the execution layer is contractually bound to honor per the spec's own note ("any execution payload that has the corresponding block as parent beacon block is required to honor these withdrawals in the execution layer") — still carries the original, uncapped `withdrawal.amount`.

This is confirmed by the repo's own test suite: [2](#0-1) 

In `test_builder_withdrawal_insufficient_balance`, a builder has only 1 ETH available, `builder_pending_withdrawals` requests 5 ETH. The verified post-state is: `builders[0].balance == 0` (deduction capped to the 1 ETH available) while `withdrawal.amount == 5 ETH` (the uncapped, requested amount) is what ends up in `payload_expected_withdrawals`. The helper `assert_process_withdrawals` even encodes this discrepancy explicitly: [3](#0-2) 

```python
    for builder_index, total_amount in builder_withdrawals.items():
        pre_balance = pre_state.builders[builder_index].balance
        post_balance = state.builders[builder_index].balance
        # Builder withdrawals cap at available balance (spec uses min())
        expected_deduction = min(total_amount, pre_balance)
        assert post_balance == pre_balance - expected_deduction, ...
```

So the CL only ever burns `min(requested, balance)` Gwei from the builder's account, but the `Withdrawal.amount` field that the EL is spec-required to pay to `fee_recipient`/`execution_address` is the full, uncapped `total_amount`. The equality that should hold — "Gwei paid out by the execution layer for a withdrawal == Gwei actually removed from the corresponding CL account" — is broken.

The spec's own prose acknowledges a related, narrower saturation risk elsewhere but frames it as an accepted invariant of "the execution layer mints the full committed amount regardless" for balance deductions performed *at commit time*: [4](#0-3) 

However, that note is about `decrease_balance` saturating to zero for normal validators between commitment and deduction slots — it does not apply to the builder-withdrawal branch, where the `min()` capping and the resulting under-collateralized `Withdrawal.amount` occur in the exact same call, deterministically, whenever a builder's pending-withdrawal queue requests more than the builder's current balance (e.g., because multiple pending withdrawals or payments have already reduced it, or a bid consumed balance since the request was queued).

### Impact Explanation
This breaks the "Gwei created or paid to a non-owner" invariant at Critical severity: the execution layer will mint/pay `withdrawal.amount` (the uncapped, requested value) to `fee_recipient`, while the beacon state has only actually removed `min(withdrawal.amount, builder_balance)` Gwei from the corresponding builder account. The difference is value created out of nothing and paid to whichever address controls `fee_recipient` in the `BuilderPendingWithdrawal` — which is attacker-controllable, since `fee_recipient` is independent of the builder's own `execution_address`: [5](#0-4) 

Any builder who queues withdrawal requests that exceed their (possibly already-depleted) balance — trivially achievable since multiple pending withdrawals/payments can be queued against the same balance before processing — causes the EL to pay out more than was ever backed on the CL side.

### Likelihood Explanation
High: this requires no coalition, no client bug, and no majority stake — a single builder simply needs to accumulate `builder_pending_withdrawals`/`builder_pending_payments` entries whose sum exceeds its current `balance` (e.g., by losing balance to a prior withdrawal, payment, or bid before a later queued withdrawal is processed), which is a normal, spec-permitted state reachable purely through builder-controlled actions (bidding and requesting withdrawals).

### Recommendation
Cap the `amount` field written into the `Withdrawal` object itself (not just the CL-side balance deduction) to `min(requested_amount, builder_balance)` at the point the withdrawal is constructed (in the builder-withdrawal-generation function feeding `apply_withdrawals`), so that the value committed to the execution layer never exceeds what is actually deducted from `state.builders[builder_index].balance`. The two must be kept as one and the same equality throughout `get_builder_withdrawals`, `apply_withdrawals`, and `payload_expected_withdrawals`.

### Proof of Concept
Using the existing test `test_builder_withdrawal_insufficient_balance` in `tests/core/pyspec/eth_consensus_specs/test/gloas/block_processing/test_process_withdrawals.py:114-156`:
- Builder balance = 1 ETH.
- `builder_pending_withdrawals` requests 5 ETH.
- After `process_withdrawals`: `builders[0].balance == 0` (only 1 ETH actually removed), but the emitted `Withdrawal.amount == 5 ETH` in `state.payload_expected_withdrawals`, which the execution layer is required to pay to `fee_recipient`.
- Net effect: 4 ETH paid by the EL with no corresponding CL-side deduction — Gwei minted and paid to `fee_recipient`, breaking the CL/EL supply invariant.

### Citations

**File:** specs/gloas/beacon-chain.md (L1923-1932)
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

**File:** tests/core/pyspec/eth_consensus_specs/test/gloas/block_processing/test_process_withdrawals.py (L114-156)
```python
@with_gloas_and_later
@spec_state_test
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

**File:** tests/core/pyspec/eth_consensus_specs/test/helpers/withdrawals.py (L721-729)
```python
    # Check builder balance decreases
    for builder_index, total_amount in builder_withdrawals.items():
        pre_balance = pre_state.builders[builder_index].balance
        post_balance = state.builders[builder_index].balance
        # Builder withdrawals cap at available balance (spec uses min())
        expected_deduction = min(total_amount, pre_balance)
        assert post_balance == pre_balance - expected_deduction, (
            f"Builder {builder_index} balance must decrease by withdrawal amount"
        )
```

**File:** tests/core/pyspec/eth_consensus_specs/test/gloas/block_processing/test_process_withdrawals.md (L33-34)
```markdown
| (in `builder_pending_withdrawals`) `.fee_recipient`      | `ExecutionAddress`                                                  | `Bytes20`. Withdrawal destination. Independent of builder's own `execution_address`.                                                                                 | Withdrawal destination                  |
| (in `builder_pending_withdrawals`) `.amount`             | `Gwei`                                                              | `Uint64`. Requested amount. Actual = `min(amount, builder.balance)`.                                                                                                 | Withdrawal amount                       |
```
