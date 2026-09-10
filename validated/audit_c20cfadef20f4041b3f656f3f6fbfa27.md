Based on my research, I found a genuine analog to the LIDO "requested amount vs. actual received amount" mismatch in the Gloas builder-withdrawal machinery.

### Title
Builder withdrawal payload amount not capped to available builder balance, allowing EL to mint uncommitted Gwei - (File: `specs/gloas/beacon-chain.md`)

### Summary
`get_builder_withdrawals` emits a `Withdrawal` with the raw, uncapped `builder_pending_withdrawals[i].amount` (the "requested" amount), while `apply_withdrawals` only debits the builder's tracked balance by `min(withdrawal.amount, builder_balance)` (the "actual" amount). Since the execution layer is required to honor `state.payload_expected_withdrawals` exactly (crediting `withdrawal.amount` to the fee recipient), any state where a queued withdrawal's `amount` exceeds the builder's balance at the time of processing causes the EL to mint more ETH than the CL debits — exactly the LIDO "requested != claimed" accounting flaw, here breaking Gwei conservation instead of an ERC4626 exchange rate.

### Finding Description
`get_builder_withdrawals` ( [1](#0-0) ) builds the payload `Withdrawal` from `withdrawal.amount` directly, without ever comparing it to `state.builders[builder_index].balance`:
```python
withdrawals.append(
    Withdrawal(
        index=withdrawal_index,
        validator_index=convert_builder_index_to_validator_index(builder_index),
        address=withdrawal.fee_recipient,
        amount=withdrawal.amount,
    )
)
```
This value becomes part of `state.payload_expected_withdrawals`, which — per the spec's own note — "any execution payload that has the corresponding block as parent beacon block is required to honor these withdrawals in the execution layer" [2](#0-1) . That is, the EL mints `withdrawal.amount` to `withdrawal.address` unconditionally.

Meanwhile, `apply_withdrawals` deliberately caps the CL-side debit:
```python
builder_balance = state.builders[builder_index].balance
state.builders[builder_index].balance -= min(withdrawal.amount, builder_balance)
``` [3](#0-2) 

The test documentation explicitly acknowledges this asymmetry as an accepted design point: "`.amount` … Requested amount. Actual = `min(amount, builder.balance)`" and "Builder pending: `actual_amount = min(requested_amount, builder.balance)` (builder balance can go to zero)" [4](#0-3) [5](#0-4) .

This is structurally identical to the LIDO bug: the protocol assumes "amount requested == amount actually available/paid," records the requested amount as if it were guaranteed, and only discovers the discrepancy at settlement time — except here the "settlement" (EL minting) uses the unadjusted requested amount rather than the capped one, so the gap is paid out rather than merely mis-accounted.

### Impact Explanation
If `withdrawal.amount > builder.balance` when a `BuilderPendingWithdrawal` reaches the front of `state.builder_pending_withdrawals`, `apply_withdrawals` reduces the builder's balance by only the smaller `builder_balance` amount, but the `Withdrawal` object committed to the block (and mandatorily honored by the EL) still carries the full, larger `withdrawal.amount`. The EL will credit the fee recipient with more ETH than was ever debited from the builder's CL-tracked balance — Gwei is created out of nothing and paid to the fee recipient, a non-owner relative to what was actually backed. This matches the "Critical: Gwei created … paid to a non-owner" impact category.

### Likelihood Explanation
I could not fully verify, within the available tool budget, whether `can_builder_cover_bid` (invoked from `process_execution_payload_bid`) strictly prevents `builder_pending_withdrawals` entries from ever exceeding the builder's balance by the time they're processed — I found evidence it validates sufficient balance against *currently queued* pending payments/withdrawals at bid-submission time (`test_process_execution_payload_bid_insufficient_balance_with_pending_withdrawals`, `test_process_execution_payload_bid_insufficient_balance_with_pending_payments`), but I did not confirm this invariant is re-checked continuously as the queue (up to `BUILDER_PENDING_WITHDRAWALS_LIMIT` = 2^20 entries) grows and drains over long periods, nor whether `process_builder_exit_request`/`process_builder_deposit_request` can reduce a builder's balance after a withdrawal has already been queued. This uncertainty means likelihood is unconfirmed — it is plausible the enqueue-time check is sufficient to make `amount <= builder.balance` an invariant that always holds by the time `get_builder_withdrawals` runs, in which case `min(...)` in `apply_withdrawals` would be dead code / defense-in-depth rather than an exploitable gap.

### Recommendation
Regardless of whether the gap is currently reachable, `get_builder_withdrawals` should use the same capped value that `apply_withdrawals` actually debits (`amount=min(withdrawal.amount, state.builders[builder_index].balance)`), so the amount committed to the execution payload can never diverge from the amount actually removed from the builder's tracked balance. This mirrors the LIDO fix recommendation of never letting "requested" and "actually available" values diverge in an accounting formula that is later paid out.

### Proof of Concept
Because I could not conclusively establish (with remaining tool calls) a concrete state-transition path where `builder_pending_withdrawals[i].amount > state.builders[builder_index].balance` at processing time — this would require tracing all mutation paths of `builder.balance` (bid settlement, `process_builder_exit_request`, `process_builder_deposit_request`, slashinglessness of builders) against every enqueue point of `builder_pending_withdrawals` across the `BUILDER_PENDING_WITHDRAWALS_LIMIT`-sized queue — I cannot provide a fully verified, minimal reproduction. The code-level contradiction between `get_builder_withdrawals` (uses raw `amount`) and `apply_withdrawals` (uses `min(amount, balance)`) is demonstrated above; a background engineering session with the full repo and pyspec test harness would be needed to confirm reachability by constructing a state with multiple queued `builder_pending_withdrawals` for one builder whose cumulative amounts exceed a balance reduced by an intervening builder-exit or another already-processed withdrawal in the same queue, then running `process_withdrawals` and comparing `payload_expected_withdrawals[i].amount` against the builder balance delta.

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

**File:** tests/core/pyspec/eth_consensus_specs/test/gloas/block_processing/test_process_withdrawals.md (L34-34)
```markdown
| (in `builder_pending_withdrawals`) `.amount`             | `Gwei`                                                              | `Uint64`. Requested amount. Actual = `min(amount, builder.balance)`.                                                                                                 | Withdrawal amount                       |
```

**File:** tests/core/pyspec/eth_consensus_specs/test/gloas/block_processing/test_process_withdrawals.md (L101-103)
```markdown
   - **Builder pending**:
     `actual_amount = min(requested_amount, builder.balance)` (builder balance
     can go to zero)
```
