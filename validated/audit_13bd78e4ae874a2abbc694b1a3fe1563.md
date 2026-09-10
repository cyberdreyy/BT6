## Analog Found: Committed Withdrawal Amount vs. Actual Builder-Balance Deduction Mismatch in Gloas `process_withdrawals`

### Summary
`get_builder_withdrawals` copies the requested `amount` from `state.builder_pending_withdrawals` verbatim into the `Withdrawal` object that becomes part of `state.payload_expected_withdrawals` — the amount the execution layer is committed to pay out — without capping it to the builder's actual balance. [1](#0-0) 
Moments later, in the same `process_withdrawals` call, `apply_withdrawals` deducts only `min(withdrawal.amount, builder_balance)` from the builder's tracked balance: [2](#0-1) 

This is structurally the same class of bug as the reported lending-pool issue: one code path computes a "nominal" amount (`repaid_amount` / `withdrawal.amount`) that is used for the externally-visible commitment (the amount transferred/paid), while a different, later calculation applies the actually-safe, capped amount (`calculate_shares` rounding down / `min(amount, balance)`) to the internal ledger — and the two are never reconciled.

### Finding Description
In `get_expected_withdrawals`, `get_builder_withdrawals` builds the `Withdrawal` entries for `state.payload_expected_withdrawals` using the raw, uncapped `amount` field taken from `state.builder_pending_withdrawals`:
```
amount=withdrawal.amount,
```
This is the amount that goes into the execution payload, which — per the withdrawals mechanism inherited from Capella/EIP-4895 — the execution layer is obligated to credit in full to `withdrawal.address`.

`apply_withdrawals`, called from the same `process_withdrawals` invocation with no intervening state mutation on `state.builders`, instead deducts:
```
state.builders[builder_index].balance -= min(withdrawal.amount, builder_balance)
```
i.e., explicitly acknowledging that `withdrawal.amount` may exceed `builder_balance`.

Because both functions read the *same* `state.builders[builder_index].balance` within the same call (no state change occurs between `get_expected_withdrawals` and `apply_withdrawals`), any state in which `withdrawal.amount > builder.balance` at the time `get_builder_withdrawals` runs produces a mismatch: the beacon block commits (via `payload_expected_withdrawals`, which the execution layer must honor) to paying `withdrawal.amount`, while the beacon state only reduces the builder's tracked balance by the smaller `builder.balance`. The execution layer mints the full committed amount to the fee recipient regardless of what the CL subtracted internally — this is the exact equality broken in the report's analog: the amount used to determine the external payment differs from the amount actually debited from the payer's ledger entry.

This is architecturally acknowledged as possible: the `min()` capping only makes sense if the authors expect `builder_pending_withdrawals[i].amount` can, in some state, exceed the current `builder.balance` (e.g., a payment queued via `apply_parent_execution_payload`'s "evicted payment" fallback path, which appends `BuilderPendingWithdrawal(amount=parent_bid.value, ...)` directly without re-checking `can_builder_cover_bid` against the builder's balance at commit time): [3](#0-2) 
and via `process_builder_pending_payments`, which moves any `BuilderPendingPayment` above quorum into `builder_pending_withdrawals` without any balance re-validation: [4](#0-3) 

### Impact Explanation
If `withdrawal.amount > builder.balance` occurs at the point `get_builder_withdrawals` computes the payload's withdrawal list, the execution layer will mint/pay the full `withdrawal.amount` to the fee recipient while the CL only reduces the builder's ledger by the smaller `builder.balance`. This is a direct instance of "Gwei created... paid to a non-owner" (Critical, per the rules): value is paid out by the EL that was never properly backed by an equivalent CL-side debit, and the CL's `builder.balance` floor at zero silently absorbs the shortfall instead of the whole withdrawal failing or being resized.

### Likelihood Explanation
I could not construct a fully concrete, protocol-legal path where `builder_pending_withdrawals[i].amount` provably exceeds `builder.balance` at the moment `get_builder_withdrawals` runs, because `can_builder_cover_bid` is designed to reserve exactly `MIN_DEPOSIT_AMOUNT + get_pending_balance_to_withdraw_for_builder(...)` against `builder.balance` at bid-acceptance time: [5](#0-4) 
and the only debit to `builder.balance` is via this same capped `apply_withdrawals` path, so by induction the invariant `balance >= sum(pending amounts)` should hold as long as `can_builder_cover_bid` is re-enforced at every point a new committed amount is added. I was not able to fully verify, within the remaining investigation budget, whether every insertion path into `builder_pending_payments`/`builder_pending_withdrawals` (in particular the "evicted payment" direct-append branch in `apply_parent_execution_payload`, and interactions across the ~2-epoch payment settlement window) is guaranteed to preserve this invariant, or whether there is a sequencing gap that lets it be violated. This is the key open question that would need to be resolved (ideally with a Devin session able to trace all `state.builders[*].balance` mutation and all `builder_pending_withdrawals`/`builder_pending_payments` insertion sites across the full spec) before this can be confirmed as concretely exploitable rather than defensive/dead code.

### Recommendation
Either (a) prove and document the invariant `builder.balance >= get_pending_balance_to_withdraw_for_builder(state, builder_index)` is maintained at all mutation sites so the `min()` in `apply_withdrawals` is truly unreachable dead code, or (b) make `get_builder_withdrawals` cap the committed `Withdrawal.amount` to `min(withdrawal.amount, builder.balance)` (mirroring `apply_withdrawals`) so the amount the execution layer is committed to pay always matches the amount actually debited from the builder's balance.

### Proof of Concept
Conceptual, within a single `process_withdrawals(state)` call:
1. Let `state.builders[B].balance = 10` and `state.builder_pending_withdrawals = [BuilderPendingWithdrawal(builder_index=B, amount=100, fee_recipient=addr)]` (e.g., reached via the uncapped `apply_parent_execution_payload` eviction-append path or via `process_builder_pending_payments` promoting a stale, no-longer-covered payment).
2. `get_expected_withdrawals` → `get_builder_withdrawals` emits `Withdrawal(validator_index=convert(B), address=addr, amount=100)` into `payload_expected_withdrawals`.
3. `apply_withdrawals` executes `state.builders[B].balance -= min(100, 10)` → `balance = 0`.
4. The execution layer, honoring `payload_expected_withdrawals`, credits `addr` with `100` Gwei, while the CL ledger for builder `B` only lost `10` Gwei — a net **90 Gwei created** with no corresponding CL-side debit, violating the balance-conservation equality (`total EL payout == total CL debit`).

Given the unresolved question about whether step 1's precondition is reachable through legitimate protocol operation, I recommend this be handed to a Devin session to trace every mutation/insertion site for `state.builders[*].balance`, `builder_pending_withdrawals`, and `builder_pending_payments` across `specs/gloas/beacon-chain.md` to conclusively determine reachability before treating this as a confirmed, exploitable Critical finding rather than a spec-text inconsistency.

### Citations

**File:** specs/gloas/beacon-chain.md (L1170-1179)
```markdown
def can_builder_cover_bid(
    state: BeaconState, builder_index: BuilderIndex, bid_amount: Gwei
) -> bool:
    builder_balance = state.builders[builder_index].balance
    pending_withdrawals_amount = get_pending_balance_to_withdraw_for_builder(state, builder_index)
    min_balance = MIN_DEPOSIT_AMOUNT + pending_withdrawals_amount
    if builder_balance < min_balance:
        return False
    return builder_balance - min_balance >= bid_amount
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

**File:** specs/gloas/beacon-chain.md (L1760-1770)
```markdown
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

**File:** specs/gloas/beacon-chain.md (L1922-1931)
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
