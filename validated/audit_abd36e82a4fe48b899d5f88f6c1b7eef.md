### Title
Builder pending withdrawals commit an uncapped amount to the execution layer while `apply_withdrawals` only deducts the capped balance, minting unbacked Gwei - (File: specs/gloas/beacon-chain.md)

### Summary
In `get_builder_withdrawals`, the `Withdrawal.amount` placed into `state.payload_expected_withdrawals` is taken directly from the pending withdrawal's requested `amount` field, without capping it to the builder's actual `balance`. `apply_withdrawals` then deducts only `min(withdrawal.amount, builder_balance)` from the builder's CL balance. Since the execution layer is required to honor the full amount recorded in the committed withdrawal, this produces a mismatch: the EL mints/pays out the full requested amount while the CL only debits the smaller, balance-capped amount — the exact "cap applied at settlement, but the value committed/rolled forward is not adjusted" pattern described in the LinearDistributor report.

### Finding Description
`get_builder_withdrawals` builds each `Withdrawal` from `state.builder_pending_withdrawals` using the raw requested `withdrawal.amount`, with no `min()` against `state.builders[builder_index].balance`: [1](#0-0) 

This list becomes `state.payload_expected_withdrawals` via `update_payload_expected_withdrawals`, and per the spec's own note, "any execution payload that has the corresponding block as parent beacon block is required to honor these withdrawals in the execution layer": [2](#0-1) 

Meanwhile, `apply_withdrawals` (the CL-side balance mutation) caps the actual deduction to `min(withdrawal.amount, builder_balance)`: [3](#0-2) 

This is structurally identical to the LinearDistributor bug: `distributed` (paid-out amount) is capped by an available resource (`balance` in Malt, `builder_balance` here), but the value that is propagated/committed forward (`currentlyVested`/`previouslyVested` in Malt, the committed `Withdrawal.amount` here) is never adjusted down to match what was actually backed. The equality broken is: `Gwei paid out on EL == Gwei debited on CL`. Here, when `withdrawal.amount > builder.balance`, the EL is committed to pay the full `withdrawal.amount` to `fee_recipient`, while the CL state only reflects a debit of `builder.balance` (down to 0). The difference (`withdrawal.amount - builder.balance`) is Gwei created with no corresponding CL-side source.

The existing test suite documents this exact behavior as expected rather than flagging it as a bug: [4](#0-3) 

### Impact Explanation
This falls under "Gwei created ... paid to a non-owner" — Critical. If a builder's pending withdrawal amount (set when the payment was queued, potentially long before settlement, e.g., via `settle_builder_payment`) exceeds the builder's balance at withdrawal-sweep time (balance can shrink via other withdrawals, prior sweeps or partial builder withdrawals processed earlier in the same or a previous slot), the committed `Withdrawal.amount` sent to the EL still equals the original (larger) requested amount. The EL is spec-obligated to execute that exact commitment, meaning it credits the fee recipient the full uncapped amount while the CL only ever debited the smaller, capped amount from the builder — a net inflation of ETH not backed by any CL balance reduction.

### Likelihood Explanation
Reaching the divergence requires a builder's `balance` to be less than the sum of amount(s) recorded in its `builder_pending_withdrawals` entries at the moment `get_builder_withdrawals`/`apply_withdrawals` run. Since builders can have multiple queued payments and their balance can be reduced between the time a `BuilderPendingPayment` is queued (`settle_builder_payment`) and when the withdrawal sweep actually executes (e.g., another withdrawal or a settled payment draining the balance first), this state is reachable purely through spec-following participants without needing a malicious peer, coalition, or client bug — it only requires ordinary payment/withdrawal timing, matching the "no balance available" precondition that the existing test suite already exercises intentionally.

### Recommendation
Cap the amount recorded in the `Withdrawal` itself (used for both the EL commitment and any downstream accounting) to the builder's available balance at construction time in `get_builder_withdrawals`, mirroring the pattern already used for `get_builders_sweep_withdrawals` (which sets `amount=builder.balance` directly) and for partial-validator withdrawals (`min(balance - MIN_ACTIVATION_BALANCE, withdrawal.amount)`):
```python
amount = min(withdrawal.amount, state.builders[builder_index].balance)
withdrawals.append(
    Withdrawal(
        index=withdrawal_index,
        validator_index=convert_builder_index_to_validator_index(builder_index),
        address=withdrawal.fee_recipient,
        amount=amount,
    )
)
```
This makes the committed EL payment equal to the actual CL-side deduction, preserving the invariant that no Gwei is created beyond what is debited from the builder's balance.

### Proof of Concept
1. Builder `B` has `balance = 1 ETH` and a `BuilderPendingWithdrawal` entry with `amount = 5 ETH` requesting payment to address `A` (this can occur because `settle_builder_payment` queued the withdrawal earlier, and `B`'s balance was subsequently reduced by another withdrawal or slashing-adjacent event before this sweep runs).
2. `get_builder_withdrawals` produces `Withdrawal(validator_index=convert(B), address=A, amount=5 ETH)` — unclamped, as confirmed by the existing test `test_builder_withdrawal_insufficient_balance` which asserts `withdrawal_amounts_builders={builder_index: 5 ETH}` while `builder_balances={builder_index: 0}` post-state: [5](#0-4) .
3. `apply_withdrawals` executes `state.builders[B].balance -= min(5 ETH, 1 ETH)`, i.e., deducts only 1 ETH, leaving `balance = 0`.
4. The block containing this committed `Withdrawal(amount=5 ETH)` is delivered to the execution layer, which mints/pays 5 ETH to `A`.
5. Net result: 5 ETH left the system on the EL side, but only 1 ETH was ever debited from any CL-tracked balance — 4 ETH created out of nothing, paid to `A`.

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
