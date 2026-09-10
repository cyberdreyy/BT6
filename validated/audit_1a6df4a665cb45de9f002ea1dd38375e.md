### Title
Builder pending withdrawals are not capped against the builder's balance before being committed to the execution payload, allowing Gwei to be paid out that is never deducted from CL state - ([File: specs/gloas/beacon-chain.md])

### Summary
`get_builder_withdrawals` copies the raw `amount` field from each queued `BuilderPendingWithdrawal` directly into the `Withdrawal` that is committed to the execution payload, without ever checking it against the builder's current balance or against the cumulative effect of earlier withdrawals processed in the same call. The actual balance deduction is only capped later, in `apply_withdrawals`, via `min(withdrawal.amount, builder_balance)`. If the sum of a builder's queued pending withdrawals exceeds its balance, the execution layer is committed to paying out the full, uncapped amounts (this is the analog of the "value transferred" in the ERC4626 report), while the beacon state only removes the smaller, saturated amount — creating Gwei that is paid to the recipient without a matching decrease anywhere in consensus state.

### Finding Description
`get_builder_withdrawals` builds withdrawals straight from the queue: [1](#0-0) 

Note the `amount=withdrawal.amount` on line 1827 is taken verbatim from `state.builder_pending_withdrawals[i].amount` — there is no `min()` against `builder.balance`, and no tracking of cumulative amounts already committed for the same `builder_index` within `prior_withdrawals`/`withdrawals` (unlike the validator-side sibling function, which explicitly recomputes `balance = get_balance_after_withdrawals(state, validator_index, all_withdrawals)` to account for prior withdrawals in the same batch, as seen at [2](#0-1) ).

The corresponding deduction is only capped afterwards, in `apply_withdrawals`: [3](#0-2) 

Line 1929 (`state.builders[builder_index].balance -= min(withdrawal.amount, builder_balance)`) saturates the deduction at the builder's current balance. But the `Withdrawal` object already placed in `payload_expected_withdrawals` — the value the execution layer is obligated to pay to `withdrawal.address` — still carries the original, uncapped `amount`.

### Impact Explanation
This breaks the equality "Gwei paid to a recipient == Gwei removed from consensus-layer accounting," which is the core invariant `apply_withdrawals`/`process_withdrawals` is supposed to enforce (the spec's own note on this function explicitly warns that any CL-side saturation of `decrease_balance` relative to a payload's committed amount "creates a net supply inflation," see [4](#0-3) ). Here that exact scenario is reachable purely through `get_builder_withdrawals`'s missing cap, without needing any cross-slot timing gap: if two pending withdrawals for the same builder are processed in the same call and their sum exceeds the builder's balance, the second one is fully honored in the payload but only partially (or not at all) deducted from `state.builders[...].balance`. This is Gwei paid to a (non-owner) recipient beyond what is actually backed by the builder's balance — matching the "Gwei created ... paid to a non-owner" Critical-impact category.

### Likelihood Explanation
Reachability depends on whether the entry point that appends to `state.builder_pending_withdrawals` (not located within the available index — likely a `process_builder_withdrawal_request`-style operation) itself validates that the sum of an individual request plus all currently-queued pending amounts does not exceed the builder's balance. I could not verify that check in the indexed content, so I cannot confirm with certainty whether an unprivileged builder can queue withdrawal requests summing to more than its balance. If such a check exists and is correct, `get_builder_withdrawals`'s missing cap is defense-in-depth-only and not independently exploitable; if it doesn't exist (or if a builder's balance can shrink between the request and processing, e.g., via slashing), the bug is directly triggerable and requires no coalition, timing race, or foreign key — only two ordinary builder-authored requests.

### Recommendation
`get_builder_withdrawals` should track cumulative withdrawal amounts per `builder_index` across `prior_withdrawals` and the withdrawals being built in the same call (mirroring `get_balance_after_withdrawals` used for validators), and cap each `Withdrawal.amount` at the builder's balance after accounting for all previously-included withdrawals for that same builder — so the amount placed into the execution payload can never exceed what `apply_withdrawals` will actually deduct.

### Proof of Concept
1. Builder `B` has `balance = X`.
2. Two `BuilderPendingWithdrawal` entries for `B` are queued, each with `amount = X` (e.g., via two separate withdrawal requests submitted before either is processed).
3. In a single call to `get_builder_withdrawals`, both entries are converted into `Withdrawal(amount=X)` for `B` (lines 1822-1829) — no cap or cross-check against `B`'s balance or against each other.
4. `process_withdrawals` commits both withdrawals (total `2X`) into `payload_expected_withdrawals`, which the execution layer is required to honor and pay to `B`'s fee recipients.
5. `apply_withdrawals` processes them in order: first withdrawal deducts `min(X, X) = X`, balance becomes `0`; second withdrawal deducts `min(X, 0) = 0`.
6. Net result: `2X` paid out by the execution layer, only `X` deducted from consensus-layer state — `X` Gwei created from nothing. [5](#0-4) [6](#0-5)

### Citations

**File:** specs/gloas/beacon-chain.md (L1804-1834)
```markdown
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

**File:** specs/electra/beacon-chain.md (L1381-1385)
```markdown
        validator_index = withdrawal.validator_index
        validator = state.validators[validator_index]
        balance = get_balance_after_withdrawals(state, validator_index, all_withdrawals)
        if is_eligible_for_partial_withdrawals(validator, balance):
            withdrawal_amount = min(balance - MIN_ACTIVATION_BALANCE, withdrawal.amount)
```
