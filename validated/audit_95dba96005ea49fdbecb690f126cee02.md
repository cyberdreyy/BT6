### Title
Builder withdrawal amount is not capped to builder's actual balance before being committed to the execution payload - ([File: specs/gloas/beacon-chain.md])

### Summary
`get_builder_withdrawals` in `specs/gloas/beacon-chain.md` builds `Withdrawal` objects using the raw, uncapped `withdrawal.amount` taken from `state.builder_pending_withdrawals`, without checking it against the builder's actual `balance`. This is the same bug class as the reported `TokenSaleETH.sol`/`TokenSaleUSDB.sol` issue: the code should assign `min(requested_amount, available_balance)` as the final amount, but instead commits the (possibly larger) requested amount.

### Finding Description
`get_builder_withdrawals` emits a `Withdrawal` entry using the withdrawal's `amount` field verbatim, with no comparison to the builder's balance: [1](#0-0) 

Later, when the state is actually mutated, `apply_withdrawals` *does* clamp the balance deduction with `min(withdrawal.amount, builder_balance)`: [2](#0-1) 

This produces exactly the asymmetry described in the report: for the same computed amount, one code path (the `Withdrawal.amount` committed into `state.payload_expected_withdrawals`, which is what the execution layer is required to pay out) uses the *uncapped* requested value, while the other path (the beacon-state balance bookkeeping) uses the *capped* value. If a builder's `balance` is smaller than the queued `builder_pending_withdrawals[i].amount` at the time the sweep runs (e.g. because the builder's balance was already reduced by an earlier withdrawal processed in the same sweep, by a builder exit, or by any other balance-reducing event between when the pending withdrawal was queued and when it is processed), the committed `Withdrawal.amount` sent to the execution layer will be **larger** than what the beacon state actually deducts from the builder.

The spec explicitly states that the execution layer is required to honor the withdrawals list exactly as committed by the block, and that balance deductions happen immediately via `apply_withdrawals` to preserve the "total supply" invariant between the commitment and its execution: [3](#0-2) 

Because `get_builder_withdrawals` never re-derives the amount from `min(withdrawal.amount, state.builders[builder_index].balance)` (unlike the analogous, correctly-capped sweep logic in `get_builders_sweep_withdrawals`, which withdraws exactly `builder.balance`), the block can commit the execution layer to paying out more Gwei than the beacon chain actually removes from the builder's tracked balance.

### Impact Explanation
This breaks the equality "Gwei paid out to a non-owner must equal Gwei removed from the paying entity's balance." The execution layer pays `withdrawal.amount` (uncapped) to `fee_recipient`, while the beacon state only decreases the builder's balance by `min(withdrawal.amount, builder_balance)`. When the builder's balance is insufficient, this creates Gwei that is paid out but never actually backed/removed from any account on the consensus side — a form of Gwei created and paid to a party the block did not fully "own" the funds for. This matches the Critical impact category ("Gwei created, destroyed, or paid to a non-owner").

### Likelihood Explanation
This requires a builder's `balance` to fall below the amount already queued in `builder_pending_withdrawals` for that builder before the sweep processes it — a state that spec-following block production can create honestly (e.g., multiple pending withdrawals queued for the same builder that cumulatively exceed the balance, since each pending withdrawal is generated independently by `process_builder_pending_payments`/related logic without re-checking against a shrinking running balance during the sweep). No malicious peer, coalition, or client bug is required — this can occur purely from ordinary, spec-compliant queue growth once balance is spent down by an earlier processed pending withdrawal, or between when the pending withdrawal amount is computed and when it's swept, so likelihood is non-negligible, though it depends on whether builder pending payment queuing logic elsewhere in the spec already guarantees `sum(pending withdrawal amounts) <= balance` at all times — I was not able to fully verify the invariant-establishing code (`process_builder_pending_payments` and where `builder_pending_withdrawals` entries are queued) within available search iterations, so this should be checked against the queuing logic before treating it as fully confirmed.

### Recommendation
In `get_builder_withdrawals`, compute the emitted `amount` as `min(withdrawal.amount, state.builders[builder_index].balance)` (and/or decrement a running "remaining balance" tracker across multiple queued withdrawals for the same builder within the same sweep), mirroring the capping already done for `get_pending_partial_withdrawals` (`min(balance - MIN_ACTIVATION_BALANCE, withdrawal.amount)`) and for the immediate builder-sweep case (`amount=builder.balance`). This ensures the amount committed to the execution payload never exceeds what `apply_withdrawals` will actually deduct from the builder's balance.

### Proof of Concept
1. Two `BuilderPendingWithdrawal` entries exist in `state.builder_pending_withdrawals` for the same `builder_index`, each requesting `amount = builder.balance` (e.g. both queued while the builder had a full balance, before either was processed).
2. `get_builder_withdrawals` iterates both entries and, since it does not track a "remaining balance," emits two `Withdrawal` objects, each with `amount = builder.balance` — i.e., the committed total is `2 * builder.balance`.
3. `apply_withdrawals` processes them sequentially: first withdrawal deducts `min(builder.balance, builder.balance) = builder.balance`, leaving builder balance at `0`; second withdrawal deducts `min(builder.balance, 0) = 0`.
4. Result: `state.payload_expected_withdrawals` commits the execution layer to paying out `2 * builder.balance` total to (potentially two different) `fee_recipient` addresses, while the beacon state balance for that builder only ever held/deducted `builder.balance` — i.e., `builder.balance` worth of Gwei is paid out with no backing balance removed from any account, matching the "Gwei paid to a non-owner without a source" bug class from the report.

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

**File:** specs/gloas/beacon-chain.md (L1967-1981)
```markdown
##### Modified `process_withdrawals`

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
```
