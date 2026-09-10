### Title
Order-dependent sync-committee reward/penalty accounting lets a duplicate-selected low-balance validator receive un-earned Gwei - (File: `specs/altair/beacon-chain.md`)

### Summary
`process_sync_aggregate` in `specs/altair/beacon-chain.md` applies per-duty-slot rewards and penalties to sync-committee participants by iterating `committee_indices` in a fixed order and calling `increase_balance`/`decrease_balance` slot-by-slot. Because a validator can legitimately be selected into the same sync committee more than once (`get_next_sync_committee_indices` samples with replacement), and `decrease_balance` floors at zero instead of allowing a true negative balance, the *order* in which that validator's duplicate slots are processed changes the net Gwei it ends up with for the exact same participation pattern. This mirrors the reported StWSX bug class: an amount is computed/applied in a sequence where an earlier mutation (balance clipped to zero) silently discards value that a later mutation (a full reward) then adds back, producing more Gwei than the reward formula intends.

### Finding Description
`process_sync_aggregate` computes a fixed `participant_reward` and applies it per duty slot: [1](#0-0) 

`decrease_balance` has underflow protection that clips to zero rather than going negative: [2](#0-1) 

If a validator index appears twice in `committee_indices` (duplicate sync-committee selection, common on `MAINNET` preset), and the two entries have opposite participation bits (one "missed", one "participated"), the two operations should cancel out (reward - penalty ≈ 0) *if* the validator's pre-epoch balance is large enough to absorb the debit. But when the validator's balance is at or below `participant_reward` (e.g. balance == 0, or reduced from other penalties/slashing), the outcome depends on which duty slot is processed first:

- Reward-slot-first, penalty-slot-second: `increase_balance` credits the reward, then `decrease_balance` removes the same amount → balance returns to its pre-state value (correct, no value created).
- Penalty-slot-first, reward-slot-second: `decrease_balance` on a balance of 0 clips at 0 (losing nothing, since there's nothing to take), then `increase_balance` unconditionally adds the full reward → the validator ends up strictly higher than pre-state, even though it only did half of its expected duty.

This exact "penalty-then-reward-vs-reward-then-penalty" ordering asymmetry is captured and explicitly documented in the associated test file — the test even skips the standard reward-validation helper because "it doesn't handle the balance computation order inside the for loop": [3](#0-2) 

The setup helper confirms the duplicate-index/positions mechanics used to trigger the asymmetry: [4](#0-3) 

The same `process_sync_aggregate` control flow (increase/decrease per committee slot, in committee order) is inherited unchanged into later forks that reuse Altair's sync-aggregate processing (no override of this loop appears in Bellatrix/Capella/Deneb/Electra beacon-chain specs).

### Impact Explanation
This breaks the equality that a validator's balance change for an epoch must equal the reward/penalty formula's intended net value for its actual participation — i.e., Gwei is effectively created and paid to a validator beyond what the protocol's issuance schedule dictates, purely as a function of duty-slot ordering rather than participation. Per the rules this falls in the "Gwei created ... or paid to a non-owner" category. All spec-following clients compute the same (wrong) result deterministically, so there is no consensus split, but real value is fabricated for the affected validator at each occurrence.

### Likelihood Explanation
Requires two conditions to coincide in the same epoch: (1) a validator selected more than once into the sync committee (statistically expected periodically under `MAINNET` sampling-with-replacement, given `SYNC_COMMITTEE_SIZE = 512` relative to the active validator set), and (2) that validator's balance being at or below `participant_reward` at the time (e.g., a freshly-slashed, near-ejection, or newly-activated low-balance validator — more reachable post-EIP-7251 where balances can be small/variable). No attacker action, malicious peer, or coalition is needed; it is a deterministic consequence of the spec's own sequencing and `decrease_balance`'s floor-at-zero behavior. Overall likelihood is low but non-zero and grows as validator balances become more variable.

### Recommendation
Aggregate a single validator's participation across all of its duty-slot occurrences in `committee_indices` (or accumulate a net signed delta before calling `increase_balance`/`decrease_balance`), rather than applying `increase_balance`/`decrease_balance` slot-by-slot per occurrence. This removes the order dependency between rewards and penalties for validators selected multiple times, analogous to computing the net user/DAO split once before mutating the shared balance, as recommended for the StWSX fix.

### Proof of Concept
Using `_run_sync_committee_selected_twice` semantics: pick a validator selected twice in `state.current_sync_committee` (duplicate on `MAINNET` preset), set `state.balances[validator_index] = 0`, set `committee_bits` so the *first* occurrence (lower slot position) is non-participating (`False`) and the *second* occurrence is participating (`True`). Run `process_sync_aggregate`:
1. First occurrence processed: `decrease_balance(state, validator_index, participant_reward)` on balance 0 → clipped, stays 0 (no actual debit possible).
2. Second occurrence processed: `increase_balance(state, validator_index, participant_reward)` → balance becomes `participant_reward > 0`.

Result: `state.balances[validator_index] > 0` even though the validator only participated in one of its two duty slots — confirmed by the existing test assertion `assert state.balances[validator_index] > 0` in `test_sync_committee_rewards_duplicate_committee_zero_balance_only_participate_second_one`, contrasted with the reverse-order test `test_sync_committee_rewards_duplicate_committee_zero_balance_only_participate_first_one` which correctly yields `state.balances[validator_index] == 0` for the same participation pattern in the opposite slot order. [5](#0-4)

### Citations

**File:** specs/altair/beacon-chain.md (L681-694)
```markdown
    # Apply participant and proposer rewards
    all_pubkeys = [v.pubkey for v in state.validators]
    committee_indices = [
        ValidatorIndex(all_pubkeys.index(pubkey)) for pubkey in state.current_sync_committee.pubkeys
    ]
    for participant_index, participation_bit in zip(
        committee_indices, sync_aggregate.sync_committee_bits, strict=True
    ):
        if participation_bit:
            increase_balance(state, participant_index, participant_reward)
            increase_balance(state, get_beacon_proposer_index(state), proposer_reward)
        else:
            decrease_balance(state, participant_index, participant_reward)
```
```

**File:** specs/phase0/beacon-chain.md (L1603-1614)
```markdown
#### `decrease_balance`

```python
def decrease_balance(state: BeaconState, index: ValidatorIndex, delta: Gwei) -> None:
    """
    Decrease the validator balance at index ``index`` by ``delta``, with underflow protection.
    """
    if delta > state.balances[index]:
        state.balances[index] = Gwei(0)
    else:
        state.balances[index] -= delta
```
```

**File:** tests/core/pyspec/eth_consensus_specs/test/altair/block_processing/sync_aggregate/test_process_sync_aggregate.py (L223-262)
```python
def _run_sync_committee_selected_twice(
    spec,
    state,
    pre_balance,
    participate_first_position,
    participate_second_position,
    skip_reward_validation=False,
):
    committee_indices = compute_committee_indices(state)

    # Preconditions of this test case
    assert is_duplicate_sync_committee(committee_indices)

    committee_size = len(committee_indices)
    committee_bits = [False] * committee_size

    # Find duplicate indices that get selected twice
    dup = {v for v in committee_indices if list(committee_indices).count(v) == 2}
    assert len(dup) > 0
    validator_index = dup.pop()
    positions = [i for i, v in enumerate(committee_indices) if v == validator_index]
    committee_bits[positions[0]] = participate_first_position
    committee_bits[positions[1]] = participate_second_position

    # Set validator's balance
    state.balances[validator_index] = pre_balance
    state.validators[validator_index].effective_balance = min(
        pre_balance - pre_balance % spec.EFFECTIVE_BALANCE_INCREMENT,
        spec.MAX_EFFECTIVE_BALANCE,
    )

    yield from run_successful_sync_committee_test(
        spec,
        state,
        committee_indices,
        committee_bits,
        skip_reward_validation=skip_reward_validation,
    )

    return validator_index
```

**File:** tests/core/pyspec/eth_consensus_specs/test/altair/block_processing/sync_aggregate/test_process_sync_aggregate.py (L265-301)
```python
@with_altair_and_later
@with_presets([MAINNET], reason="to create duplicate committee")
@spec_state_test
def test_sync_committee_rewards_duplicate_committee_zero_balance_only_participate_first_one(
    spec, state
):
    validator_index = yield from _run_sync_committee_selected_twice(
        spec,
        state,
        pre_balance=0,
        participate_first_position=True,
        participate_second_position=False,
    )

    # The validator gets reward first (balance > 0) and then gets the same amount of penalty (balance == 0)
    assert state.balances[validator_index] == 0


@with_altair_and_later
@with_presets([MAINNET], reason="to create duplicate committee")
@spec_state_test
def test_sync_committee_rewards_duplicate_committee_zero_balance_only_participate_second_one(
    spec, state
):
    # Skip `validate_sync_committee_rewards` because it doesn't handle the balance computation order
    # inside the for loop
    validator_index = yield from _run_sync_committee_selected_twice(
        spec,
        state,
        pre_balance=0,
        participate_first_position=False,
        participate_second_position=True,
        skip_reward_validation=True,
    )

    # The validator gets penalty first (balance is still 0) and then gets reward (balance > 0)
    assert state.balances[validator_index] > 0
```
