### Title
Double payment to a builder when a slow/missed-slot payload's PTC attestation weight reaches quorum before the payload is settled - (File: specs/gloas/beacon-chain.md)

### Summary
`apply_parent_execution_payload` in the Gloas fork has two independent, non-communicating settlement paths for a builder payment recorded in `state.builder_pending_payments`. When a parent block's child is delayed by more than one full epoch (i.e., the parent's slot ages out of the two-epoch payment queue window), the epoch-boundary function `process_builder_pending_payments` may already have paid the builder via the PTC-attestation-weight "quorum" mechanism, silently evicting the queue entry. When the real child block for that parent eventually arrives, `apply_parent_execution_payload` falls into the `elif parent_bid.value > 0` branch and unconditionally creates a *second* `BuilderPendingWithdrawal` directly from `state.latest_execution_payload_bid`, because that cached bid retains the payment amount independently of the (now-emptied) `builder_pending_payments` queue slot.

### Finding Description
`process_builder_pending_payments` (`specs/gloas/beacon-chain.md:1663-1677`) runs every epoch and pays out any payment whose accumulated PTC weight is `>= quorum`, purely based on epoch counting, with no knowledge of whether the corresponding block was ever actually built on: [1](#0-0) 

`apply_parent_execution_payload` (`specs/gloas/beacon-chain.md:1729-1775`) settles the same payment via a completely separate code path when the actual child block finally shows up: [2](#0-1) 

`settle_builder_payment` (`specs/gloas/beacon-chain.md:1521-1527`) is only used when the parent's payment index still falls inside the live two-epoch window (`parent_epoch == current_epoch` or `parent_epoch == previous_epoch`). It reads and *clears* `state.builder_pending_payments[payment_index]`: [3](#0-2) 

If the child is delayed further (more than 2 epochs — multiple missed slots), `apply_parent_execution_payload` no longer touches `builder_pending_payments` at all. It uses `parent_bid.value` (equivalently `parent_bid.value > 0`) from `state.latest_execution_payload_bid`, which is a value cached independently of the payments queue, and unconditionally appends a fresh withdrawal: [4](#0-3) 

The break in the equality: PTC members vote on payload *timeliness* (was the payload revealed on time), which is orthogonal to whether the *next slot's proposer* actually proposes a block. It is entirely possible for enough weight (validator effective balance) to accumulate via `process_attestation`'s same-slot weight tracking (see `state.builder_pending_payments[...].weight` updates exercised by `test_builder_payment_weight_tracking` and `test_builder_payment_weight_accumulates`) to reach the quorum threshold from `get_builder_payment_quorum_threshold`, while subsequent slots are missed by unrelated proposers for more than one full epoch. In that case:
1. At the 2-epoch boundary, `process_builder_pending_payments` sees `payment.weight >= quorum` and appends `payment.withdrawal` to `state.builder_pending_withdrawals` — Withdrawal #1.
2. The rotation subsequently overwrites (evicts) that queue slot, destroying the record that it was already paid.
3. When a proposer eventually builds a child referencing the same still-unsettled parent (`state.latest_block_hash` has not changed because `apply_parent_execution_payload` never ran for it), `process_parent_execution_payload` → `apply_parent_execution_payload` computes `parent_epoch` as older than `previous_epoch`, takes the `elif parent_bid.value > 0` branch, and appends a *second* `BuilderPendingWithdrawal` for the identical `fee_recipient`/`amount`/`builder_index` — Withdrawal #2.

Both withdrawals are later paid out via `get_builder_withdrawals`/`apply_withdrawals`, so the builder's `fee_recipient` is credited twice for a single execution payload while nobody's balance is decreased a second time to fund it — Gwei is created out of thin air and paid to a specific party.

This is distinct from the bug already fixed and regression-tested by `test_builder_payment_after_missed_epochs` (`tests/core/pyspec/eth_consensus_specs/test/gloas/sanity/test_blocks.py:782-856`), which only covers the case where the delayed payment's `weight` stayed at `0` (no attestations reached quorum), so `process_builder_pending_payments` never paid it and the `elif` branch pays exactly once: [5](#0-4) 

That fix/test does not cover — and the current code does not guard against — the case where quorum *is* reached before the multi-epoch delay elapses.

### Impact Explanation
This breaks the "Gwei created or paid to a non-owner" invariant: the protocol pays a builder's `fee_recipient` twice for one payload, minting Gwei that was never backed by a corresponding balance decrease (the builder's balance is only decreased once, at actual withdrawal-application time, per payment; two pending-withdrawal entries for the same payment both draw down real balance twice, or if uncapped, simply mint value at the execution-layer withdrawal target). This is a Critical-class issue per the campaign's impact criteria (Gwei created/paid to a non-owner).

### Likelihood Explanation
Requires: (a) a builder payload whose PTC attestation weight organically reaches the quorum threshold (a normal, expected outcome for any reasonably well-attested payload), and (b) the very next slot(s) being missed by unrelated proposers for more than one full epoch (rare but not implausible during periods of poor network conditions / proposer downtime), with the missed-slot chain eventually being extended rather than orphaned. No malicious coalition, privileged role, or attacker-controlled key is required — it can occur from ordinary network conditions, satisfying the "unprivileged analog" requirement.

### Recommendation
Track whether a payment has already been settled (e.g., retain an explicit "settled" flag or move the fallback withdrawal creation to only trigger when the payment was evicted from the queue *without* reaching quorum). Concretely, `process_builder_pending_payments` should record settlement disposition (paid vs. dropped) per slot in a structure that `apply_parent_execution_payload`'s late-arrival branch can consult, or the fallback branch should be removed in favor of extending the payment queue's window so a payment is never evicted before its corresponding parent slot has been confirmed as processed (settled or genuinely orphaned).

### Proof of Concept
Conceptual state trace (extends the existing `test_builder_payment_after_missed_epochs` scaffolding):
1. Build `block_1` at slot `S` with a non-self-build bid of `value > 0` from `builder_index`; process it — creates `builder_pending_payments[SLOTS_PER_EPOCH + S % SLOTS_PER_EPOCH]` with `weight=0`.
2. For subsequent slots within epoch `E = compute_epoch_at_slot(S)`, have PTC-member validators submit valid `PayloadAttestation`s / same-slot attestations targeting slot `S`'s payload as timely, accumulating `weight` in that queue entry until `weight >= get_builder_payment_quorum_threshold(state)` — while *not* building any further child blocks (simulate consecutive missed slots by every subsequent proposer for `> SLOTS_PER_EPOCH` slots, analogous to how `test_builder_payment_after_missed_epochs` skips slots).
3. Advance `process_slots` past two epoch boundaries without ever calling `process_parent_execution_payload` for slot `S` (i.e., without a block whose `latest_block_header.slot == S` being processed) — at the second boundary, `process_builder_pending_payments` observes `payment.weight >= quorum` for the (now-first-half) entry and appends `payment.withdrawal` to `state.builder_pending_withdrawals` (Withdrawal #1), then the rotation evicts the slot.
4. Finally build `block_2` referencing `bid.parent_block_hash == block_hash` (same as in the existing test), whose `latest_block_header.slot == S` when processed. `apply_parent_execution_payload` computes `parent_epoch` as older than `previous_epoch`, and since `parent_bid.value > 0`, appends a second `BuilderPendingWithdrawal` with identical `fee_recipient`/`amount`/`builder_index` (Withdrawal #2).
5. Assert `len(state.builder_pending_withdrawals)` reflects two entries for the same builder payment, and that the builder's balance is debited/its `fee_recipient` credited twice for one payload — confirming the double payment.

I was not able to execute this trace against the pyspec test harness (no code execution capability available in this environment), so step-by-step confirmation of the exact PTC weight accumulation mechanics (`get_builder_payment_quorum_threshold`, `process_attestation`'s weight-increment conditions) relies on the test files cited above (`test_process_attestation.py` lines 247-299/518-655) rather than direct execution; a Devin session with repo access and a test runner would be needed to run this scenario concretely and confirm the double withdrawal.

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

**File:** specs/gloas/beacon-chain.md (L1754-1775)
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

    # Update parent payload availability and latest block hash
    state.execution_payload_availability[parent_slot % SLOTS_PER_HISTORICAL_ROOT] = Boolean(True)
    state.latest_block_hash = parent_bid.block_hash
```
```

**File:** tests/core/pyspec/eth_consensus_specs/test/gloas/sanity/test_blocks.py (L834-841)
```python
    # Build Block 2 with 2+ epochs of missed slots. During the slot advancement,
    # process_builder_pending_payments runs at each epoch boundary:
    #   1st boundary: shifts payment from second half to first half
    #   2nd boundary: checks quorum on first half — weight 0 < quorum → evicted
    # When Block 2 is processed, parent is FULL so apply_parent_execution_payload
    # runs. Since parent_epoch is older than previous_epoch, payment_index is None.
    # The fix creates the withdrawal directly from the bid in this case.
    block_1_epoch = spec.compute_epoch_at_slot(block_1.slot)
```
