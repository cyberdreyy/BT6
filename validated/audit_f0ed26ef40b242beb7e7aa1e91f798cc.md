### Title
Uncapped builder withdrawal amount committed to payload while balance deduction is capped, creating unbacked Gwei - ([File: specs/gloas/beacon-chain.md])

### Summary
`get_builder_withdrawals` copies the raw, uncapped `amount` field from `state.builder_pending_withdrawals` into the `Withdrawal` that is committed to `state.payload_expected_withdrawals` (and from there into the execution payload the block commits to). `apply_withdrawals` then only decreases the builder's CL-side balance by `min(withdrawal.amount, builder_balance)`. Because the execution layer is required to honor the committed withdrawal amount unconditionally, any case where a pending builder withdrawal's `amount` exceeds the builder's current balance results in the EL crediting the full requested amount to `fee_recipient` while the CL only debits the (smaller) available balance — creating Gwei with no corresponding source.

### Finding Description
In `get_builder_withdrawals`: [1](#0-0) 
the `Withdrawal.amount` field is set directly to `withdrawal.amount` from the pending-withdrawal queue, with no clamping to the builder's actual balance.

This committed withdrawal is placed into `state.payload_expected_withdrawals`, and per the spec note, "any execution payload that has the corresponding block as parent beacon block is required to honor these withdrawals in the execution layer" — i.e., the EL will unconditionally credit `withdrawal.address` with `withdrawal.amount`. [2](#0-1) 

However, the actual CL-side balance deduction in `apply_withdrawals` is capped: [3](#0-2) 
```python
if is_builder_index(withdrawal.validator_index):
    builder_index = convert_validator_index_to_builder_index(withdrawal.validator_index)
    builder_balance = state.builders[builder_index].balance
    state.builders[builder_index].balance -= min(withdrawal.amount, builder_balance)
```

If `withdrawal.amount > builder_balance` (e.g. balance decreased between the time the withdrawal was queued and processed — via `process_pending_consolidations`-style effects, slashing, or simply multiple queued withdrawals for the same builder summing above balance), the committed `Withdrawal.amount` that the EL will pay out remains the full requested amount, while the CL only removes `builder_balance` (the smaller amount) from `state.builders[builder_index].balance`.

This is confirmed by the repository's own test: [4](#0-3) 
Here a builder with 1 ETH balance has a pending withdrawal for 5 ETH. The post-state verifies `builders[0].balance == 0` (only 1 ETH deducted) while `withdrawal_amounts_builders={builder_index: withdrawal_amount}` confirms the *committed* withdrawal amount in `payload_expected_withdrawals` is still the full 5 ETH — the exact amount the EL is obligated to pay to the fee recipient.

The equality broken is: `Σ(EL payout amounts) == Σ(CL balance decreases)`. Here the EL pays 5 ETH but the CL only decreases 1 ETH, producing 4 ETH of Gwei with no corresponding decrease anywhere in the system — directly analogous to the Nibiru finding's "amount taken in ≠ amount actually burned/backed" double-accounting break, except here it manifests as a single mismatched debit/credit rather than a double fee, and it is the CL amount that under-deducts relative to what the EL commits to pay.

### Impact Explanation
This breaks Gwei conservation between the consensus layer and execution layer: Gwei is credited to `fee_recipient` on the EL side without a matching decrease of the builder's balance on the CL side. Per the rules, "a Gwei created ... paid to a non-owner" is a Critical-severity outcome, since it results in unbacked value entering the execution-layer account, permanently inflating supply.

### Likelihood Explanation
This is a normal-flow, unprivileged scenario: it only requires that a builder's pending-withdrawal `amount` in the queue exceed the builder's balance at the time of processing — which can arise naturally (e.g. a builder's balance is reduced by slashing or other processing between when the withdrawal is queued and when it is swept) without needing any other party's cooperation, majority stake, or a client bug. The spec's own test suite explicitly exercises and asserts this exact insufficient-balance scenario as expected behavior, showing that spec-following nodes will reach this state and the payload amount/balance-decrease mismatch is deterministic and reproducible.

### Recommendation
Clamp the `amount` written into the committed `Withdrawal` in `get_builder_withdrawals` (and in `get_builders_sweep_withdrawals`) to `min(pending_withdrawal.amount, state.builders[builder_index].balance)` at the time the withdrawal is queued into `payload_expected_withdrawals`, so the amount the EL is obligated to pay always matches the amount actually debited from the builder's balance, eliminating the possibility of unbacked Gwei.

### Proof of Concept
1. Builder 0 has `balance = 1_000_000_000` Gwei (1 ETH).
2. `state.builder_pending_withdrawals` contains an entry for builder 0 with `amount = 5_000_000_000` Gwei (5 ETH) (as set up in `test_builder_withdrawal_insufficient_balance`). [5](#0-4) 
3. `process_withdrawals` runs: `get_builder_withdrawals` copies `amount=5 ETH` unmodified into `payload_expected_withdrawals`.
4. `apply_withdrawals` executes `state.builders[0].balance -= min(5 ETH, 1 ETH)`, leaving `balance = 0`.
5. Post-state: `payload_expected_withdrawals[0].amount == 5 ETH` while `builders[0].balance` only decreased by 1 ETH — confirmed by the test's assertions `builder_balances={0: 0}` and `withdrawal_amounts_builders={0: 5 ETH}`. [6](#0-5) 
6. The execution layer, which must honor the committed withdrawals list, will credit 5 ETH to `fee_recipient`, while only 1 ETH was ever removed from the builder — 4 ETH of Gwei materializes with no corresponding source.

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
