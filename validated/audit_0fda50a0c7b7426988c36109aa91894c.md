### Title
Builder-payment quorum computed against a total active balance that can shift after the payment's weight was locked in - ([File: specs/gloas/beacon-chain.md])

### Summary
`process_builder_pending_payments` checks a payment's already-fixed `weight` (an aggregate of attesters' effective balances captured during block processing in the *previous* epoch) against `get_builder_payment_quorum_threshold(state)`, which is computed from `get_total_active_balance(state)` at the moment epoch processing runs — i.e., *after* the same epoch's registry/effective-balance updates have already mutated the active-validator set. This is the same numerator/denominator timing mismatch as the Nouns Builder `Governor` quorum bug: the "vote weight" (numerator) is frozen at an earlier point than the "total supply" (denominator) used to judge it.

### Finding Description
`get_builder_payment_quorum_threshold` derives the quorum purely from the current state's total active balance: [1](#0-0) 

`payment.weight` is accumulated earlier, during `process_attestation`, strictly from the effective balances of same-slot attesters as recorded at block-processing time in the prior epoch: [2](#0-1) 

`process_builder_pending_payments` then compares this frozen `weight` against a quorum computed with `get_total_active_balance(state)` evaluated at the time epoch processing reaches this step — after `process_registry_updates`, `process_pending_deposits`, `process_pending_consolidations`, and `process_effective_balance_updates` have already run for the same epoch transition and can change which validators are active and what their effective balances are: [3](#0-2) 

`get_total_active_balance`/`get_total_balance` simply sum `state.validators[index].effective_balance` over the *current* active set, with no snapshotting mechanism tied to when the weight was recorded: [4](#0-3) 

Exactly like the Governor case — where `quorumVotes` is fixed at `propose()` time but the vote is later evaluated against `getPastVotes`, letting tokens minted afterward count toward a stale quorum snapshot — here the payment's `weight` numerator is fixed against the effective-balance snapshot at attestation time, while the `quorum` denominator is recomputed later against a state that may have shrunk (validator exits processed by `process_registry_updates`, or effective-balance decreases processed earlier in the same epoch transition). If the total active balance drops between the epoch in which the weight was accumulated and the epoch-boundary processing that checks it, the same fixed `weight` now represents a larger fraction of a smaller total, so `payment.weight >= quorum` can become true even though the payment never actually achieved 60% support of the total active balance that existed when attesters cast their same-slot attestations.

### Impact Explanation
If the builder-payment quorum is satisfied only because the denominator (`get_total_active_balance`) shrank between weight accumulation and quorum evaluation — rather than because attesters genuinely representing 60% of *any single consistent* balance snapshot supported the payload — `state.builder_pending_withdrawals.append(payment.withdrawal)` fires and authorizes a builder payment that the protocol's stated 60%-of-active-balance rule did not actually satisfy. This is a builder payment "misdirected" relative to its intended quorum guarantee — the class of impact explicitly listed as High (a builder payment or withdrawal misdirected, doubled, or escaped).

### Likelihood Explanation
This requires the total active balance to shrink measurably between the last slot where the payment's weight could be updated (previous epoch) and the epoch boundary where `process_builder_pending_payments` runs, and it only closes a narrow margin near the 60% threshold (the same profile as the original Nouns Builder finding, which is a narrow-window, low-frequency issue rather than something trivially and cheaply repeatable). Per-epoch churn limits (`get_activation_churn_limit`, `get_exit_churn_limit`, slashing correlation penalties) bound how much the total active balance can move in a single epoch, so the achievable quorum "discount" is small under normal validator-set sizes, but it is non-zero and grows in relative terms precisely in the same low-total-balance regimes the original report calls out (e.g., early in a validator set's life, or during periods of concentrated exits/slashings).

### Recommendation
Compute `get_builder_payment_quorum_threshold` using a total-active-balance snapshot taken at the same point the payment's weight was being accumulated (e.g., cache the previous epoch's total active balance, analogous to caching total supply at proposal creation in the Governor analog), rather than recomputing it from the post-registry-update state at the moment `process_builder_pending_payments` runs. Alternatively, evaluate quorum before `process_registry_updates`/`process_effective_balance_updates` mutate the active set for the epoch being finalized.

### Proof of Concept
1. At epoch `N-1`, validators attest same-slot to a builder's payload; `process_attestation` accumulates `payment.weight` using their effective balances at that time, e.g. `weight = W`, while `get_total_active_balance(state) = T` (so `W < 0.6*T // SLOTS_PER_EPOCH`, i.e. below quorum as intended).
2. During the epoch-N transition, before `process_builder_pending_payments` runs, `process_registry_updates` exits/ejects a set of validators (or `process_slashings`/`process_effective_balance_updates` reduce effective balances), shrinking total active balance to `T' < T`.
3. `process_builder_pending_payments` computes `quorum = get_builder_payment_quorum_threshold(state)` using the new, smaller `T'`. If `W >= 0.6*T' // SLOTS_PER_EPOCH` even though `W < 0.6*T // SLOTS_PER_EPOCH`, the payment is now treated as having met quorum and its withdrawal is appended to `state.builder_pending_withdrawals`, even though the attesters who actually cast weight `W` never represented 60% of the active balance that existed while they were attesting.

### Citations

**File:** specs/gloas/beacon-chain.md (L1416-1423)
```markdown
def get_builder_payment_quorum_threshold(state: BeaconState) -> Uint64:
    """
    Calculate the quorum threshold for builder payments.
    """
    per_slot_balance = get_total_active_balance(state) // Uint64(SLOTS_PER_EPOCH)
    quorum = per_slot_balance * BUILDER_PAYMENT_THRESHOLD_NUMERATOR
    return Uint64(quorum // BUILDER_PAYMENT_THRESHOLD_DENOMINATOR)
```
```

**File:** specs/gloas/beacon-chain.md (L1664-1677)
```markdown
def process_builder_pending_payments(state: BeaconState) -> None:
    """
    Processes the builder pending payments from the previous epoch.
    """
    quorum = get_builder_payment_quorum_threshold(state)
    for payment in state.builder_pending_payments[:SLOTS_PER_EPOCH]:
        if payment.weight >= quorum:
            state.builder_pending_withdrawals.append(payment.withdrawal)

    old_payments = state.builder_pending_payments[SLOTS_PER_EPOCH:]
    state.builder_pending_payments[:SLOTS_PER_EPOCH] = old_payments
    new_payments = [BuilderPendingPayment.empty() for _ in range(SLOTS_PER_EPOCH)]
    state.builder_pending_payments[SLOTS_PER_EPOCH:] = new_payments
```
```

**File:** specs/gloas/beacon-chain.md (L2383-2403)
```markdown
        if (
            will_set_new_flag
            and had_no_participation
            and is_attestation_same_slot(state, data)
            and payment.withdrawal.amount > 0
        ):
            payment.weight += state.validators[index].effective_balance

    # Reward proposer
    proposer_reward_denominator = (
        (WEIGHT_DENOMINATOR - PROPOSER_WEIGHT) * WEIGHT_DENOMINATOR // PROPOSER_WEIGHT
    )
    proposer_reward = Gwei(proposer_reward_numerator // proposer_reward_denominator)
    increase_balance(state, get_beacon_proposer_index(state), proposer_reward)

    # [New in Gloas:EIP7732]
    # Update builder payment weight
    if current_epoch_target:
        state.builder_pending_payments[SLOTS_PER_EPOCH + data.slot % SLOTS_PER_EPOCH] = payment
    else:
        state.builder_pending_payments[data.slot % SLOTS_PER_EPOCH] = payment
```

**File:** specs/phase0/beacon-chain.md (L1505-1533)
```markdown
#### `get_total_balance`

```python
def get_total_balance(state: BeaconState, indices: Set[ValidatorIndex]) -> Gwei:
    """
    Return the combined effective balance of the ``indices``.
    ``EFFECTIVE_BALANCE_INCREMENT`` Gwei minimum to avoid divisions by zero.
    Math safe up to ~10B ETH, after which this overflows Uint64.
    """
    return Gwei(
        max(
            EFFECTIVE_BALANCE_INCREMENT,
            sum([state.validators[index].effective_balance for index in indices]),
        )
    )
```

#### `get_total_active_balance`

```python
def get_total_active_balance(state: BeaconState) -> Gwei:
    """
    Return the combined effective balance of the active validators.
    Note: ``get_total_balance`` returns ``EFFECTIVE_BALANCE_INCREMENT`` Gwei minimum to avoid divisions by zero.
    """
    return get_total_balance(
        state, set(get_active_validator_indices(state, get_current_epoch(state)))
    )
```
```
