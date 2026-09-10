### Title
Builder pending withdrawal amount is not capped to available balance before being committed to the execution payload, permitting the execution layer to mint Gwei the beacon state never removes - (File: specs/gloas/beacon-chain.md)

### Summary
`get_builder_withdrawals` copies the *requested* `amount` from `state.builder_pending_withdrawals` directly into the `Withdrawal` object that becomes part of `state.payload_expected_withdrawals` — the value the execution layer is required to honor — without capping it to the builder's actual balance. `apply_withdrawals`, which mutates CL state, does cap the balance decrease to `min(withdrawal.amount, builder_balance)`. This produces two different numbers for what is supposed to be the same value: the amount promised/executed on the EL side, and the amount actually removed from the CL side.

### Finding Description
In `get_builder_withdrawals`, each `Withdrawal` committed to the block is built with the raw requested amount: [1](#0-0) 

This `Withdrawal` (uncapped `amount`) is exactly what is placed in `state.payload_expected_withdrawals`, which the note in `process_withdrawals` explicitly states the execution layer "is required to honor" for the corresponding execution payload: [2](#0-1) 

Meanwhile, `apply_withdrawals` — the function that mutates the CL-side accounting — deliberately saturates the deduction at the available balance rather than the committed withdrawal amount: [3](#0-2) 

So when `builder.balance < withdrawal.amount`, the CL only deducts `builder.balance` (down to 0), while the committed `Withdrawal.amount` — the number the EL mints to `withdrawal.address` — remains the full, uncapped requested amount. This is confirmed by the repo's own test comments, which explicitly document the committed amount staying at the full requested value while the balance saturates at zero: [4](#0-3) [5](#0-4) 

This breaks the equality that must hold for every withdrawal: `Gwei minted by the EL to the recipient == Gwei removed from the source's CL balance`. It is the direct spec-level analog of the fee-on-transfer bug class described in the report: an amount is committed/paid out that does not match the amount actually deducted from the paying party's ledger.

Whether `builder.balance` can legitimately fall below a previously-queued `builder_pending_withdrawals[i].amount` at processing time (e.g., via multiple queued withdrawals for the same builder draining balance before later ones are processed, or slashing-equivalent balance reduction for builders) determines exploitability; I was not able to fully confirm within the available spec text whether `builder_pending_withdrawals` insertion enforces `amount <= balance` at the time of insertion and whether balance can decrease between insertion and processing (e.g. multiple pending withdrawals against the same builder summing to more than balance). The `test_builder_withdrawal_insufficient_balance` test explicitly exercises and asserts this exact scenario as valid input (a builder with 1 ETH balance and a queued withdrawal of 5 ETH), which strongly suggests the spec/tests treat under-collateralized pending withdrawals as a reachable, in-scope state rather than a precondition violation.

### Impact Explanation
If reachable, this is a Critical-severity issue under the stated impact categories: "Gwei created, destroyed, or paid to a non-owner." Specifically, Gwei is created out of thin air — the execution layer mints/pays the full committed withdrawal amount to `fee_recipient` while the beacon state's builder balance is only reduced by the smaller, available amount, inflating total supply.

### Likelihood Explanation
Likelihood depends entirely on whether the state where `builder.balance < sum of its queued builder_pending_withdrawals[].amount` is reachable through normal, spec-following block processing (e.g., a builder queuing multiple withdrawal requests that jointly exceed its balance, or its balance being reduced by other mechanisms between the time a withdrawal is queued and when it is processed). The existing unit tests (`test_builder_withdrawal_insufficient_balance`, `test_builder_withdrawal_insufficient_balance_realistic_bounds`) construct this exact state directly via test helpers, which the spec code accepts and processes without any assertion/error, indicating the case is not rejected as invalid by `process_withdrawals` itself.

### Recommendation
Cap the `amount` field of the `Withdrawal` object created in `get_builder_withdrawals` to `min(withdrawal.amount, state.builders[builder_index].balance)` (using the current, correctly-accounted-for balance — accounting for withdrawals already appended in the same `withdrawals` list, similar to how `get_pending_partial_withdrawals`/`get_validators_sweep_withdrawals` compute `balance` via `get_balance_after_withdrawals`) so that the value committed to the execution payload always equals the value actually deducted from the builder's CL balance in `apply_withdrawals`.

### Proof of Concept
1. A builder `B` has `balance = 1 ETH`.
2. `state.builder_pending_withdrawals` contains an entry `{builder_index: B, fee_recipient: X, amount: 5 ETH}` (as constructed by `prepare_process_withdrawals`/exercised by `test_builder_withdrawal_insufficient_balance`).
3. `process_withdrawals(state)` runs:
   - `get_builder_withdrawals` emits `Withdrawal(validator_index=convert(B), address=X, amount=5 ETH)` into `state.payload_expected_withdrawals`.
   - `apply_withdrawals` executes `state.builders[B].balance -= min(5 ETH, 1 ETH)`, leaving `builder.balance = 0`.
4. The execution layer, honoring `state.payload_expected_withdrawals`, credits `X` with `5 ETH`.
5. Net effect: `5 ETH` credited on the EL side, but only `1 ETH` removed from the CL side — `4 ETH` created with no corresponding backing, violating Gwei conservation. [6](#0-5) [7](#0-6)

### Citations

**File:** specs/gloas/beacon-chain.md (L1805-1834)
```markdown
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

**File:** specs/gloas/beacon-chain.md (L1969-1975)
```markdown
*Note*: This is modified to only take the `state` as parameter. Withdrawals are
deterministic given the beacon state, any execution payload that has the
corresponding block as parent beacon block is required to honor these
withdrawals in the execution layer. `process_withdrawals` must be called after
`process_parent_execution_payload` (which updates `state.latest_block_hash`) and
before `process_execution_payload_bid` as the latter function affects validator
balances.
```

**File:** tests/core/pyspec/eth_consensus_specs/test/gloas/block_processing/test_process_withdrawals.py (L116-130)
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
```

**File:** tests/core/pyspec/eth_consensus_specs/test/gloas/block_processing/test_process_withdrawals.md (L34-34)
```markdown
| (in `builder_pending_withdrawals`) `.amount`             | `Gwei`                                                              | `Uint64`. Requested amount. Actual = `min(amount, builder.balance)`.                                                                                                 | Withdrawal amount                       |
```
