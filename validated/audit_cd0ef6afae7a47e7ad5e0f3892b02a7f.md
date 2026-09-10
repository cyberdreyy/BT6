### Title
Builder full-balance sweep withdrawal double-counts balance already committed to pending withdrawals, causing unbacked Gwei to be minted on the execution layer - (File: specs/gloas/beacon-chain.md)

### Summary
`get_builders_sweep_withdrawals` computes the sweep withdrawal amount as the builder's raw `balance` field, without subtracting any amount already committed in `state.builder_pending_withdrawals` for the same slot. When both a pending builder payment and a builder-exit sweep fire for the same builder inside the same `process_withdrawals` call, the two `Withdrawal` objects committed to the execution layer sum to more than the builder's actual balance, while `apply_withdrawals` only ever deducts up to the real balance from `state.builders[...].balance`. This breaks the equality between CL-side balance debit and EL-side minted value.

### Finding Description
`get_expected_withdrawals` builds the withdrawal list in this order [1](#0-0) :
1. `get_builder_withdrawals` — emits `Withdrawal(amount=withdrawal.amount, address=fee_recipient)` for each entry in `state.builder_pending_withdrawals`, taken verbatim from the stored amount [2](#0-1) .
2. `get_builders_sweep_withdrawals` — for any builder with `withdrawable_epoch <= epoch and balance > 0`, emits `Withdrawal(amount=builder.balance, address=execution_address)`, reading the **un-decremented** `builder.balance` field at list-build time [3](#0-2) .

Both withdrawal entries are computed from the *same* pre-mutation state before any deduction happens. `apply_withdrawals` is applied afterward and only caps the *CL-internal* balance deduction to what's actually left:
```
builder_balance = state.builders[builder_index].balance
state.builders[builder_index].balance -= min(withdrawal.amount, builder_balance)
``` [4](#0-3) 

So if builder balance is `B` and a pending withdrawal of `W` (`W ≤ B`) is queued for that same builder:
- First withdrawal applied: deduct `min(W, B) = W` → balance becomes `B-W`.
- Sweep withdrawal (amount fixed at `B`, computed before any deduction): deduct `min(B, B-W) = B-W` → balance becomes `0`.

Total CL debit = `W + (B-W) = B` (correct, no CL underflow). But the two `Withdrawal` SSZ objects sent to the execution layer carry amounts `W` (to `fee_recipient`) and `B` (to `execution_address`) — summing to `B+W`. Per the spec's own withdrawal-processing note, "any execution payload that has the corresponding block as parent beacon block is required to honor these withdrawals in the execution layer" and "the execution layer mints the full committed amount regardless" [5](#0-4) . Thus the EL mints `B+W` while the CL only ever had `B` worth of builder balance — `W` Gwei is created from nothing and paid out to `fee_recipient`, an address that did not actually have a claim on that portion once the sweep consumed the full remaining balance.

This is directly analogous to the reported Golom bug class: a pre-computed commitment/amount (`bid.value`, captured into `builder_pending_withdrawals`) becomes stale relative to the account's true remaining balance by the time settlement occurs, and the settlement path (`apply_withdrawals`) silently reconciles/caps one side of the ledger (the CL balance) without correspondingly adjusting the other side (the EL-committed `Withdrawal.amount`), so the two sides of the equality diverge instead of both failing or both succeeding consistently.

The scenario is reachable through ordinary honest-looking protocol operation: a builder can have outstanding `builder_pending_withdrawals`/`builder_pending_payments` (which can take multiple slots to drain due to the `MAX_WITHDRAWALS_PER_PAYLOAD - 1` cap per payload, see `get_builder_withdrawals`) while also being active enough to submit one more winning bid before its `withdrawable_epoch` (set by `initiate_builder_exit`, `MIN_BUILDER_WITHDRAWABILITY_DELAY` slots out [6](#0-5) ) is finally reached. Once `withdrawable_epoch <= epoch`, the sweep fires for the full stale `balance` in the same payload that is still draining the pending-withdrawal queue for that builder.

### Impact Explanation
This is a Critical-class finding under the given rubric: Gwei is created and paid to a non-owner/beyond-entitlement address — the execution layer mints value (`B+W`) that exceeds what the consensus layer ever actually removed from any account (`B`). It is a payment applied (on the EL) that the CL's own bookkeeping does not back, breaking the fundamental total-supply/ledger equality between the two layers that `process_withdrawals` is supposed to guarantee.

### Likelihood Explanation
No malicious coalition or client bug is required. It only requires ordinary timing: a builder queued for exit whose pending payment/withdrawal backlog has not fully drained by the time its `withdrawable_epoch` arrives — plausible whenever withdrawal traffic is high (bounded by `MAX_WITHDRAWALS_PER_PAYLOAD - 1` per slot) or `MIN_BUILDER_WITHDRAWABILITY_DELAY` is short relative to backlog. It is fully deterministic and reproducible by any spec-following node, so all clients would agree on the (incorrect) result — it is not a fork-choice/consensus split, but a silent, universally-applied supply-inflation bug.

### Recommendation
`get_builders_sweep_withdrawals` should compute the sweep amount net of any balance already reserved by `builder_pending_withdrawals` / `builder_pending_payments` for that builder (analogous to `get_pending_balance_to_withdraw_for_builder` used by `can_builder_cover_bid`), i.e. `amount = builder.balance - get_pending_balance_to_withdraw_for_builder(state, builder_index)`, rather than the raw `builder.balance` field, so the sum of all `Withdrawal.amount` entries committed to the EL for a given builder in one payload never exceeds that builder's actual balance.

### Proof of Concept
1. Builder `i` has `balance = B`.
2. A bid from builder `i` is accepted with `value = W` (`W ≤ B - MIN_DEPOSIT_AMOUNT`), creating a `BuilderPendingPayment`/`BuilderPendingWithdrawal` of amount `W` that is still sitting in `state.builder_pending_withdrawals` (not yet drained because of the `MAX_WITHDRAWALS_PER_PAYLOAD - 1` cap or epoch-boundary timing).
3. Builder `i` had earlier submitted a `BuilderExitRequest`; `initiate_builder_exit` set `withdrawable_epoch = current_epoch + MIN_BUILDER_WITHDRAWABILITY_DELAY`, and that epoch is now reached.
4. In one call to `process_withdrawals`:
   - `get_builder_withdrawals` emits `Withdrawal(amount=W, address=fee_recipient)`.
   - `get_builders_sweep_withdrawals` emits `Withdrawal(amount=B, address=execution_address)` (reading the still-full `builder.balance`).
5. `apply_withdrawals` processes them in order: deducts `W` then `B-W`, leaving `state.builders[i].balance == 0` — internally consistent for the CL.
6. The execution payload nonetheless commits to minting `W + B` total to the two addresses, `W` more than the builder ever had, per the spec's stated rule that the EL "mints the full committed amount regardless" [7](#0-6) .

### Citations

**File:** specs/gloas/beacon-chain.md (L1506-1516)
```markdown
#### New `initiate_builder_exit`

```python
def initiate_builder_exit(state: BeaconState, builder_index: BuilderIndex) -> None:
    """
    Initiate the exit of the builder with index ``index``.
    """
    # Set builder exit epoch
    builder = state.builders[builder_index]
    builder.withdrawable_epoch = get_current_epoch(state) + MIN_BUILDER_WITHDRAWABILITY_DELAY
```
```

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

**File:** specs/gloas/beacon-chain.md (L1879-1901)
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

**File:** specs/gloas/beacon-chain.md (L1977-1990)
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
