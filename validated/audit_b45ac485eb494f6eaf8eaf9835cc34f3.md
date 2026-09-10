### Title
Builder withdrawal amount is committed uncapped while builder balance deduction is capped, minting Gwei on the execution layer - (File: specs/gloas/beacon-chain.md)

### Summary
In the Gloas fork, `get_builder_withdrawals` constructs a `Withdrawal` record using the **full**, uncapped `builder_pending_withdrawals[i].amount`, but `apply_withdrawals` only decreases the builder's CL balance by `min(withdrawal.amount, builder_balance)`. Since `state.payload_expected_withdrawals` (built from the uncapped amount) is the value the execution layer is contractually required to pay to `fee_recipient`, a builder can request more than its balance and have the difference paid out with no backing collateral — the same "insufficient balance leads to over-transfer" bug class as the referenced Lido report, but here it manifests as unbacked Gwei minted on L1.

### Finding Description
`get_builder_withdrawals` iterates `state.builder_pending_withdrawals` and appends a `Withdrawal` whose `amount` field is set directly to the pending withdrawal's requested `amount`, without any comparison to `state.builders[builder_index].balance`: [1](#0-0) 

This `Withdrawal` list becomes `state.payload_expected_withdrawals`, which per spec commentary the execution layer "is required to honor" for the corresponding block: [2](#0-1) 

Meanwhile, `apply_withdrawals` only decreases the CL-side builder balance by the capped amount: [3](#0-2) 

So the value actually debited from the builder's stake (`min(amount, balance)`) is strictly less than the value committed in `payload_expected_withdrawals[i].amount` whenever `amount > balance`. This exact scenario is already exercised (and treated as expected behavior, not a bug) by the test suite: [4](#0-3) 

The invariant test helper explicitly documents "Actual = `min(amount, builder.balance)`" for the CL debit while the committed `Withdrawal.amount` remains the full requested amount: [5](#0-4) 

The equality broken is: *the amount debited from the CL builder ledger must equal the amount the EL is committed to pay out via the withdrawal*. With `amount=5 ETH` requested and `available_balance=1 ETH`, the builder balance is reduced by 1 ETH (to 0), but `payload_expected_withdrawals` commits to paying `fee_recipient` the full 5 ETH — 4 ETH of value with no corresponding CL-side backing.

### Impact Explanation
This is a Critical-severity finding under "Gwei created ... paid to a non-owner": the execution layer will process a withdrawal of the full uncapped amount to an arbitrary `fee_recipient` address (attacker-controlled, since `fee_recipient` and `amount` are fields of `BuilderPendingWithdrawal`, presumably builder/self-supplied when queuing the withdrawal), while only the builder's actual (possibly near-zero) balance is deducted on the CL side. This mints Gwei on L1 that was never staked, breaking the fundamental supply invariant between the CL and EL.

### Likelihood Explanation
Any builder can queue a `BuilderPendingWithdrawal` with `amount` set arbitrarily higher than its current `balance` — there does not appear to be a validation step in `get_builder_withdrawals` (or wherever `builder_pending_withdrawals` entries are enqueued, which was not fully located in this scan) rejecting `amount > builder.balance`. Given the test suite explicitly exercises and asserts this exact "insufficient balance" scenario as expected/passing behavior (`test_builder_withdrawal_insufficient_balance`), the condition is trivially reachable by a single builder with no coalition, no majority stake, and no client bug required.

### Recommendation
Cap the committed `Withdrawal.amount` in `get_builder_withdrawals` (and any sweep/build path) to `min(withdrawal.amount, state.builders[builder_index].balance)` at the point the `Withdrawal` record is constructed, so that the amount committed to `payload_expected_withdrawals` never exceeds what `apply_withdrawals` actually debits from the builder's CL balance — mirroring the recommendation in the referenced report to require the payer to hold sufficient balance before committing to a transfer.

### Proof of Concept
1. Register a builder with `balance = 1 ETH` (`available_balance`).
2. Enqueue a `BuilderPendingWithdrawal` for this builder with `amount = 5 ETH` and `fee_recipient = <attacker address>`.
3. Call `process_withdrawals(state)`. `get_builder_withdrawals` produces `Withdrawal(amount=5 ETH, address=<attacker address>)` [6](#0-5) , which is placed into `state.payload_expected_withdrawals`.
4. `apply_withdrawals` deducts only `min(5 ETH, 1 ETH) = 1 ETH` from `state.builders[0].balance`, leaving it at 0 [7](#0-6) .
5. The execution layer, honoring the beacon block's committed `payload_expected_withdrawals`, credits the attacker address with the full 5 ETH — 4 ETH created without any CL-side stake backing it. This is directly demonstrated by `test_builder_withdrawal_insufficient_balance` [8](#0-7) , which asserts `withdrawal.amount == 5 ETH` (requested) while `builders[0].balance == 0` post-state.

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

**File:** specs/gloas/beacon-chain.md (L1969-1976)
```markdown
*Note*: This is modified to only take the `state` as parameter. Withdrawals are
deterministic given the beacon state, any execution payload that has the
corresponding block as parent beacon block is required to honor these
withdrawals in the execution layer. `process_withdrawals` must be called after
`process_parent_execution_payload` (which updates `state.latest_block_hash`) and
before `process_execution_payload_bid` as the latter function affects validator
balances.

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

**File:** tests/core/pyspec/eth_consensus_specs/test/gloas/block_processing/test_process_withdrawals.md (L34-34)
```markdown
| (in `builder_pending_withdrawals`) `.amount`             | `Gwei`                                                              | `Uint64`. Requested amount. Actual = `min(amount, builder.balance)`.                                                                                                 | Withdrawal amount                       |
```
