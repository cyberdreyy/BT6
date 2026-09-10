### Title
Slashing a source validator after a consolidation request permanently wastes consolidation churn, reducing future consolidation throughput for all validators - (File: specs/electra/beacon-chain.md)

### Summary
`process_consolidation_request` reserves consolidation churn for a source validator by calling `compute_consolidation_epoch_and_update_churn`, which decrements `state.consolidation_balance_to_consume` and sets `source_validator.exit_epoch`. If the source validator is subsequently slashed before `process_pending_consolidations` runs, the balance-moving branch is skipped entirely, but the churn that was already consumed to reserve the source's exit-epoch slot is never restored. This mirrors the Vault.sol bug where `totalAllocatedTokens` (an aggregate) is decremented as usage grows but never corrected when an entry is voided ("blacklisted"), permanently deflating future allocations for everyone else.

### Finding Description
Churn reservation happens in `process_consolidation_request`: [1](#0-0) 

which calls `compute_consolidation_epoch_and_update_churn`, decrementing the shared pool `state.consolidation_balance_to_consume`: [2](#0-1) 

Later, at epoch processing, `process_pending_consolidations` iterates the queue and, for any source validator that has since been slashed, skips the balance transfer entirely and simply advances past the entry — with no adjustment to any churn/consumption accounting: [3](#0-2) 

The equality broken: the invariant intended by the churn model is "cumulative consolidation balance moved (or committed-and-guaranteed-to-be-moved) across epochs ≤ cumulative per-epoch churn granted." Once a pending consolidation is voided by slashing, the balance is never moved, yet the churn slot it reserved (an advance on `earliest_consolidation_epoch`/`consolidation_balance_to_consume`) is not returned to the pool. Any validator can self-slash (e.g., via a slashable double-vote) after submitting a legitimate consolidation request, at zero cost beyond their own slashing penalty, and this permanently and irreversibly shrinks the effective consolidation throughput available to every other validator for the epochs that were reserved.

### Impact Explanation
This does not create or destroy Gwei, and does not fit the Critical bucket. It matches the High-severity category "a builder payment or withdrawal misdirected, doubled, or escaped" / churn-limit style bucket only loosely — more precisely it is a design flaw that permanently and unrecoverably reduces the consolidation churn budget for all future consolidation requesters, without requiring any privileged access, coalition, or partition: a single self-slash after a benign consolidation request is sufficient. It is directly analogous to the reported Vault.sol issue (M-6): an aggregate counter used to gate a shared resource (`totalAllocatedTokens` / `consolidation_balance_to_consume`) is not corrected when the underlying allocation is voided, permanently lowering the resource available to everyone else.

### Likelihood Explanation
Likelihood is low-to-moderate: it requires the same validator that submitted (or whose address submitted) a consolidation request to also get slashed before `process_pending_consolidations` processes the entry — plausible if a validator wants to both punish/burn its own churn-reservation and simultaneously grief the broader consolidation queue, or as an accidental side effect of any validator being slashed for unrelated reasons (equivocation) while it happens to have a pending consolidation. No coalition or malicious peer is required; a single validator can trigger it unilaterally against itself.

### Recommendation
When `process_pending_consolidations` skips a pending consolidation because the source validator is slashed, restore the consolidation churn that was reserved for that entry (increase `state.consolidation_balance_to_consume` by the effective balance amount that was originally consumed for that source, or otherwise track and refund the reservation), analogous to correcting `totalAllocatedTokens` when `currentAllocations[_protocolNum]` is zeroed in the referenced report.

### Proof of Concept
1. Validator `V` (source) submits a valid `ConsolidationRequest` targeting validator `T`. `process_consolidation_request` calls `compute_consolidation_epoch_and_update_churn(state, V.effective_balance)`, decrementing `state.consolidation_balance_to_consume` by `V.effective_balance` and setting `V.exit_epoch`/`withdrawable_epoch`, and appending `PendingConsolidation(source_index=V, target_index=T)` to `state.pending_consolidations`.
2. Before `process_pending_consolidations` runs (i.e., before `V.withdrawable_epoch` is reached and consolidation is finalized), `V` is slashed (e.g., via `AttesterSlashing`/`ProposerSlashing`), setting `V.slashed = True` via `slash_validator`.
3. At the next epoch boundary, `process_pending_consolidations` runs, sees `source_validator.slashed == True`, increments `next_pending_consolidation` and `continue`s — no balance is moved, and `state.consolidation_balance_to_consume`/`state.earliest_consolidation_epoch` are left as already reserved (decremented) by step 1.
4. The pool `state.consolidation_balance_to_consume` therefore reflects a reservation for a consolidation that will never execute; the churn budget for the epoch(s) that were advanced (`earliest_consolidation_epoch`) is permanently lost and unavailable to any other validator's consolidation requests, even though no consolidation actually occurred.

Note: I was unable to verify whether any later specs (e.g., `gloas`) fixed this behavior in `process_pending_consolidations`, since the excerpt I found for `gloas/beacon-chain.md` did not include that function's body in the retrieved context — this should be double-checked directly in that file.

### Citations

**File:** specs/electra/beacon-chain.md (L938-965)
```markdown
def compute_consolidation_epoch_and_update_churn(
    state: BeaconState, consolidation_balance: Gwei
) -> Epoch:
    earliest_consolidation_epoch = max(
        state.earliest_consolidation_epoch, compute_activation_exit_epoch(get_current_epoch(state))
    )
    per_epoch_consolidation_churn = get_consolidation_churn_limit(state)
    # New epoch for consolidations.
    if state.earliest_consolidation_epoch < earliest_consolidation_epoch:
        consolidation_balance_to_consume = per_epoch_consolidation_churn
    else:
        consolidation_balance_to_consume = state.consolidation_balance_to_consume

    # Consolidation doesn't fit in the current earliest epoch.
    if consolidation_balance > consolidation_balance_to_consume:
        balance_to_process = consolidation_balance - consolidation_balance_to_consume
        additional_epochs = (balance_to_process - 1) // per_epoch_consolidation_churn + 1
        earliest_consolidation_epoch += Epoch(additional_epochs)
        consolidation_balance_to_consume += additional_epochs * per_epoch_consolidation_churn

    # Consume the balance and update state variables.
    state.consolidation_balance_to_consume = (
        consolidation_balance_to_consume - consolidation_balance
    )
    state.earliest_consolidation_epoch = earliest_consolidation_epoch

    return state.earliest_consolidation_epoch
```
```

**File:** specs/electra/beacon-chain.md (L1195-1220)
```markdown
#### New `process_pending_consolidations`

```python
def process_pending_consolidations(state: BeaconState) -> None:
    next_epoch = get_current_epoch(state) + 1
    next_pending_consolidation = 0
    for pending_consolidation in state.pending_consolidations:
        source_validator = state.validators[pending_consolidation.source_index]
        if source_validator.slashed:
            next_pending_consolidation += 1
            continue
        if source_validator.withdrawable_epoch > next_epoch:
            break

        # Calculate the consolidated balance
        source_effective_balance = min(
            state.balances[pending_consolidation.source_index], source_validator.effective_balance
        )

        # Move active balance to target. Excess balance is withdrawable.
        decrease_balance(state, pending_consolidation.source_index, source_effective_balance)
        increase_balance(state, pending_consolidation.target_index, source_effective_balance)
        next_pending_consolidation += 1

    state.pending_consolidations = state.pending_consolidations[next_pending_consolidation:]
```
```

**File:** specs/electra/beacon-chain.md (L2067-2077)
```markdown
    # Initiate source validator exit and append pending consolidation
    source_validator.exit_epoch = compute_consolidation_epoch_and_update_churn(
        state, source_validator.effective_balance
    )
    source_validator.withdrawable_epoch = (
        source_validator.exit_epoch + MIN_VALIDATOR_WITHDRAWABILITY_DELAY
    )
    state.pending_consolidations.append(
        PendingConsolidation(source_index=source_index, target_index=target_index)
    )
```
```
