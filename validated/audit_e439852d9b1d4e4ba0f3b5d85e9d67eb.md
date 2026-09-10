### Title
Builder Withdrawal Payload Commits an Unclamped Amount While `apply_withdrawals` Only Deducts the Clamped Balance, Creating Unbacked Gwei Paid to the Builder's Fee Recipient - (File: `specs/gloas/beacon-chain.md`)

### Summary
`get_builder_withdrawals` (used to compute `get_expected_withdrawals`, which is committed to the execution layer via `state.payload_expected_withdrawals`) emits a `Withdrawal` whose `amount` is copied verbatim from `state.builder_pending_withdrawals[i].amount`, with no clamp against the builder's current balance. `apply_withdrawals`, which performs the actual consensus-layer bookkeeping, instead clamps the deduction with `min(withdrawal.amount, builder_balance)`. If a builder's balance has been drawn down below the recorded pending-withdrawal amount by the time the withdrawal is processed, the two paths diverge: the execution layer is committed to mint/pay the builder's fee recipient the full, uncapped `withdrawal.amount`, while the beacon state only debits the smaller, clamped amount (or nothing, if balance is already zero). The difference is Gwei paid to the fee recipient that is never actually removed from any account — new Gwei effectively created and paid to a non-owner, exactly mirroring the CoreRouter bug class (a payout amount computed independently of, and not verified against, the actual balance-affecting operation).

### Finding Description
`get_builder_withdrawals` builds the payload-committed withdrawal list directly from `state.builder_pending_withdrawals`: [1](#0-0) 

Note the `amount=withdrawal.amount` on line 1827 — this is the amount that becomes part of `state.payload_expected_withdrawals` via `update_payload_expected_withdrawals`, which the execution layer is contractually bound to honor for the corresponding block: [2](#0-1) 

In contrast, `apply_withdrawals` — the function that actually mutates consensus-layer state for the *same* withdrawal object — clamps the deduction to the builder's live balance: [3](#0-2) 

The test documentation for this exact code path confirms the clamp is intentional and that the builder balance "can go to zero," i.e. the spec authors anticipated `withdrawal.amount > builder.balance` as a reachable case: [4](#0-3) 

The balance-sufficiency check `can_builder_cover_bid` is only enforced at the moment a *new* bid is accepted in `process_execution_payload_bid` (Gwei needed = new bid + existing pending payments + `MIN_ACTIVATION_BALANCE`). It is **not** re-verified when a pending payment is later settled into `state.builder_pending_withdrawals`. In particular, the "evicted" fallback path in `apply_parent_execution_payload` appends a withdrawal directly with the bid's original value and performs no balance check at all at settlement time: [5](#0-4) 

Because `state.builder_pending_withdrawals` is a FIFO queue that can accumulate multiple entries for the same builder across several blocks/epochs, and because `apply_withdrawals` processes queue entries in order — draining a builder's balance as earlier queued withdrawals are applied — a later queued withdrawal for the same builder can find `builder.balance` already reduced below its own recorded `amount` by the time it is popped off the queue and turned into a payload commitment by `get_builder_withdrawals`. At that point:
- `get_builder_withdrawals`/`get_expected_withdrawals` still commits the *original, unclamped* `amount` to the execution payload (paid to `withdrawal.fee_recipient`).
- `apply_withdrawals` only decreases `state.builders[builder_index].balance` by `min(withdrawal.amount, builder_balance)`.

This breaks the equality that every Gwei paid out via an execution-layer withdrawal must correspond to an equal decrease somewhere in beacon-state balances. The excess is Gwei created and paid to a non-owner (the builder's `fee_recipient`) with no offsetting deduction anywhere in `BeaconState`.

### Impact Explanation
This is a Critical-severity issue under the stated rubric ("Gwei created, destroyed or paid to a non-owner"). The execution layer is required to honor `state.payload_expected_withdrawals` exactly; if the beacon state and execution layer disagree on the amount actually backed by a balance decrease, the chain's total-Gwei-supply invariant (sum of validator/builder balances plus withdrawn amounts must be conserved) is violated. Repeated occurrences (e.g., a builder that queues several sizable bids and is drained by earlier withdrawals in the queue) would let a builder extract more Gwei via a fee recipient than the protocol ever debited from its own account.

### Likelihood Explanation
Reaching the divergent state does not require a malicious peer, client bug, or coalition — it only requires that a builder have more than one pending withdrawal queued (achievable through ordinary bidding/settlement flow, including the explicit "evicted"/fallback direct-append path that skips any balance check) such that by the time the second (or later) entry is dequeued, the builder's own balance has already been consumed by the earlier entries or other builder-balance-decreasing operations. The spec's own test commentary explicitly acknowledges "builder balance can go to zero," meaning under-collateralized settlement is a recognized, reachable state — the missing piece is that the *payload commitment* (`get_builder_withdrawals`) does not apply the same clamp that `apply_withdrawals` does.

### Recommendation
Make `get_builder_withdrawals` compute the same clamped amount that `apply_withdrawals` will actually deduct, e.g. track a running "balance after prior withdrawals in this block" per builder (similar to `get_balance_after_withdrawals` used for validators) and set `amount = min(withdrawal.amount, projected_builder_balance)` when constructing the committed `Withdrawal`, so the execution-payload commitment and the beacon-state deduction can never diverge.

### Proof of Concept
Conceptual state walk-through:
1. Builder `B` has `balance = 100`.
2. Two `BuilderPendingWithdrawal` entries end up queued for `B` in `state.builder_pending_withdrawals`, each with `amount = 80` (e.g., one created normally via `settle_builder_payment`, a second appended via the balance-unchecked "evicted" branch in `apply_parent_execution_payload` after a missed-epoch payment).
3. In a subsequent slot's `get_expected_withdrawals` → `get_builder_withdrawals`, both are turned into `Withdrawal(amount=80)` and `Withdrawal(amount=80)` and placed into `state.payload_expected_withdrawals` — total 160 Gwei committed to be paid to `B`'s fee recipient(s).
4. `apply_withdrawals` processes them in order: first iteration, `builder_balance = 100`, deducts `min(80,100)=80`, `balance` becomes 20. Second iteration, `builder_balance = 20`, deducts `min(80,20)=20`, `balance` becomes 0.
5. Total actually deducted from `B`'s balance: 100. Total committed/paid via the execution payload: 160. The 60 Gwei difference is paid to the fee recipient(s) with no corresponding decrease anywhere in `BeaconState`, violating supply conservation.

### Citations

**File:** specs/gloas/beacon-chain.md (L1754-1770)
```markdown
    # Settle the builder payment
    if parent_epoch == get_current_epoch(state):
        payment_index = SLOTS_PER_EPOCH + parent_slot % SLOTS_PER_EPOCH
        settle_builder_payment(state, payment_index)
    elif parent_epoch == get_previous_epoch(state):
        payment_index = parent_slot % SLOTS_PER_EPOCH
        settle_builder_payment(state, payment_index)
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

**File:** specs/gloas/beacon-chain.md (L1802-1834)
```markdown
##### New `get_builder_withdrawals`

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

**File:** specs/gloas/beacon-chain.md (L1934-1941)
```markdown
##### New `update_payload_expected_withdrawals`

```python
def update_payload_expected_withdrawals(
    state: BeaconState, withdrawals: Sequence[Withdrawal]
) -> None:
    state.payload_expected_withdrawals = Withdrawals(data=withdrawals)
```
```

**File:** tests/core/pyspec/eth_consensus_specs/test/gloas/block_processing/test_process_withdrawals.md (L99-105)
```markdown
3. **Balance Thresholds by Withdrawal Type**:

   - **Builder pending**:
     `actual_amount = min(requested_amount, builder.balance)` (builder balance
     can go to zero)
   - **Builder sweep**: requires `builder.withdrawable_epoch <= epoch` AND
     `builder.balance > 0`; withdraws full `builder.balance`
```
