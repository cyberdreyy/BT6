### Title
Builder double-credited when a queued pending withdrawal and a builder-sweep withdrawal are computed against the same undiminished balance in `process_withdrawals` - (File: `specs/gloas/beacon-chain.md`)

### Summary
`get_expected_withdrawals` builds the withdrawal list for the execution layer by calling, in order, `get_builder_withdrawals` (drains `state.builder_pending_withdrawals`) and then `get_builders_sweep_withdrawals` (drains any builder past `withdrawable_epoch`). Both helpers read `state.builders[i].balance` as it exists *before* `apply_withdrawals` runs, so if the same builder has a queued pending withdrawal **and** is independently eligible for a full-balance sweep in the same call, the same Gwei gets committed twice into `state.payload_expected_withdrawals`. The CL-side balance bookkeeping in `apply_withdrawals` is safely capped with `min(...)`, but the execution layer is contractually required to honor the full committed `Withdrawal.amount` for every entry regardless of that CL-side capping — exactly as the spec's own note on `process_withdrawals` acknowledges can create "net supply inflation."

### Finding Description
`get_builder_withdrawals` emits one `Withdrawal` per queued `BuilderPendingWithdrawal` using the withdrawal's stored `amount` field, unrelated to the builder's current balance: [1](#0-0) 

`get_builders_sweep_withdrawals`, called immediately afterward within the same `get_expected_withdrawals` pass, reads the *same, still-undiminished* `builder.balance` from state and emits a second `Withdrawal` for the builder's full balance if `withdrawable_epoch <= epoch`: [2](#0-1) 

Neither helper accounts for what the other helper has already queued for the same builder, because the actual balance mutation is deferred to `apply_withdrawals`, which runs only after the full withdrawals list (both entries) has been constructed: [3](#0-2) 

`apply_withdrawals` then processes the list sequentially and safely caps the CL balance decrease with `min(withdrawal.amount, builder_balance)`: [4](#0-3) 

That `min()` capping only protects `state.builders[*].balance` from going negative — it does **not** change the `amount` field already written into `state.payload_expected_withdrawals`, which is the object committed to and honored by the execution layer. The spec explicitly documents that the EL "mints the full committed amount regardless" of any CL-side saturation: [5](#0-4) 

That note is written for a different, narrower case (a partial withdrawal amount reduced below the committed amount by an intervening consolidation between commitment and deduction slots). It does not cover — and the code does not guard against — a builder being queued in **both** `builder_pending_withdrawals` and eligible for `get_builders_sweep_withdrawals` in the *same* `process_withdrawals` call, which is the scenario analogous to the reported "protocol fee + trade fee + royalty > inputAmount" bug: two independent payout mechanisms compute their payout against the same shared balance without checking that their sum does not exceed what is actually available, and the actual (correct) deduction from the balance diverges from what is promised/paid out externally.

### Impact Explanation
This breaks the equality "total Gwei credited by withdrawals in the execution payload == total Gwei debited from CL balances." A builder ends up with two `Withdrawal` entries referencing the same pre-deduction balance: one paid to an arbitrary `fee_recipient` (from the queued pending withdrawal) and one paid to the builder's own `execution_address` (from the sweep), while the CL balance is only ever decremented once (correctly capped to the true balance by `min()`). The excess is Gwei created out of thin air and credited to the builder's own withdrawal address — a non-owner relative to the actual backing balance — which matches the Critical impact category "Gwei created, destroyed or paid to a non-owner."

### Likelihood Explanation
No malicious peer, client bug, or coalition is required. Builder pending withdrawals accumulate from ordinary settled payments (`settle_builder_payment`) independently of a builder's exit status, while sweep eligibility is driven solely by `withdrawable_epoch` set via `initiate_builder_exit` after a `BuilderExitRequest`. Any builder that requests an exit while it still has an unsettled/queued pending withdrawal outstanding, and whose `withdrawable_epoch` elapses before that pending withdrawal is drained, triggers this condition purely through normal, spec-compliant protocol usage.

### Recommendation
Track a running "reserved/committed" balance across `get_builder_withdrawals` and `get_builders_sweep_withdrawals` within `get_expected_withdrawals` (e.g., subtract already-queued pending-withdrawal amounts for a builder from the balance used by the sweep helper, or cap the sweep withdrawal amount by `builder.balance - pending_amount_already_queued_this_call`), so the sum of all `Withdrawal.amount` entries emitted for a given builder in one call can never exceed that builder's balance at the start of `process_withdrawals`.

### Proof of Concept
1. Builder `B` has `state.builders[B].balance = 10_000_000_000` (10 ETH).
2. A prior bid from `B` is settled via `settle_builder_payment`, appending `BuilderPendingWithdrawal(fee_recipient=feeAddr, amount=10_000_000_000, builder_index=B)` to `state.builder_pending_withdrawals` (see `settle_builder_payment` at `specs/gloas/beacon-chain.md:1518-1527`). `B.balance` is still 10 ETH (unchanged; the pending withdrawal has not been applied yet).
3. `B` submits a valid `BuilderExitRequest`; `initiate_builder_exit` sets `B.withdrawable_epoch = current_epoch + MIN_BUILDER_WITHDRAWABILITY_DELAY`. Once that epoch is reached, `B.balance` is still 10 ETH and `B.withdrawable_epoch <= epoch`.
4. At the next block whose parent is full, `process_withdrawals(state)` runs `get_expected_withdrawals`:
   - `get_builder_withdrawals` drains the queued pending withdrawal, producing `Withdrawal(address=feeAddr, amount=10_000_000_000)`.
   - `get_builders_sweep_withdrawals` then scans `state.builders` and finds `B` still shows `balance=10_000_000_000` (not yet decremented), producing a second `Withdrawal(address=B.execution_address, amount=10_000_000_000)`.
5. `apply_withdrawals` processes both: the first deducts `min(10e9, 10e9)=10e9`, balance→0; the second deducts `min(10e9, 0)=0`, balance stays 0.
6. `state.payload_expected_withdrawals` (committed to the execution layer) contains both withdrawals totaling 20 ETH credited to `feeAddr` and `B.execution_address`, while only 10 ETH was ever backed by/deducted from `B`'s CL balance — 10 ETH of Gwei has been created and paid to `B`'s own address.

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

**File:** specs/gloas/beacon-chain.md (L1839-1874)
```markdown
def get_builders_sweep_withdrawals(
    state: BeaconState,
    withdrawal_index: WithdrawalIndex,
    prior_withdrawals: Sequence[Withdrawal],
) -> Tuple[Sequence[Withdrawal], WithdrawalIndex, Uint64]:
    epoch = get_current_epoch(state)
    builders_limit = min(len(state.builders), MAX_BUILDERS_PER_WITHDRAWALS_SWEEP)
    withdrawals_limit = MAX_WITHDRAWALS_PER_PAYLOAD - 1
    assert len(prior_withdrawals) <= withdrawals_limit

    processed_count = Uint64(0)
    withdrawals: list[Withdrawal] = []
    builder_index = state.next_withdrawal_builder_index
    for _ in range(builders_limit):
        all_withdrawals = list(prior_withdrawals) + withdrawals
        has_reached_limit = len(all_withdrawals) >= withdrawals_limit
        if has_reached_limit:
            break

        builder = state.builders[builder_index]
        if builder.withdrawable_epoch <= epoch and builder.balance > 0:
            withdrawals.append(
                Withdrawal(
                    index=withdrawal_index,
                    validator_index=convert_builder_index_to_validator_index(builder_index),
                    address=builder.execution_address,
                    amount=builder.balance,
                )
            )
            withdrawal_index += 1

        builder_index = (builder_index + 1) % len(state.builders)
        processed_count += 1

    return withdrawals, withdrawal_index, processed_count
```
```

**File:** specs/gloas/beacon-chain.md (L1879-1918)
```markdown
def get_expected_withdrawals(state: BeaconState) -> ExpectedWithdrawals:
    withdrawal_index = state.next_withdrawal_index
    withdrawals: list[Withdrawal] = []

    # [New in Gloas:EIP7732]
    # Get builder withdrawals
    builder_withdrawals, withdrawal_index, processed_builder_withdrawals_count = (
        get_builder_withdrawals(state, withdrawal_index, withdrawals)
    )
    withdrawals.extend(builder_withdrawals)

    # Get partial withdrawals
    partial_withdrawals, withdrawal_index, processed_partial_withdrawals_count = (
        get_pending_partial_withdrawals(state, withdrawal_index, withdrawals)
    )
    withdrawals.extend(partial_withdrawals)

    # [New in Gloas:EIP7732]
    # Get builders sweep withdrawals
    builders_sweep_withdrawals, withdrawal_index, processed_builders_sweep_count = (
        get_builders_sweep_withdrawals(state, withdrawal_index, withdrawals)
    )
    withdrawals.extend(builders_sweep_withdrawals)

    # Get validators sweep withdrawals
    validators_sweep_withdrawals, withdrawal_index, processed_validators_sweep_count = (
        get_validators_sweep_withdrawals(state, withdrawal_index, withdrawals)
    )
    withdrawals.extend(validators_sweep_withdrawals)

    return ExpectedWithdrawals(
        withdrawals,
        # [New in Gloas:EIP7732]
        processed_builder_withdrawals_count,
        processed_partial_withdrawals_count,
        # [New in Gloas:EIP7732]
        processed_builders_sweep_count,
        processed_validators_sweep_count,
    )
```
```

**File:** specs/gloas/beacon-chain.md (L1920-1932)
```markdown
##### Modified `apply_withdrawals`

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
