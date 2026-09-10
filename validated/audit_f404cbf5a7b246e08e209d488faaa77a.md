### Title
Builder payment settlement skips the PTC quorum check, letting a proposer/builder pair get paid without network-wide payload availability - ([File: specs/gloas/beacon-chain.md])

### Summary
In Gloas (ePBS), `settle_builder_payment`, which is invoked from `apply_parent_execution_payload` for the common "parent slot is in the current or previous epoch" case, pays out the builder's pending payment whenever `payment.withdrawal.amount > 0`, without checking `payment.weight` against `get_builder_payment_quorum_threshold`. The quorum/weight mechanism, which is supposed to gate payment on a majority-weighted PTC (payload-timeliness-committee) confirmation that the payload was actually delivered/available network-wide, is only enforced later, in the epoch-boundary function `process_builder_pending_payments` — but by the time that runs, any payment already reached by the "next block" fast path has already been settled and cleared to empty.

### Finding Description
`process_execution_payload_bid` creates a `BuilderPendingPayment` with `weight=Gwei(0)` for a new bid at slot `N`: [1](#0-0) 

`weight` for that payment is only incremented later, by `process_attestation`, when attestations for slot `N` (voting `payload_present`) are processed as part of a *subsequent* block's operations (confirmed by `test_builder_payment_weight_accumulates`).

The payment is normally settled one block later, as part of processing the parent's payload: [2](#0-1) 

```python
def settle_builder_payment(state: BeaconState, payment_index: Uint64) -> None:
    assert payment_index < len(state.builder_pending_payments)
    payment = state.builder_pending_payments[payment_index]
    if payment.withdrawal.amount > 0:
        state.builder_pending_withdrawals.append(payment.withdrawal)
    state.builder_pending_payments[payment_index] = BuilderPendingPayment.empty()
``` [3](#0-2) 

Crucially, in `process_block`, `process_parent_execution_payload` (which triggers this settlement) runs *before* `process_operations` (which processes the attestations that update `weight`): [4](#0-3) 

Because slot `N`'s attestations cannot exist before slot `N+1`, and `process_parent_execution_payload` for slot `N`'s payment fires at the very start of block `N+1` — before that same block's own attestations are processed — `payment.weight` is provably `0` at the moment `settle_builder_payment` runs for the "current/previous epoch" fast path. The `if payment.withdrawal.amount > 0` check therefore always fires whenever the immediate child block declares the parent FULL, completely bypassing the quorum machinery that `get_builder_payment_quorum_threshold` / `process_builder_pending_payments` implement for exactly this purpose: [5](#0-4) [6](#0-5) 

The only gate on entering the FULL/paid path is `process_parent_execution_payload`'s commitment check — that the next block's `execution_requests` hashes to the root the builder committed to, and that the bid's `parent_block_hash` matches: [7](#0-6) 

This is a proof that *the block N+1 proposer personally possesses* the payload data — it is **not** proof that the payload was ever broadcast to, or observed by, the wider network. The entire point of `weight`/`get_builder_payment_quorum_threshold`/PTC attestations is to require independent, majority-weighted confirmation of payload availability before paying the builder — precisely to prevent a single colluding proposer+builder pair from asserting "the payload was delivered" when it was only exchanged out-of-band between the two of them. The quorum gate designed for that scenario is unreachable in the common case because settlement always precedes any possibility of weight accumulation for that same payment index.

### Impact Explanation
A builder and the very next block's proposer — a two-party collusion requiring no majority stake and no other validator's cooperation — can privately exchange the actual `ExecutionRequests` data for a bid without ever publishing the corresponding `SignedExecutionPayloadEnvelope` to the network. The colluding proposer then builds the next block committing to the same root, declares the parent FULL, and `apply_parent_execution_payload` unconditionally settles and pays the builder (`state.builder_pending_withdrawals.append(payment.withdrawal)`), since `weight` is guaranteed to be `0` and is never checked in this branch. This breaks the invariant that a builder is paid *only if* a quorum-weighted majority of the network confirms the payload was actually made available in time — the exact equality `get_builder_payment_quorum_threshold`/PTC voting is meant to protect. This matches the "High" impact category: a builder payment escapes the availability check that the protocol otherwise enforces for every other settlement path (the epoch-boundary path).

### Likelihood Explanation
This is not a rare edge case — it is the *default* code path taken every single time a builder's payload is genuinely delivered and a block is built on top of it in the next slot (the overwhelmingly common case in ePBS operation), since `weight` can never be non-zero at settlement time for this branch. Any two colluding parties (one payload builder, one block proposer) can trigger it deterministically without needing broader network cooperation, extra stake, or timing luck.

### Recommendation
Either (a) check `payment.weight >= get_builder_payment_quorum_threshold(state)` inside `settle_builder_payment` (or before calling it) so the same quorum requirement enforced by `process_builder_pending_payments` also applies to the "current/previous epoch" fast-settlement path, or (b) redefine the fast path so it does not rely on `weight` at all but instead requires an independent, non-proposer-controlled proof of network-wide availability (e.g., only settle at epoch boundary via the existing quorum-gated function, removing the unconditional `settle_builder_payment` calls from `apply_parent_execution_payload`).

### Proof of Concept
1. Builder `B` submits a bid for slot `N` with `value = V > 0`; `process_execution_payload_bid` records `BuilderPendingPayment(weight=0, withdrawal=amount=V, ...)` at `builder_pending_payments[SLOTS_PER_EPOCH + N % SLOTS_PER_EPOCH]`.
2. `B` never publishes the `SignedExecutionPayloadEnvelope` for slot `N` over gossip; instead `B` sends the raw `ExecutionRequests`/payload data privately to the proposer `P` of slot `N+1`.
3. `P` builds block `N+1` with `parent_execution_requests` equal to the data received privately, computing `hash_tree_root(requests) == parent_bid.execution_requests_root`, and sets `signed_execution_payload_bid.message.parent_block_hash == state.latest_execution_payload_bid.block_hash` (declaring parent FULL).
4. State-transition of block `N+1`: `process_parent_execution_payload` passes the commitment check, `apply_parent_execution_payload` runs, `parent_epoch == get_current_epoch(state)` (or previous epoch), so `settle_builder_payment` is invoked. At this point `payment.weight == 0` (no attestations for slot `N` could have been processed yet), yet `payment.withdrawal.amount == V > 0`, so the withdrawal is appended to `state.builder_pending_withdrawals` unconditionally.
5. `B` gets paid `V` even though no PTC quorum ever confirmed `payload_present`/availability for slot `N`, and the rest of the network never received the payload — reproducible on every honest chain of two blocks where builder and proposer collude, with no reliance on `process_builder_pending_payments`'s quorum check ever being exercised for this payment.

### Citations

**File:** specs/gloas/beacon-chain.md (L1413-1423)
```markdown
#### New `get_builder_payment_quorum_threshold`

```python
def get_builder_payment_quorum_threshold(state: BeaconState) -> Uint64:
    """
    Calculate the quorum threshold for builder payments.
    """
    per_slot_balance = get_total_active_balance(state) // Uint64(SLOTS_PER_EPOCH)
    quorum = per_slot_balance * BUILDER_PAYMENT_THRESHOLD_NUMERATOR
    return Uint64(quorum // BUILDER_PAYMENT_THRESHOLD_DENOMINATOR)
```
```

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

**File:** specs/gloas/beacon-chain.md (L1661-1677)
```markdown
#### New `process_builder_pending_payments`

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

**File:** specs/gloas/beacon-chain.md (L1699-1717)
```markdown
def process_block(state: BeaconState, block: BeaconBlock) -> None:
    # [New in Gloas:EIP7732]
    parent_slot = state.latest_block_header.slot

    # [New in Gloas:EIP7732]
    process_parent_execution_payload(state, block)
    process_block_header(state, block)
    # [Modified in Gloas:EIP7732]
    process_withdrawals(state)
    # [Modified in Gloas:EIP7732]
    # Removed `process_execution_payload`
    # [New in Gloas:EIP7732]
    process_execution_payload_bid(state, block.body.signed_execution_payload_bid)
    process_randao(state, block.body)
    process_eth1_data(state, block.body)
    # [Modified in Gloas:EIP7732]
    process_operations(state, block.body, parent_slot)
    process_sync_aggregate(state, block.body.sync_aggregate)
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

**File:** specs/gloas/beacon-chain.md (L1784-1798)
```markdown
```python
def process_parent_execution_payload(state: BeaconState, block: BeaconBlock) -> None:
    bid = block.body.signed_execution_payload_bid.message
    parent_bid = state.latest_execution_payload_bid
    requests = block.body.parent_execution_requests

    if bid.parent_block_hash != parent_bid.block_hash:
        # Parent was EMPTY -- no execution requests expected
        assert requests == ExecutionRequests.empty()
        return

    # Parent was FULL -- verify the bid commitment and apply the payload
    assert hash_tree_root(requests) == parent_bid.execution_requests_root
    apply_parent_execution_payload(state, requests)
```
```

**File:** specs/gloas/beacon-chain.md (L2124-2141)
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
```
