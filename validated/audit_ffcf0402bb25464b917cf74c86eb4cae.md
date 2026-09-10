Confirmed: `verify_execution_payload_envelope` asserts `hash_tree_root(payload.withdrawals) == hash_tree_root(state.payload_expected_withdrawals)` [1](#0-0) , which is populated directly from `get_expected_withdrawals` via `update_payload_expected_withdrawals` [2](#0-1) . This is exactly the field the execution layer uses to actually credit Gwei to the destination address (the beacon chain "mints" balance to the EL address named in each `Withdrawal.amount`). The `amount` recorded for a builder pending withdrawal is the full requested amount, uncapped by the builder's actual balance [3](#0-2) , while `apply_withdrawals` only decrements `state.builders[builder_index].balance` by `min(withdrawal.amount, builder_balance)` [4](#0-3) . This is corroborated by the existing spec test, which explicitly documents the mismatch: a builder with 1 ETH gets a withdrawal `amount` field of 5 ETH while its internal balance only drops by 1 ETH (capped to 0) [5](#0-4) .

### Title
Builder pending withdrawal amount is not capped to builder balance before being committed to `payload_expected_withdrawals`, minting uncollateralized Gwei on the execution layer - (File: specs/gloas/beacon-chain.md)

### Summary
`get_builder_withdrawals` copies `builder_pending_withdrawals[i].amount` verbatim into the `Withdrawal.amount` field that becomes part of `state.payload_expected_withdrawals`, without capping it to the builder's actual `balance` [6](#0-5) . This committed amount is exactly what the execution payload is required to match via `hash_tree_root(payload.withdrawals) == hash_tree_root(state.payload_expected_withdrawals)` [1](#0-0) , and it is this amount the EL uses to credit the destination address. Meanwhile `apply_withdrawals` only removes `min(withdrawal.amount, builder_balance)` from `state.builders[i].balance` [4](#0-3) .

### Finding Description
When a builder's `builder_pending_withdrawals` entry requests more Gwei than `state.builders[builder_index].balance` currently holds (e.g. after the builder's balance was reduced by other withdrawals/slashing-equivalent effects processed earlier in the same or prior blocks, or due to multiple pending withdrawal entries queued against the same shrinking balance), the beacon state still emits a `Withdrawal` object whose `amount` equals the full, uncapped requested amount. That `Withdrawal.amount` flows into `state.payload_expected_withdrawals`, which is the value cryptographically committed via `hash_tree_root` equality against the execution payload's withdrawals list. The execution layer, honoring this commitment, credits the full (uncapped) amount to the destination `fee_recipient` address — creating Gwei that was never backed by the builder's on-chain balance, because the beacon-state-side accounting (`state.builders[i].balance`) only decreases by the smaller, actually-available amount. This breaks the equality that every Gwei paid out to an address must correspond to an equal decrease in a real, existing balance.

This is the structural analog of the reported M-10 issue but inverted in effect: instead of the *actual* transfer silently becoming 0 while accounting proceeds as if it succeeded, here the accounting deliberately clamps to the safe/available amount while the *externally paid* amount (the one actually delivered on-chain via the EL) is left uncapped — net result is Gwei created and paid to a recipient with no offsetting decrease anywhere in the protocol's balances.

### Impact Explanation
This falls under "Gwei created... paid to a non-owner": the execution layer will credit an address with `Withdrawal.amount` Gwei that exceeds what was actually debited from the builder's balance in the beacon state. Because `payload_expected_withdrawals` is part of the state that all honest, spec-following nodes compute deterministically, this is not a client bug or a foreign-key/coalition issue — it is inherent in how `get_builder_withdrawals` constructs the amount field versus how `apply_withdrawals` debits it. This is Critical/High-severity in nature (Gwei minted without backing), though its practical triggerability depends on how the protocol is meant to guarantee `builder_pending_withdrawals[i].amount <= builder.balance` at request-creation time elsewhere in the spec (see Likelihood).

### Likelihood Explanation
I was not able to fully verify, within tool-call/context limits, whether some other spec function (e.g. wherever `BuilderPendingWithdrawal` requests are appended to `state.builder_pending_withdrawals`, or `compute_exit_epoch_and_update_churn`-style processing for builders) already enforces `amount <= builder.balance` at insertion time, or re-validates it immediately before consumption, which would make this unreachable in practice. The existing test suite explicitly exercises and asserts the "insufficient balance" scenario as expected/passing behavior with the full requested amount kept in the withdrawal [5](#0-4) , suggesting the protocol authors are aware of and treat this specific behavior as intended (i.e., builder balance capped at withdrawal apply time is the "fix", but the committed `amount` itself is not re-derived from the capped value). Given this is treated as a deliberate design decision documented and tested, I flag this with caveats rather than full confidence — it should be validated against how `builder_pending_withdrawals` entries are created (out of scope of what I could inspect in this pass) to confirm whether an over-request can genuinely occur, or whether upstream invariants prevent `amount` from ever exceeding `balance` at enqueue time.

### Recommendation
Either (a) cap the emitted `Withdrawal.amount` in `get_builder_withdrawals` to `min(withdrawal.amount, state.builders[builder_index].balance)` so the committed payload amount can never exceed the deducted balance, or (b) require `apply_withdrawals`/verification to reject payloads whose committed amount for a builder withdrawal is not consistent with the pre-decrement `builder.balance`, ensuring the amount actually paid by the EL is always bounded by real on-chain builder funds.

### Proof of Concept
1. Beacon state has `state.builders[B].balance = 1 ETH`.
2. `state.builder_pending_withdrawals` contains an entry `{builder_index: B, amount: 5 ETH, fee_recipient: X}`.
3. During block processing, `get_builder_withdrawals` emits `Withdrawal(validator_index=convert(B), address=X, amount=5 ETH)` unchanged [3](#0-2) .
4. This is stored in `state.payload_expected_withdrawals` via `update_payload_expected_withdrawals` [2](#0-1) .
5. `apply_withdrawals` decrements `state.builders[B].balance` by only `min(5 ETH, 1 ETH) = 1 ETH`, setting it to 0 [4](#0-3) .
6. `verify_execution_payload_envelope` requires the EL payload's withdrawals to hash-match `state.payload_expected_withdrawals`, i.e., the EL must credit address X with 5 ETH [1](#0-0) .
7. Net effect: address X receives 5 ETH on the execution layer, but the beacon-chain-side builder balance only decreased by 1 ETH — 4 ETH of Gwei created with no matching debit anywhere in `state`, exactly matching the test's asserted "expected" behavior [7](#0-6) .

### Citations

**File:** specs/gloas/fork-choice.md (L684-689)
```markdown
    # Verify the execution payload is valid
    assert payload.slot_number == state.slot
    assert payload.parent_hash == state.latest_block_hash
    assert payload.timestamp == compute_time_at_slot(state, state.slot)
    assert hash_tree_root(payload.withdrawals) == hash_tree_root(state.payload_expected_withdrawals)
    assert execution_engine.verify_and_notify_new_payload(
```

**File:** specs/gloas/beacon-chain.md (L1802-1834)
```markdown
##### New `get_builder_withdrawals`

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

**File:** specs/gloas/beacon-chain.md (L1936-1941)
```markdown
```python
def update_payload_expected_withdrawals(
    state: BeaconState, withdrawals: Sequence[Withdrawal]
) -> None:
    state.payload_expected_withdrawals = Withdrawals(data=withdrawals)
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
