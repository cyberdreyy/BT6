### Title
Builder pending-withdrawal `amount` is minted uncapped by the EL while the CL only debits the builder's available balance - ([File: specs/gloas/beacon-chain.md])

### Summary
In the Gloas fork, `get_builder_withdrawals` emits a `Withdrawal` object whose `amount` is the *raw, requested* `BuilderPendingWithdrawal.amount`, unclamped by the builder's actual balance. `apply_withdrawals`, however, only decreases `state.builders[builder_index].balance` by `min(withdrawal.amount, builder_balance)`. Since the execution layer credits the recipient with exactly the `amount` field committed in the block's withdrawals list (it does not re-derive or cap it against any CL-side balance), a builder with insufficient balance can still have the full, uncapped `amount` paid out on the execution layer while the consensus layer only debits what it actually had. This is analogous to the reported LaunchEvent bug where the "amount owed" and the "amount actually available/backed" silently diverge, except here the divergence manifests as unbacked ETH minted to the builder's `fee_recipient` rather than a reverted withdrawal.

### Finding Description
`get_builder_withdrawals` builds the withdrawal record straight from the pending-withdrawal request, with no cap against the builder's current balance: [1](#0-0) 

```
for withdrawal in state.builder_pending_withdrawals:
    ...
    withdrawals.append(
        Withdrawal(
            index=withdrawal_index,
            validator_index=convert_builder_index_to_validator_index(builder_index),
            address=withdrawal.fee_recipient,
            amount=withdrawal.amount,   # <-- uncapped, requested amount
        )
    )
```

This `Withdrawal.amount` is what ends up in `state.payload_expected_withdrawals`, which the block commits to and which the execution layer is required to honor verbatim (the EL simply credits `fee_recipient` with `amount` for every entry in the payload's withdrawals list — this is the standard withdrawal-processing contract inherited from Capella's `process_withdrawals`/EIP-4895 semantics; nothing in the withdrawal-application path re-checks the amount against any builder or validator balance on the EL side).

Meanwhile, the CL-side balance mutation caps the deduction to what the builder actually has: [2](#0-1) 

```python
def apply_withdrawals(state: BeaconState, withdrawals: Sequence[Withdrawal]) -> None:
    for withdrawal in withdrawals:
        if is_builder_index(withdrawal.validator_index):
            builder_index = convert_validator_index_to_builder_index(withdrawal.validator_index)
            builder_balance = state.builders[builder_index].balance
            state.builders[builder_index].balance -= min(withdrawal.amount, builder_balance)
        else:
            decrease_balance(state, withdrawal.validator_index, withdrawal.amount)
```

The spec's own test suite documents and asserts this exact divergence as intended behavior: when a builder requests a 5 ETH withdrawal but only has 1 ETH, the committed `withdrawal.amount` stays at 5 ETH while the builder's balance is only decreased by 1 ETH (down to 0): [3](#0-2) 

The input-space report for this function explicitly states the actual balance change formula differs from the committed withdrawal amount: "Actual = `min(amount, builder.balance)`" for the CL-side balance decrease, while the withdrawal's `amount` field carries the full "Requested amount": [4](#0-3) 

The spec authors were aware that saturating deductions can create supply mismatches with the EL — they explicitly discuss this risk for validator withdrawals in the `process_withdrawals` notes, arguing that applying deductions immediately (in the same block that computes the withdrawal amount) avoids the "net supply inflation" that would occur if the balance dropped between commitment and deduction: [5](#0-4) 

That argument, however, does not hold for the builder path: unlike `is_partially_withdrawable_validator`/`is_fully_withdrawable_validator`, which compute the withdrawal `amount` from the *current* balance at commitment time (so the amount recorded is already bounded by what's available and can never exceed it — validators are always drained by exactly their real balance or exactly the requested excess above `MAX_EFFECTIVE_BALANCE`, which is not more than the current balance), `get_builder_withdrawals` records a request amount from `state.builder_pending_withdrawals`, an amount which was set whenever the withdrawal request was created, is never re-validated or reduced when the entry is later dequeued, and can legitimately be larger than the builder's current balance (e.g., if the builder's balance was reduced by the fully separate `get_builders_sweep_withdrawals` path, or by covering excess-balance requirements in `process_execution_payload_bid`, between the time the pending withdrawal was queued and the time it is swept out). Because `payload_expected_withdrawals`/`Withdrawal.amount` is what the block commits to and the EL executes verbatim, the EL side pays the requested (uncapped) amount to `fee_recipient` while the CL side only ever removes the smaller, capped amount from `state.builders[...].balance`. The difference is Gwei paid to (or minted for) the builder's fee recipient that was never backed by any decrease elsewhere in consensus state — a direct violation of the "Gwei created, destroyed, or paid to a non-owner" invariant, and a payment applied by the EL that the CL accounting does not actually reflect.

### Impact Explanation
This breaks the fundamental equality that every Gwei paid out on the execution layer via a beacon withdrawal must correspond to an equal decrease in a tracked consensus-layer balance. Here, the amount actually minted/credited to the builder's `fee_recipient` on the execution layer (`withdrawal.amount`, uncapped) can exceed the amount the consensus layer removed from the builder's balance (`min(withdrawal.amount, builder_balance)`). This is a form of Gwei creation reachable by any single builder that requests a withdrawal larger than its balance (or whose balance later drops below its already-queued withdrawal amount) — no coalition, malicious peer, or client bug is required; it is a property of the honest, spec-following state-transition function itself. This satisfies the Critical bar ("Gwei created ... paid to a non-owner").

### Likelihood Explanation
Likelihood is high in the sense that reaching the divergent state requires only ordinary protocol actions: a builder submits (or has queued) a `BuilderPendingWithdrawal` for an amount larger than its balance, or its balance drops (e.g. from being consumed by bid economics or another withdrawal) between request queuing and the sweep that processes it. The test suite's own "insufficient balance" tests demonstrate this is not merely theoretical but a state that is explicitly modeled and accepted by the current spec text without any compensating mechanism to keep the EL-side and CL-side amounts equal.

### Recommendation
Cap the `amount` field written into the `Withdrawal` in `get_builder_withdrawals` (and any other builder-withdrawal-producing helper) to `min(withdrawal.amount, state.builders[builder_index].balance)` at the point the withdrawal record is constructed, mirroring how `is_partially_withdrawable_validator`/`is_fully_withdrawable_validator` always derive the withdrawal amount from the validator's real, current balance. This ensures the amount committed to (and thus minted by) the execution layer can never exceed what `apply_withdrawals` actually removes from the builder's tracked balance, restoring the CL/EL balance-conservation invariant.

### Proof of Concept
1. A builder is registered with balance `B` (e.g. 1 ETH).
2. A `BuilderPendingWithdrawal` is queued (via whatever request path enqueues it) with `amount = 5 ETH`, exceeding `B`. (The unit test `test_builder_withdrawal_insufficient_balance` constructs this scenario directly by injecting the pending withdrawal and setting a smaller balance.) [6](#0-5) 
3. `process_withdrawals` runs: `get_builder_withdrawals` emits `Withdrawal(amount=5 ETH, address=fee_recipient)` into `payload_expected_withdrawals`. [7](#0-6) 
4. `apply_withdrawals` decreases `builders[0].balance` by only `min(5 ETH, 1 ETH) = 1 ETH`, leaving it at 0. [8](#0-7) 
5. The execution layer, honoring the block's committed withdrawals list, credits `fee_recipient` with the full 5 ETH.
6. Net result: 4 ETH appears on the execution layer with no corresponding decrease anywhere in the consensus-layer state — unbacked value creation, confirmed exactly by the test's assertions (`builder_balances={builder_index: 0}` vs `withdrawal_amounts_builders={builder_index: withdrawal_amount}` where `withdrawal_amount = 5 ETH`). [9](#0-8)

### Citations

**File:** specs/gloas/beacon-chain.md (L1815-1832)
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

**File:** specs/gloas/beacon-chain.md (L1977-1989)
```markdown
*Note*: Unlike deposits (which are applied at the child's slot via
`apply_parent_execution_payload`), withdrawal balance deductions are applied
immediately via `apply_withdrawals`. Deferring the deduction to the child's slot
would break the total supply invariant: state transitions between the commitment
slot and the deduction slot (e.g., `process_pending_consolidations` at an epoch
boundary) can reduce a validator's balance below the committed withdrawal
amount, causing `decrease_balance` to saturate at zero. Since the execution
layer mints the full committed amount regardless, any CL-side saturation creates
a net supply inflation. As a consequence, `state.balances` reflects the
withdrawal deduction before the corresponding execution payload is confirmed,
creating a transient asymmetry with the EL state at `state.latest_block_hash`.
Off-chain consumers that require CL/EL balance consistency can reconstruct
pre-deduction balances by adding back `state.payload_expected_withdrawals`.
```

**File:** tests/core/pyspec/eth_consensus_specs/test/gloas/block_processing/test_process_withdrawals.py (L114-156)
```python
@with_gloas_and_later
@spec_state_test
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

**File:** tests/core/pyspec/eth_consensus_specs/test/gloas/block_processing/test_process_withdrawals.md (L31-35)
```markdown
| `state.builder_pending_withdrawals`                      | `List[BuilderPendingWithdrawal, BUILDER_PENDING_WITHDRAWALS_LIMIT]` | Length in [0, 2^20]. Processed first. Max `MAX_WITHDRAWALS_PER_PAYLOAD - 1` can produce withdrawals.                                                                 | Iterate for builder pending withdrawals |
| (in `builder_pending_withdrawals`) `.builder_index`      | `BuilderIndex`                                                      | `Uint64` in \[0, `len(builders)`-1\]. **Index into** `builders[]`.                                                                                                   | Identify builder in registry            |
| (in `builder_pending_withdrawals`) `.fee_recipient`      | `ExecutionAddress`                                                  | `Bytes20`. Withdrawal destination. Independent of builder's own `execution_address`.                                                                                 | Withdrawal destination                  |
| (in `builder_pending_withdrawals`) `.amount`             | `Gwei`                                                              | `Uint64`. Requested amount. Actual = `min(amount, builder.balance)`.                                                                                                 | Withdrawal amount                       |
| `state.pending_partial_withdrawals`                      | `List[PendingPartialWithdrawal, PENDING_PARTIAL_WITHDRAWALS_LIMIT]` | Length in [0, 2^27]. Processed second. Max `MAX_PENDING_PARTIALS_PER_WITHDRAWALS_SWEEP` (8) processed per block.                                                     | Iterate for partial withdrawals         |
```
