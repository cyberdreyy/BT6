### Title
Slashing rewards are minted from the validator's stale `effective_balance` rather than its actual, possibly-depleted `balance` - (File: `specs/phase0/beacon-chain.md`, `slash_validator`)

### Summary
`slash_validator` computes both the slashing penalty and the proposer/whistleblower reward from `validator.effective_balance`, while the actual deduction is applied through `decrease_balance`, which silently floors at zero instead of reverting. `effective_balance` is only refreshed once per epoch in `process_effective_balance_updates`, so within an epoch a validator's real `balance` can already be far below its `effective_balance` (e.g., after heavy inactivity-leak penalties applied earlier the same epoch in `process_rewards_and_penalties`). When such a validator is later slashed, `decrease_balance` deducts nothing (or less than the nominal penalty) from the already-depleted balance, yet the proposer and whistleblower are still credited `effective_balance // WHISTLEBLOWER_REWARD_QUOTIENT`, an amount not backed by any real deduction elsewhere in the state.

### Finding Description
`slash_validator` (`specs/phase0/beacon-chain.md`, also mirrored with the same structure in Altair/Bellatrix/Electra) reads a single field, `validator.effective_balance`, for two distinct purposes:

```python
state.slashings[epoch % EPOCHS_PER_SLASHINGS_VECTOR] += validator.effective_balance
decrease_balance(
    state, slashed_index, validator.effective_balance // MIN_SLASHING_PENALTY_QUOTIENT
)
...
whistleblower_reward = validator.effective_balance // WHISTLEBLOWER_REWARD_QUOTIENT
proposer_reward = whistleblower_reward // PROPOSER_REWARD_QUOTIENT
increase_balance(state, proposer_index, proposer_reward)
increase_balance(state, whistleblower_index, whistleblower_reward - proposer_reward)
``` [1](#0-0) 

`decrease_balance` has underflow protection that floors the debit at zero instead of asserting or scaling down the credited rewards to match:
```python
def decrease_balance(state: BeaconState, index: ValidatorIndex, delta: Gwei) -> None:
    if delta > state.balances[index]:
        state.balances[index] = Gwei(0)
    else:
        state.balances[index] -= delta
``` [2](#0-1) 

Crucially, `effective_balance` is not kept in lock-step with `balance`; it is only recalculated once per epoch, in `process_effective_balance_updates`:
```python
def process_effective_balance_updates(state: BeaconState) -> None:
    for index, validator in enumerate(state.validators):
        balance = state.balances[index]
        ...
        if (balance + DOWNWARD_THRESHOLD < validator.effective_balance
                or validator.effective_balance + UPWARD_THRESHOLD < balance):
            validator.effective_balance = min(...)
``` [3](#0-2) 

`process_epoch` runs `process_rewards_and_penalties` (which can apply a large inactivity-leak penalty via `decrease_balance`) before `process_effective_balance_updates` refreshes `effective_balance` for the next epoch. If, within that same epoch (or block), the validator is subsequently slashed via `process_proposer_slashing`/`process_attester_slashing` → `slash_validator`, the whistleblower/proposer reward is still computed off the pre-penalty `effective_balance`, not off whatever remains in `state.balances[index]`.

This exactly mirrors the reported Overlay bug: `liquidatable()` used `maintenanceMarginFraction` against a value while the fee payout used a separate, unrelated `liquidationFeeRate`, so "enough value left" for eligibility did not imply "enough value left" for the payout. Here, the mechanism that decides how much to *deduct* (capped, floor-protected `decrease_balance`) and the mechanism that decides how much to *pay out* (`whistleblower_reward`/`proposer_reward`, computed independently from the same stale field) are similarly decoupled: nothing ties the reward to the amount actually removed from the slashed validator.

### Impact Explanation
This breaks the "Gwei conservation" invariant: `increase_balance` calls for the proposer and whistleblower are not matched by an equal-and-opposite `decrease_balance` on the slashed validator, when the validator's real balance has already been driven below its cached `effective_balance`. The result is Gwei effectively minted and paid to third parties (the proposer/whistleblower are non-owners of the extra value), which falls under the Critical impact category ("Gwei created, destroyed or paid to a non-owner").

### Likelihood Explanation
This requires no malicious peer, coalition, or client bug — it is reachable purely by an honest sequence of state-transition events: a validator accrues a large inactivity-leak penalty within an epoch (`process_rewards_and_penalties`), and is slashed later in that same epoch/block before `process_effective_balance_updates` has a chance to resynchronize `effective_balance` to the depleted `balance`. Because `MIN_EPOCHS_TO_INACTIVITY_PENALTY` and the inactivity leak can produce large, fast-accelerating penalties (quadratic leak), and slashing can occur at any point via a `ProposerSlashing`/`AttesterSlashing` included in a block, this window is realistically reachable, though it requires an already-leaking, near-zero-balance validator to be slashed within the exposure window — a comparatively narrow but concretely reachable state.

### Recommendation
Base the slashing penalty and the whistleblower/proposer reward on the amount actually deducted (i.e., derive the reward from `min(validator.effective_balance // MIN_SLASHING_PENALTY_QUOTIENT, state.balances[slashed_index])`, or otherwise ensure `decrease_balance`'s effective debit and the reward computation always reference the same bounded quantity), so that `increase_balance(proposer/whistleblower, ...)` can never exceed what was actually removed from the slashed validator's balance.

### Proof of Concept
1. At epoch `N`, validator `V` is in a long inactivity leak; `process_rewards_and_penalties` at the end of epoch `N` applies a very large inactivity penalty via `decrease_balance`, driving `state.balances[V]` to (near) 0, while `validator.effective_balance` still reflects the previous, much higher value (it has not yet been recalculated).
2. Before `process_effective_balance_updates` runs (still within the same `process_epoch` call, or in a block included before the next epoch boundary), an honest proposer includes a `ProposerSlashing` or `AttesterSlashing` against `V` (validly signed, `V` genuinely equivocated at some prior point and is still slashable per `is_slashable_validator`). [4](#0-3) 
3. `process_attester_slashing`/`process_proposer_slashing` calls `slash_validator(state, V)`. [5](#0-4) 
4. `slash_validator` computes `slashing_penalty = validator.effective_balance // MIN_SLASHING_PENALTY_QUOTIENT`, a nonzero amount, and calls `decrease_balance(state, V, slashing_penalty)`; since `state.balances[V]` is already 0, `decrease_balance` sets it to `Gwei(0)` (no actual debit occurs).
5. Nonetheless, `whistleblower_reward = validator.effective_balance // WHISTLEBLOWER_REWARD_QUOTIENT` and `proposer_reward = whistleblower_reward // PROPOSER_REWARD_QUOTIENT` are computed from the same stale `effective_balance` and credited via `increase_balance` to the proposer and whistleblower.
6. Net effect: `state.balances[V]` decreased by 0 Gwei, but `state.balances[proposer_index]` and `state.balances[whistleblower_index]` increased by `whistleblower_reward` Gwei combined — Gwei has been created and paid to non-owners with no corresponding debit anywhere in state.

### Citations

**File:** specs/phase0/beacon-chain.md (L1112-1122)
```markdown
#### `is_slashable_validator`

```python
def is_slashable_validator(validator: Validator, epoch: Epoch) -> bool:
    """
    Check if ``validator`` is slashable.
    """
    return (not validator.slashed) and (
        validator.activation_epoch <= epoch < validator.withdrawable_epoch
    )
```
```

**File:** specs/phase0/beacon-chain.md (L1606-1613)
```markdown
def decrease_balance(state: BeaconState, index: ValidatorIndex, delta: Gwei) -> None:
    """
    Decrease the validator balance at index ``index`` by ``delta``, with underflow protection.
    """
    if delta > state.balances[index]:
        state.balances[index] = Gwei(0)
    else:
        state.balances[index] -= delta
```

**File:** specs/phase0/beacon-chain.md (L1658-1670)
```markdown
    state.slashings[epoch % EPOCHS_PER_SLASHINGS_VECTOR] += validator.effective_balance
    decrease_balance(
        state, slashed_index, validator.effective_balance // MIN_SLASHING_PENALTY_QUOTIENT
    )

    # Apply proposer and whistleblower rewards
    proposer_index = get_beacon_proposer_index(state)
    if whistleblower_index is None:
        whistleblower_index = proposer_index
    whistleblower_reward = validator.effective_balance // WHISTLEBLOWER_REWARD_QUOTIENT
    proposer_reward = whistleblower_reward // PROPOSER_REWARD_QUOTIENT
    increase_balance(state, proposer_index, proposer_reward)
    increase_balance(state, whistleblower_index, whistleblower_reward - proposer_reward)
```

**File:** specs/phase0/beacon-chain.md (L2209-2220)
```markdown
def process_effective_balance_updates(state: BeaconState) -> None:
    # Update effective balances with hysteresis
    for index, validator in enumerate(state.validators):
        balance = state.balances[index]
        HYSTERESIS_INCREMENT = Uint64(EFFECTIVE_BALANCE_INCREMENT // HYSTERESIS_QUOTIENT)
        DOWNWARD_THRESHOLD = HYSTERESIS_INCREMENT * HYSTERESIS_DOWNWARD_MULTIPLIER
        UPWARD_THRESHOLD = HYSTERESIS_INCREMENT * HYSTERESIS_UPWARD_MULTIPLIER
        if (
            balance + DOWNWARD_THRESHOLD < validator.effective_balance
            or validator.effective_balance + UPWARD_THRESHOLD < balance
        ):
            validator.effective_balance = min(
```

**File:** specs/phase0/beacon-chain.md (L2373-2374)
```markdown

    slash_validator(state, header_1.proposer_index)
```
