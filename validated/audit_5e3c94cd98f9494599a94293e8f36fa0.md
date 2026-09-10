### Title
Builder withdrawal amount is unconditionally committed to the execution layer while the CL only debits the capped balance, creating Gwei out of thin air - ([File: specs/gloas/beacon-chain.md])

### Summary
In the Gloas fork, `get_builder_withdrawals` builds a `Withdrawal` object using the *raw requested* `builder_pending_withdrawals[i].amount`, without capping it to the builder's actual balance. This `Withdrawal.amount` is placed into `state.payload_expected_withdrawals`, which the spec explicitly states the execution layer *must* honor as a payment. However, `apply_withdrawals` only decreases the builder's CL-side balance by `min(withdrawal.amount, builder_balance)`. If a builder's balance is smaller than the amount recorded in a queued `BuilderPendingWithdrawal`, the EL mints the full requested amount to `fee_recipient` while the CL removes only the smaller, capped amount from `state.builders[...].balance` — the difference is Gwei created with no corresponding source, breaking the ETH conservation invariant. This is the direct analog of the fee-on-transfer bug in the original report, where the amount "promised" for distribution and the amount actually available/moved diverge; here the direction is even more severe because it results in inflation (Gwei minted that was never actually held), rather than merely a later-claimant shortfall.

### Finding Description
`get_builder_withdrawals` (specs/gloas/beacon-chain.md:1805-1834) constructs each `Withdrawal` as:
```python
Withdrawal(
    index=withdrawal_index,
    validator_index=convert_builder_index_to_validator_index(builder_index),
    address=withdrawal.fee_recipient,
    amount=withdrawal.amount,   # <-- raw, uncapped requested amount
)
``` [1](#0-0) 

This withdrawal is placed unmodified into `state.payload_expected_withdrawals` by `update_payload_expected_withdrawals`, and the spec states this list is deterministic given the state and "any execution payload that has the corresponding block as parent beacon block is required to honor these withdrawals in the execution layer" [2](#0-1)  — i.e. the EL must mint the full `withdrawal.amount` Gwei to `withdrawal.address`.

Meanwhile, `apply_withdrawals` deducts only the capped amount from the builder's balance:
```python
if is_builder_index(withdrawal.validator_index):
    builder_index = convert_validator_index_to_builder_index(withdrawal.validator_index)
    builder_balance = state.builders[builder_index].balance
    state.builders[builder_index].balance -= min(withdrawal.amount, builder_balance)
``` [3](#0-2) 

If `withdrawal.amount > builder_balance` at processing time, the EL mints `withdrawal.amount` (uncapped) but the CL only removes `builder_balance` (the whole balance) from the builder registry. The gap `withdrawal.amount - builder_balance` is Gwei that leaves the tracked total supply on the execution side without ever having existed as tracked stake on the consensus side.

This scenario is directly reachable by honest, spec-following block production: a `BuilderPendingWithdrawal` is created with a fixed `amount` at one point (e.g. when a builder requests an exit or payment), and the builder's `balance` can subsequently decrease (e.g., multiple pending withdrawals queued for the same builder, or a builder-sweep/other pending withdrawal draining balance first, given "Builder pending withdrawals" are processed before "Builder sweep withdrawals" per the processing order). By the time the queued pending withdrawal is dequeued and applied, the recorded `amount` can exceed the then-current `balance`. No malicious peer, coalition, or client bug is required — only ordinary sequencing of protocol-permitted events (multiple queued builder payments/withdrawals against the same builder).

The repo's own test suite documents this exact capped-vs-uncapped mismatch as expected behavior:
```
- withdrawal.amount: 5 ETH (requested amount)
- builders[0].balance: 0 (deduction capped to available balance)
``` [4](#0-3) 
and the assertion helper explicitly caps the *balance* check while the withdrawal amount used for payload output remains uncapped:
```python
expected_deduction = min(total_amount, pre_balance)
assert post_balance == pre_balance - expected_deduction, (...)
``` [5](#0-4) 

### Impact Explanation
This breaks the Gwei-conservation equality: total Gwei paid out via `payload_expected_withdrawals` (which the EL must mint to `fee_recipient`/`execution_address`) can exceed the total Gwei actually removed from the beacon state's tracked balances (`state.builders[*].balance`). This is a Critical-class issue per the rubric ("Gwei created ... or paid to a non-owner", "a payload executed or paid that the block did not commit to correctly") because the CL state and EL state diverge on how much ETH was actually backed by stake, inflating supply without any corresponding burn/lock elsewhere.

### Likelihood Explanation
Reachable without any adversarial coalition: any sequence of legitimate builder withdrawal requests where a builder's balance drops below a previously-queued pending withdrawal amount (e.g., two pending withdrawals queued for the same builder that together exceed the balance, or a sweep/pending-withdrawal processed first reducing balance) triggers the mismatch. The unit tests in the repository (`test_builder_withdrawal_insufficient_balance`, `test_builder_withdrawal_insufficient_balance_realistic_bounds`) already demonstrate the state configuration is reachable and the spec code executes without any assertion failure.

### Recommendation
`get_builder_withdrawals` should record the *capped* amount (`min(withdrawal.amount, state.builders[builder_index].balance)`, accounting for amounts already reserved by earlier withdrawals in the same payload) as the `Withdrawal.amount`, so the value the EL is required to mint matches exactly what `apply_withdrawals` removes from the builder's balance — mirroring the recommended fix in the original report (verify actual amount moved equals the amount credited/committed) rather than trusting the unconditioned "requested" amount.

### Proof of Concept
1. Builder `B` has `balance = 1 ETH`.
2. A `BuilderPendingWithdrawal` for `B` is enqueued with `amount = 5 ETH` (a legitimate, protocol-permitted request made while balance was still sufficient, e.g. balance later reduced by an earlier-processed withdrawal item in the same or a prior slot).
3. `process_withdrawals` runs: `get_builder_withdrawals` emits `Withdrawal(address=B.fee_recipient, amount=5 ETH)` into `payload_expected_withdrawals` [6](#0-5) .
4. `apply_withdrawals` executes `state.builders[B].balance -= min(5 ETH, 1 ETH)`, leaving `balance = 0` [7](#0-6) .
5. The execution layer, following the committed `payload_expected_withdrawals`, mints the full `5 ETH` to `fee_recipient`.
6. Net result: `5 ETH` paid out on the EL side, only `1 ETH` removed from CL-tracked builder balance — `4 ETH` created with no backing source, exactly as reproduced by the repository's own test `test_builder_withdrawal_insufficient_balance` [4](#0-3) .

### Citations

**File:** specs/gloas/beacon-chain.md (L1804-1834)
```markdown
```python
def get_builder_withdrawals(
    state: BeaconState,
    withdrawal_index: WithdrawalIndex,
    prior_withdrawals: Sequence[Withdrawal],
) -> Tuple[Sequence[Withdrawal], WithdrawalIndex, Uint64]:
    withdrawals_limit = MAX_WITHDRAWALS_PER_PAYLOAD - 1
    assert len(prior_withdrawals) <= withdrawals_limit

    processed_count = Uint64(0)
    withdrawals: list[Withdrawal] = []
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

**File:** specs/gloas/beacon-chain.md (L1967-1975)
```markdown
##### Modified `process_withdrawals`

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

**File:** tests/core/pyspec/eth_consensus_specs/test/helpers/withdrawals.py (L722-729)
```python
    for builder_index, total_amount in builder_withdrawals.items():
        pre_balance = pre_state.builders[builder_index].balance
        post_balance = state.builders[builder_index].balance
        # Builder withdrawals cap at available balance (spec uses min())
        expected_deduction = min(total_amount, pre_balance)
        assert post_balance == pre_balance - expected_deduction, (
            f"Builder {builder_index} balance must decrease by withdrawal amount"
        )
```
