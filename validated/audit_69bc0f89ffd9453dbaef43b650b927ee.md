### Title
Block-level `settle_builder_payment` releases builder payments without the PTC/attester weight quorum check enforced by `process_builder_pending_payments` - (File: specs/gloas/beacon-chain.md)

### Summary
In the Gloas fork, a builder's payment for a slot is only supposed to be released to `builder_pending_withdrawals` once enough attester weight (`payment.weight >= quorum`) has vouched for the availability of that slot's execution payload. `process_builder_pending_payments`, the epoch-boundary sweep, correctly enforces this gate. However, `apply_parent_execution_payload` — which runs on essentially every block via `process_parent_execution_payload` — calls `settle_builder_payment`, which releases the same payment based solely on `payment.withdrawal.amount > 0`, never checking `payment.weight` at all. Because `apply_parent_execution_payload` runs on the very next proposed block (well before any epoch boundary), it settles the payment first in virtually all cases, making the weight/quorum check in `process_builder_pending_payments` effectively dead code and bypassing the intended availability-attestation gate.

### Finding Description
Two independent code paths mutate/release the same `BuilderPendingPayment` entry, but only one applies the quorum check:

1. Epoch-level (`process_epoch` → `process_builder_pending_payments`), correctly gated: [1](#0-0) 

```python
def process_builder_pending_payments(state: BeaconState) -> None:
    quorum = get_builder_payment_quorum_threshold(state)
    for payment in state.builder_pending_payments[:SLOTS_PER_EPOCH]:
        if payment.weight >= quorum:
            state.builder_pending_withdrawals.append(payment.withdrawal)
    ...
```

2. Block-level (`process_block` → `process_parent_execution_payload` → `apply_parent_execution_payload` → `settle_builder_payment`), NOT gated by weight: [2](#0-1) 

```python
def settle_builder_payment(state: BeaconState, payment_index: Uint64) -> None:
    assert payment_index < len(state.builder_pending_payments)
    payment = state.builder_pending_payments[payment_index]
    if payment.withdrawal.amount > 0:
        state.builder_pending_withdrawals.append(payment.withdrawal)
    state.builder_pending_payments[payment_index] = BuilderPendingPayment.empty()
```

`settle_builder_payment` is invoked unconditionally for the immediately preceding slot whenever that slot's parent block was "full" (bid hash matched): [3](#0-2) 

```python
    if parent_epoch == get_current_epoch(state):
        payment_index = SLOTS_PER_EPOCH + parent_slot % SLOTS_PER_EPOCH
        settle_builder_payment(state, payment_index)
    elif parent_epoch == get_previous_epoch(state):
        payment_index = parent_slot % SLOTS_PER_EPOCH
        settle_builder_payment(state, payment_index)
```

`payment.weight` is only meant to be incremented when attesters commit to a `TIMELY_TARGET`-style flag for the *same slot* and confirm the payload as available: [4](#0-3) 

```python
        if (
            will_set_new_flag
            and had_no_participation
            and is_attestation_same_slot(state, data)
            and payment.withdrawal.amount > 0
        ):
            payment.weight += state.validators[index].effective_balance
```

This is the same structural bug class as the referenced report: `Vault.withdraw` synced the checkpoint (`highWaterMark`) via `syncFeeCheckpoint`, while the equivalent `Vault.redeem` path omitted it, letting fees be computed/paid against a stale, unsynced value. Here, `process_builder_pending_payments` "syncs" the payment release against the weight checkpoint, while the functionally-equivalent `settle_builder_payment` path (reached on essentially every block, i.e., far more frequently and *earlier* than the epoch sweep) omits the same check and pays out unconditionally. Since `apply_parent_execution_payload` runs on the next block for the immediately-preceding slot, it settles (and clears) the payment entry before the epoch boundary in the overwhelming majority of cases, meaning the quorum check almost never actually gets a chance to run against a still-populated entry with real weight accrued from later attestations in the epoch.

### Impact Explanation
This breaks the equality that a builder's escrowed bid amount should only be paid out (`builder_pending_withdrawals` entry created, later turned into an actual `Withdrawal` reducing `builder.balance`/minting an EL payment) once sufficient attester/PTC weight has vouched that the corresponding payload was actually available. Because the block-level path pays out regardless of weight, a builder (in cooperation with the very next proposer, which under EIP-7732 could be anyone building on top with `bid.parent_block_hash == parent_bid.block_hash`) receives its full committed payment even when no attesters/committee members ever confirmed payload availability for that slot. This is a Gwei payment released to a party without the authorization mechanism (attester quorum) the protocol requires — i.e., "a builder payment ... misdirected/escaped" its intended gating, falling into the Critical/High impact categories described (payment applied that the block did not actually get committed to by sufficient attestation weight).

### Likelihood Explanation
This is not a contrived edge case — it is the *default* code path. `process_parent_execution_payload`/`apply_parent_execution_payload` runs on nearly every produced block (as long as the parent slot's bid hash matches, which is the common, honest-proposer case), settling the previous slot's payment before the epoch boundary is ever reached. No coalition, no malicious client, and no partition is required; it follows directly from the specified control flow of `process_block`.

### Recommendation
Update `settle_builder_payment` to also enforce the weight quorum check before appending the payment's withdrawal, mirroring `process_builder_pending_payments`:

```python
def settle_builder_payment(state: BeaconState, payment_index: Uint64) -> None:
    assert payment_index < len(state.builder_pending_payments)
    payment = state.builder_pending_payments[payment_index]
    quorum = get_builder_payment_quorum_threshold(state)
    if payment.withdrawal.amount > 0 and payment.weight >= quorum:
        state.builder_pending_withdrawals.append(payment.withdrawal)
    state.builder_pending_payments[payment_index] = BuilderPendingPayment.empty()
```

Alternatively, defer settlement until the epoch boundary consistently so the weight accumulated across the full window is actually evaluated before any payout decision is made.

### Proof of Concept
1. A builder submits a winning bid for slot `N` with `value = V > 0`; `process_execution_payload_bid` records a `BuilderPendingPayment` with `weight = 0` and `withdrawal.amount = V`.
2. No (or insufficient) attesters/PTC members submit same-slot attestations confirming payload availability for slot `N`, so `payment.weight` never reaches `get_builder_payment_quorum_threshold(state)`.
3. The proposer of slot `N+1` builds on top of slot `N`'s payload with a matching `bid.parent_block_hash`, causing `process_parent_execution_payload` to treat the parent as FULL and call `apply_parent_execution_payload`.
4. `apply_parent_execution_payload` computes `payment_index` for slot `N` and calls `settle_builder_payment(state, payment_index)`, which appends `payment.withdrawal` (amount `V`) to `state.builder_pending_withdrawals` — with `weight` still `0`, i.e., far below quorum.
5. The queued withdrawal later executes through the ordinary withdrawal sweep, actually moving `V` Gwei to the builder's `fee_recipient`, even though quorum weight vouching for payload availability was never achieved — the exact quorum check that `process_builder_pending_payments` would have enforced never gets applied because the entry was already cleared to `BuilderPendingPayment.empty()` in step 4.

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

**File:** specs/gloas/beacon-chain.md (L2382-2389)
```markdown
        # [New in Gloas:EIP7732]
        if (
            will_set_new_flag
            and had_no_participation
            and is_attestation_same_slot(state, data)
            and payment.withdrawal.amount > 0
        ):
            payment.weight += state.validators[index].effective_balance
```
