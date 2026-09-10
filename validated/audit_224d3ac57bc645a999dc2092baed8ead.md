This is `update_next_withdrawal_validator_index`, defined in `specs/capella/beacon-chain.md` and reused unmodified through Electra and Gloas.

### Title
`update_next_withdrawal_validator_index` reuses `Withdrawal.validator_index == MAX_WITHDRAWALS_PER_PAYLOAD` trigger without excluding BUILDER_INDEX_FLAG-tagged entries in Gloas - ([File: specs/capella/beacon-chain.md])

### Summary
`specs/capella/beacon-chain.md#L513-L529` computes the next validator-sweep starting index using `(withdrawals[-1].validator_index + 1) % len(state.validators)` whenever the payload contains exactly `MAX_WITHDRAWALS_PER_PAYLOAD` withdrawals. This function is inherited unmodified into Gloas, where `Withdrawal.validator_index` can now also encode a `BuilderIndex` with `BUILDER_INDEX_FLAG` (`2**40`) set [1](#0-0) . This is exactly the shared-container mismatch pattern in the report: one code path (`get_builder_withdrawals`/`apply_withdrawals`) knows how to special-case the "different asset" (builder-flagged index), but the reused `update_next_withdrawal_validator_index` does not.

### Finding Description
Gloas explicitly documents this: the note in `apply_withdrawals` states `is_builder_index` must be checked before treating `withdrawal.validator_index` as a real validator index [2](#0-1) , and a regression test explicitly enumerates the previously-existing bug: "the spec used `(withdrawals[-1].validator_index + 1) % num_validators`, but builder withdrawals have `BUILDER_INDEX_FLAG` (2^40) set in `validator_index`, producing incorrect results" [3](#0-2) .

This was mitigated by reserving one slot for the validator sweep so the payload can never be filled to exactly `MAX_WITHDRAWALS_PER_PAYLOAD` entries consisting purely of builder-flagged withdrawals (PR #4832), verified by `test_full_builder_payload_reserves_sweep_slot` [4](#0-3) . However, `update_next_withdrawal_validator_index` itself was never patched to filter or convert builder indices — the mitigation is purely a capacity/slot-reservation guarantee elsewhere in `get_expected_withdrawals`, not a fix inside the mutator function that reads `withdrawals[-1].validator_index`. The mutator still blindly takes the flagged index whenever `len(withdrawals) == MAX_WITHDRAWALS_PER_PAYLOAD`.

### Impact Explanation
If any future change to Gloas's withdrawal-slot accounting (e.g. builder pending + partial + builder-sweep queues sized so the reserved slot assumption is violated, or a future fork adds another withdrawal source competing for the same `MAX_WITHDRAWALS_PER_PAYLOAD` budget) allows the last element of the withdrawals list to be a builder-flagged entry while the list length equals `MAX_WITHDRAWALS_PER_PAYLOAD`, `state.next_withdrawal_validator_index` would be corrupted to a value derived from `BUILDER_INDEX_FLAG | builder_index`, which is `>= 2**40`. Every subsequent block's `get_validators_sweep_withdrawals` indexes `state.validators[validator_index]` with `validator_index = state.next_withdrawal_validator_index`, causing spec-following nodes to either throw an `IndexError` (chain halt / invalid-block divergence between clients that clamp vs. crash) or, if wrapped via modulo differently by different client implementations, select a different validator for sweep withdrawal than another honest client would — a duty-selection/state divergence that breaks the equality that all spec-following nodes agree on the same next-sweep validator and the same withdrawal set. This is a High/Critical-class node-divergence risk, contingent entirely on the currently-safe slot-reservation invariant holding.

### Likelihood Explanation
Currently **not exploitable**: `get_builder_withdrawals` and `get_builders_sweep_withdrawals` are both capped at `MAX_WITHDRAWALS_PER_PAYLOAD - 1` [5](#0-4) [6](#0-5) , and `get_pending_partial_withdrawals` is likewise capped below `MAX_WITHDRAWALS_PER_PAYLOAD - 1` [7](#0-6) , so the last of exactly `MAX_WITHDRAWALS_PER_PAYLOAD` withdrawals is guaranteed to come from `get_validators_sweep_withdrawals`, which never emits a builder-flagged index. This is confirmed by the passing regression test [8](#0-7) . So the described divergence is not currently reachable given present spec invariants — it is a latent fragility (function-level fix missing, invariant-level mitigation only) rather than a live bug.

### Recommendation
`update_next_withdrawal_validator_index` (specs/capella/beacon-chain.md, inherited by Gloas) should be made robust independent of the slot-reservation invariant by either: (1) explicitly filtering out builder-flagged entries before selecting `withdrawals[-1]` for the validator-index calculation, mirroring how `apply_withdrawals` checks `is_builder_index` before treating an index as a builder, or (2) asserting within the function that the last withdrawal's index is not builder-flagged, so any future violation of the slot-reservation invariant fails loudly (deterministically the same way on all clients) rather than silently corrupting `next_withdrawal_validator_index`.

### Proof of Concept
Not applicable — this is a specification robustness issue, not exploitable under the current invariants. As shown by `test_full_builder_payload_reserves_sweep_slot`, the reserved-slot invariant currently prevents the last withdrawal in a full-length payload from ever being builder-flagged, so no concrete PoC state transition exists today that triggers the corruption; the finding is that the guarantee lives in the caller's capacity math rather than in the mutator itself, making it a single-point-of-failure that could silently regress in future spec changes.

### Citations

**File:** specs/gloas/beacon-chain.md (L547-555)
```markdown
### Index flags

*Note*: The `BUILDER_INDEX_FLAG` is a bitwise flag which indicates that a
`ValidatorIndex` should be treated as a `BuilderIndex`. This exists so that the
same `Withdrawal` container can be used for validators and builders.

| Name                 | Value           |
| -------------------- | --------------- |
| `BUILDER_INDEX_FLAG` | `Uint64(2**40)` |
```

**File:** specs/gloas/beacon-chain.md (L1810-1811)
```markdown
    withdrawals_limit = MAX_WITHDRAWALS_PER_PAYLOAD - 1
    assert len(prior_withdrawals) <= withdrawals_limit
```

**File:** specs/gloas/beacon-chain.md (L1846-1847)
```markdown
    withdrawals_limit = MAX_WITHDRAWALS_PER_PAYLOAD - 1
    assert len(prior_withdrawals) <= withdrawals_limit
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

**File:** tests/core/pyspec/eth_consensus_specs/test/gloas/block_processing/test_process_withdrawals.py (L1019-1043)
```python
def test_full_builder_payload_reserves_sweep_slot(spec, state):
    """
    Test that builder withdrawals reserve one slot for validator sweep.

    This test verifies the fix from https://github.com/ethereum/consensus-specs/pull/4832
    which reserves one slot in MAX_WITHDRAWALS_PER_PAYLOAD for validator sweep withdrawals.

    Previous Bug (before the fix):
        When all MAX_WITHDRAWALS_PER_PAYLOAD slots were filled by builder withdrawals,
        next_withdrawal_validator_index was calculated incorrectly. The spec used
        (withdrawals[-1].validator_index + 1) % num_validators, but builder withdrawals
        have BUILDER_INDEX_FLAG (2^40) set in validator_index, producing incorrect results.
        See also: https://github.com/ethereum/consensus-specs/pull/4835

    Input State:
        - builder_pending_withdrawals: MAX_WITHDRAWALS_PER_PAYLOAD entries
        - All validator balances capped (no validator withdrawals)
        - next_withdrawal_validator_index: Known starting value

    Output State Verified:
        - Only MAX_WITHDRAWALS_PER_PAYLOAD - 1 builder withdrawals processed
        - One slot reserved for validator sweep
        - next_withdrawal_validator_index: Correctly advanced by MAX_VALIDATORS_PER_WITHDRAWALS_SWEEP
          (sweep runs even though no validator withdrawals are produced due to capped balances)
    """
```

**File:** tests/core/pyspec/eth_consensus_specs/test/gloas/block_processing/test_process_withdrawals.py (L1104-1111)
```python
    # Assert the fix: next_withdrawal_validator_index is correct
    # Before the fix, it would have been buggy_result (completely wrong due to BUILDER_INDEX_FLAG)
    assert state.next_withdrawal_validator_index == correct_result, (
        f"Spec produces {state.next_withdrawal_validator_index}, expected {correct_result}"
    )
    assert state.next_withdrawal_validator_index != buggy_result, (
        f"Bug fix verified: spec no longer produces buggy result {buggy_result}"
    )
```

**File:** specs/electra/beacon-chain.md (L1366-1370)
```markdown
    withdrawals_limit = min(
        len(prior_withdrawals) + MAX_PENDING_PARTIALS_PER_WITHDRAWALS_SWEEP,
        MAX_WITHDRAWALS_PER_PAYLOAD - 1,
    )
    assert len(prior_withdrawals) <= withdrawals_limit
```
