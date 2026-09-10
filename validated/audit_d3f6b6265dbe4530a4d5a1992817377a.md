### Title
Builder payments settle unconditionally in `apply_parent_execution_payload`, bypassing the attester-weight quorum gate meant to condition payment - (File: `specs/gloas/beacon-chain.md`)

### Summary
`settle_builder_payment()` releases a builder's pending payment into `state.builder_pending_withdrawals` whenever `payment.withdrawal.amount > 0`, without ever checking `payment.weight` against the quorum threshold that the spec elsewhere treats as the condition for paying a builder. Because `settle_builder_payment` is invoked from `apply_parent_execution_payload` in the very next block after the parent's payload is included — long before the epoch-boundary quorum check (`process_builder_pending_payments`) ever runs on that same slot — the weight/quorum gate is effectively dead code for the standard "parent was full" path. The builder is paid unconditionally as soon as any child block processes the parent's payload, regardless of whether the attesting committee actually accumulated `weight >= quorum`.

### Finding Description
Two different functions decide whether a `BuilderPendingPayment` becomes a real withdrawal:

1. `process_builder_pending_payments` (epoch processing) — the only place that checks the quorum: [1](#0-0) 

2. `settle_builder_payment` — invoked per-block from `apply_parent_execution_payload`, which appends the withdrawal purely based on `amount > 0`, with no reference to `weight`: [2](#0-1) 

`apply_parent_execution_payload` calls `settle_builder_payment` for the *same* `payment_index` that the weight-accumulation logic in `process_attestation` is still trying to fill, and it does so as soon as the very next block processes the parent payload (i.e., a single slot later), not at epoch boundary: [3](#0-2) 

The payment's `weight` field is populated incrementally as same-slot attestations are processed, via `process_attestation`: [4](#0-3) 

Because `settle_builder_payment` clears the payment slot (`state.builder_pending_payments[payment_index] = BuilderPendingPayment.empty()`) in the very next block, by the time `process_builder_pending_payments` runs at the epoch boundary and would apply the `weight >= quorum` test, the slot has almost always already been drained through the unconditional per-block path. The quorum-gated epoch function only ever sees payments for slots where the parent payload was *not* processed by the immediate next block (e.g., after skipped slots), which is the exceptional case, not the common one.

This creates a genuine equality break: the spec's own container comment and the `process_attestation` weight-tracking logic establish that a builder payment should require `weight >= get_builder_payment_quorum_threshold(state)` (i.e., a committee-confirmed condition) before the withdrawal is created. But the dominant code path (`apply_parent_execution_payload` → `settle_builder_payment`) pays the builder without ever consulting `weight`, so the payment happens irrespective of whether the committee actually attested support for that slot's payload.

### Impact Explanation
This falls under "a builder payment or withdrawal misdirected, doubled, or escaped" (High-severity criterion): the payment "escapes" the quorum condition that the protocol's own weight-accumulation and quorum-check machinery is designed to enforce. In the common case, any proposer simply following the spec's honest block-production logic (including the parent's payload) triggers payout to the builder unconditionally — no attester weight is required at all, since the field is checked nowhere in the dominant settlement path. This defeats the purpose of the PTC/attestation weight mechanism (presumably meant to withhold payment from a builder whose payload wasn't sufficiently attested/available), letting builder payments be settled regardless of actual committee confirmation.

### Likelihood Explanation
This triggers on every ordinary block that follows a "full" parent slot — it does not require any adversarial coalition, equivocation, or malicious peer. It is a deterministic consequence of correctly implementing `process_block` → `process_parent_execution_payload` → `apply_parent_execution_payload` → `settle_builder_payment` as specified. Because it happens on the "happy path" for essentially all blocks, likelihood is high; this is a state-transition-level logic bug, not merely a theoretical corner case.

### Recommendation
`settle_builder_payment` should gate the withdrawal on `payment.weight >= get_builder_payment_quorum_threshold(state)`, mirroring the condition already implemented in `process_builder_pending_payments`, instead of unconditionally appending whenever `amount > 0`. Alternatively, if quorum-gating is only intended for stale/evicted payments (the `elif parent_bid.value > 0` fallback branch), the spec text and comments should make explicit why the primary per-block settlement path is exempt from the weight check, and that rationale should be reconciled with the attestation weight-tracking logic that currently appears to serve no purpose in the common path.

### Proof of Concept
Conceptual state-transition trace (pure spec-level, no client bugs required):
1. At slot `N`, a builder submits `SignedExecutionPayloadBid` with `value = V > 0`; `process_execution_payload_bid` records a `BuilderPendingPayment` with `weight = 0` at index `SLOTS_PER_EPOCH + N % SLOTS_PER_EPOCH`. [5](#0-4) 
2. Suppose the entire committee for slot `N` fails to attest same-slot (e.g. attestations are late/withheld), so `payment.weight` stays at `0`, far below `get_builder_payment_quorum_threshold(state)`.
3. At slot `N+1`, the proposer includes the parent's (slot `N`) full execution payload. `process_parent_execution_payload` → `apply_parent_execution_payload` runs, and since `parent_epoch == get_current_epoch(state)`, it calls `settle_builder_payment(state, payment_index)`. [3](#0-2) 
4. `settle_builder_payment` sees `payment.withdrawal.amount == V > 0` and unconditionally appends the withdrawal to `state.builder_pending_withdrawals`, then clears the slot — **without ever checking `payment.weight` against quorum.** [2](#0-1) 
5. The builder is subsequently paid via the normal withdrawal sweep (`get_builder_withdrawals`), even though `weight (0) < quorum`, i.e., even though the committee never confirmed the payload was seen/valid enough to warrant payment under the quorum rule that `process_builder_pending_payments` would have otherwise enforced.

I was not able to retrieve the exact body of `get_builder_payment_quorum_threshold` in this pass (only test usages were indexed), so I cannot confirm the precise numeric threshold it computes; however, this does not affect the core finding, since the bug is that `settle_builder_payment` never references `weight`/quorum at all, regardless of the threshold's value.

### Citations

**File:** specs/gloas/beacon-chain.md (L1518-1527)
```markdown
#### New `settle_builder_payment`

```python
def settle_builder_payment(state: BeaconState, payment_index: Uint64) -> None:
    assert payment_index < len(state.builder_pending_payments)
    payment = state.builder_pending_payments[payment_index]
    if payment.withdrawal.amount > 0:
        state.builder_pending_withdrawals.append(payment.withdrawal)
    state.builder_pending_payments[payment_index] = BuilderPendingPayment.empty()
```
```

**File:** specs/gloas/beacon-chain.md (L1663-1677)
```markdown
```python
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

**File:** specs/gloas/beacon-chain.md (L1754-1761)
```markdown
    # Settle the builder payment
    if parent_epoch == get_current_epoch(state):
        payment_index = SLOTS_PER_EPOCH + parent_slot % SLOTS_PER_EPOCH
        settle_builder_payment(state, payment_index)
    elif parent_epoch == get_previous_epoch(state):
        payment_index = parent_slot % SLOTS_PER_EPOCH
        settle_builder_payment(state, payment_index)
    elif parent_bid.value > 0:
```

**File:** specs/gloas/beacon-chain.md (L2124-2140)
```markdown
    # Record the pending payment if there is some payment
    if amount > 0:
        pending_payment = BuilderPendingPayment(
            weight=Gwei(0),
            withdrawal=BuilderPendingWithdrawal(
                fee_recipient=bid.fee_recipient,
                amount=amount,
                builder_index=builder_index,
            ),
            proposer_index=get_beacon_proposer_index(state),
        )
        state.builder_pending_payments[SLOTS_PER_EPOCH + bid.slot % SLOTS_PER_EPOCH] = (
            pending_payment
        )

    # Cache the signed execution payload bid
    state.latest_execution_payload_bid = bid
```

**File:** specs/gloas/beacon-chain.md (L2382-2403)
```markdown
        # [New in Gloas:EIP7732]
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
