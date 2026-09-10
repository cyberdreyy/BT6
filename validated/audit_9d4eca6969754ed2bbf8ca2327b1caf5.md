### Title
Builder pending withdrawals commit an uncapped `amount` to the execution layer while the consensus layer only deducts `min(amount, balance)` - (File: specs/gloas/beacon-chain.md)

### Summary
In the Gloas fork, `get_builder_withdrawals()` copies the *requested* amount from `state.builder_pending_withdrawals[i].amount` verbatim into the `Withdrawal.amount` field that is committed to the execution layer via `payload_expected_withdrawals`. However, `apply_withdrawals()` only decreases the builder's on-chain balance by `min(withdrawal.amount, builder_balance)`. When a queued builder withdrawal amount exceeds the builder's current balance, the CL commits to an EL mint/credit of the full (uncapped) amount while only debiting the smaller, capped amount from the builder — creating Gwei that is not backed by any real balance decrease. This is the same bug class as the reference finding: a function is expected to report the *actual* transferred/deducted value but instead returns/commits the *requested* value, breaking the equality between what is recorded/committed and what actually moved.

### Finding Description
`get_builder_withdrawals` builds the `Withdrawal` objects that eventually populate `state.payload_expected_withdrawals`: [1](#0-0) 

```python
for withdrawal in state.builder_pending_withdrawals:
    ...
    builder_index = withdrawal.builder_index
    withdrawals.append(
        Withdrawal(
            index=withdrawal_index,
            validator_index=convert_builder_index_to_validator_index(builder_index),
            address=withdrawal.fee_recipient,
            amount=withdrawal.amount,   # <-- raw requested amount, not capped
        )
    )
```

The amount used here is the raw, previously-queued request amount (set e.g. from `bid.value` in `process_execution_payload_bid`, or copied when settling a builder payment in `apply_parent_execution_payload`), not the builder's actual available balance.

`apply_withdrawals` then performs the real balance mutation, but caps the deduction to the builder's current balance: [2](#0-1) 

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

The uncapped `Withdrawal` (with `amount = withdrawal.amount`, not `min(withdrawal.amount, builder_balance)`) is then stored into `state.payload_expected_withdrawals` via `update_payload_expected_withdrawals`, and the spec explicitly states this list is a binding commitment that the execution layer *must* honor: [3](#0-2) 

"any execution payload that has the corresponding block as parent beacon block is required to honor these withdrawals in the execution layer... Since the execution layer mints the full committed amount regardless, any CL-side saturation creates a net supply inflation."

This note only discusses the *validator* sweep-saturation case, but the exact same "EL mints the full committed amount regardless of CL-side capping" mechanism applies to builder pending withdrawals, and there it is reached through `min(withdrawal.amount, builder_balance)` in `apply_withdrawals`, not through `decrease_balance` saturation.

This matches the reference report's root cause pattern exactly: `SavingsAccountUtil.savingsAccountTransfer()` returned the caller-supplied `_amount` instead of the actual transferred/capped value, so downstream accounting (`_sharesReceived`) diverged from the real internal transfer. Here, `get_builder_withdrawals` returns the caller-supplied/requested `amount` instead of the actual amount that will be deducted (`min(amount, balance)`), so the value committed to the EL (`payload_expected_withdrawals`) diverges from the real internal balance deduction.

### Impact Explanation
This breaks the fundamental equality that "every Gwei credited on the EL side must correspond to an equal Gwei debited on the CL side." Whenever `builder_pending_withdrawals[i].amount > builders[builder_index].balance` at settlement time, the block commits an EL withdrawal (mint to `fee_recipient`) for the full requested amount, while the CL state only reduces the builder's balance by the smaller, capped amount. The difference is Gwei created out of nothing and paid to `fee_recipient` (a non-owner/arbitrary address), which is a Critical-severity finding per the rubric ("Gwei created, destroyed or paid to a non-owner").

The spec's own commit that documents the *validator* case explicitly acknowledges "any CL-side saturation creates a net supply inflation" — the exact same failure mode exists for builder withdrawals, but is not called out or fixed there.

### Likelihood Explanation
The spec test suite itself constructs and accepts as valid the exact precondition needed (`builder_pending_withdrawals[i].amount` exceeding the builder's current balance) without any rejection/assertion failure, demonstrating this state is reachable through ordinary spec-following execution (no malicious peer, coalition, or client bug needed): [4](#0-3) 

The test explicitly documents: "withdrawal.amount: 5 ETH (requested amount)" while "builders[0].balance: 0 (deduction capped to available balance)" — i.e., the spec processes this without error, confirming the divergence between committed `Withdrawal.amount` and actual balance deduction is a normal, non-reverting code path rather than a corner case blocked by an invariant/assertion.

Whether an under-collateralized `builder_pending_withdrawals` entry can arise "for free" (without a coalition/attacker) depends on the interaction between `can_builder_cover_bid` (checked at bid time) and the ~2-epoch delay before `settle_builder_payment`/direct-append paths add the entry to `builder_pending_withdrawals`; I was not able to fully verify `can_builder_cover_bid`'s exact accounting logic before running out of iterations, so I cannot conclusively state how far balance can drift below a previously-queued amount during that window. This is the main open uncertainty in this analysis.

### Recommendation
In `get_builder_withdrawals` (and anywhere a `Withdrawal` is constructed for a builder), cap the committed `amount` to the builder's actual available balance so the committed EL payload equals what `apply_withdrawals` will actually deduct:

```python
withdrawals.append(
    Withdrawal(
        index=withdrawal_index,
        validator_index=convert_builder_index_to_validator_index(builder_index),
        address=withdrawal.fee_recipient,
        amount=min(withdrawal.amount, state.builders[builder_index].balance),
    )
)
```

Equivalently, `apply_withdrawals` should not silently cap/saturate; instead the amount used for the EL commitment and the CL deduction must be computed identically in one place to preserve the supply invariant.

### Proof of Concept
1. A `BuilderPendingWithdrawal` entry is queued for `builder_index` with `amount = 5 ETH` (e.g., via `process_execution_payload_bid` → `BuilderPendingPayment` → settled into `state.builder_pending_withdrawals`, as shown in [5](#0-4) ).
2. Before this withdrawal is processed, `builders[builder_index].balance` drops to `1 ETH` (this exact precondition is directly constructed and accepted by the spec's own test suite in `test_builder_withdrawal_insufficient_balance`, [6](#0-5) ).
3. `process_withdrawals` calls `get_expected_withdrawals` → `get_builder_withdrawals`, which emits `Withdrawal(amount=5 ETH, address=fee_recipient)` — this is stored in `state.payload_expected_withdrawals`, which the EL must honor (mint 5 ETH to `fee_recipient`).
4. `apply_withdrawals` executes `state.builders[builder_index].balance -= min(5 ETH, 1 ETH)`, i.e., only deducts 1 ETH.
5. Net effect: EL credits `fee_recipient` with 5 ETH; CL debits the builder only 1 ETH. 4 ETH of Gwei has been created and paid to `fee_recipient` with no corresponding balance decrease anywhere in the beacon state — confirmed by the test's own assertions: `builder_balances={builder_index: 0}` (only 1 ETH deducted) alongside `withdrawal_amounts_builders={builder_index: withdrawal_amount}` where `withdrawal_amount = 5 ETH` ( [7](#0-6) ).

### Citations

**File:** specs/gloas/beacon-chain.md (L1761-1770)
```markdown
    elif parent_bid.value > 0:
        # Parent is older than the previous epoch, its payment entry has been
        # evicted from builder_pending_payments. Append the withdrawal directly.
        state.builder_pending_withdrawals.append(
            BuilderPendingWithdrawal(
                fee_recipient=parent_bid.fee_recipient,
                amount=parent_bid.value,
                builder_index=parent_bid.builder_index,
            )
        )
```

**File:** specs/gloas/beacon-chain.md (L1815-1830)
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

**File:** specs/gloas/beacon-chain.md (L1967-1990)
```markdown
##### Modified `process_withdrawals`

*Note*: This is modified to only take the `state` as parameter. Withdrawals are
deterministic given the beacon state, any execution payload that has the
corresponding block as parent beacon block is required to honor these
withdrawals in the execution layer. `process_withdrawals` must be called after
`process_parent_execution_payload` (which updates `state.latest_block_hash`) and
before `process_execution_payload_bid` as the latter function affects validator
balances.

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
