### Title
Builder payment bypasses attester-quorum authorization when parent block settlement is delayed past 2 epochs - ([File: specs/gloas/beacon-chain.md])

### Summary
`apply_parent_execution_payload` normally releases a builder's payment only through `settle_builder_payment`, which is gated on `process_builder_pending_payments` having already checked `payment.weight >= quorum` — i.e., only if enough attesters (weighted by effective balance) confirmed the payload was delivered on time. But when the parent block's payment entry has fallen out of the `SLOTS_PER_EPOCH`-sized rolling window (parent's epoch is older than `get_previous_epoch(state)`), the code takes a fallback branch that appends a `BuilderPendingWithdrawal` directly from the bid, with **no quorum/weight check at all**.

### Finding Description
The intended invariant for builder payment release in Gloas ePBS is: a builder is paid only if `BuilderPendingPayment.weight >= get_builder_payment_quorum_threshold(state)`, enforced in `process_builder_pending_payments`: [1](#0-0) 

Weight is only accumulated when attesters vote for the block in the same slot it was proposed, via `process_attestation`: [2](#0-1) 

This weight lives in `state.builder_pending_payments`, a list of size `2 * SLOTS_PER_EPOCH` (current+previous epoch window). Each epoch transition rotates/evicts entries older than the window via `process_builder_pending_payments`. When a parent block's execution payload is finally applied in `apply_parent_execution_payload` (called from `process_parent_execution_payload`), the settlement path is: [3](#0-2) 

- If the parent's epoch is still in the current or previous epoch, `settle_builder_payment(state, payment_index)` is called, which only creates a `BuilderPendingWithdrawal` `if payment.withdrawal.amount > 0` — but by construction this branch reads the exact same `builder_pending_payments` slot that `process_builder_pending_payments` would otherwise have gated on quorum. Crucially, note `settle_builder_payment` itself does **not** check `weight >= quorum`: [4](#0-3) 
  (This relies on the entry already having been evicted-with-quorum-check by epoch processing before this branch can be hit; the intended quorum enforcement is `process_builder_pending_payments`.)
- If the parent's epoch is now older than `get_previous_epoch(state)` (i.e., 2+ epochs have elapsed with no intervening block importing the parent's payload — for example due to skipped/missed slots), the payment entry has already been evicted from `builder_pending_payments` by prior epoch-boundary calls to `process_builder_pending_payments` (which would have dropped it entirely if `weight < quorum`, or already turned it into a withdrawal if `weight >= quorum`). The fallback branch, however, ignores this history and unconditionally re-derives a *new* `BuilderPendingWithdrawal` straight from `parent_bid.fee_recipient` / `parent_bid.value` / `parent_bid.builder_index`, with **no reference to `payment.weight`** at all.

The test `test_builder_payment_after_missed_epochs` in the test suite explicitly documents and exercises this exact fallback path — a builder bid with `weight == 0` (i.e., **no attesters ever voted** for the block) still results in the builder being fully charged/paid once the block is finally re-included 2+ epochs later: [5](#0-4) 

This breaks the equality the quorum mechanism is designed to enforce: *a builder payment must only be released with a quorum-weighted vote from attesters confirming the corresponding payload was actually delivered/available*. In the delayed-settlement fallback, this authorization check is entirely skipped — a Gwei payment (`parent_bid.value`) is paid to the builder's `fee_recipient` without the required attester-quorum authorization.

### Impact Explanation
This falls under the High/Critical categories in scope: "a builder payment ... misdirected, doubled or escaped" and "Gwei ... paid to a non-owner" in the sense of being paid without the authorization (quorum vote) the protocol requires. Concretely: if a chain experiences 2+ epochs of skipped slots/missed blocks between when a builder's bid is committed and when its parent-payload settlement is finally processed (a scenario that can occur naturally, not just via attacker coordination — e.g., proposer unavailability, network partition recovery, or a chain stall), the builder is paid in full regardless of whether attesters ever confirmed the payload was delivered. Under the normal path this same builder, having `weight == 0`, would have had its pending payment entry evicted with **no** withdrawal created (payment denied). The fallback path pays it anyway, creating value paid to a builder that the honest state-transition logic (via `process_builder_pending_payments`) was designed to deny.

### Likelihood Explanation
This does not require a malicious peer or coalition — it is triggered purely by chain conditions (2+ epochs elapsing between the parent bid's commitment and the processing of its corresponding `process_parent_execution_payload` call, e.g. due to consecutive missed slots). It is captured as expected/tested behavior in `test_builder_payment_after_missed_epochs`, which the test's own comments describe as "The fix creates the withdrawal directly from the bid in this case" — indicating this fallback is a deliberate design choice, not an accidental oversight, but the design still causes payment without quorum authorization, which is a deviation from the invariant enforced by `process_builder_pending_payments` for the normal path.

### Recommendation
In the `parent_bid.value > 0` fallback branch of `apply_parent_execution_payload`, do not unconditionally create a `BuilderPendingWithdrawal`. Instead, persist the weight/quorum decision before the entry is evicted from the 2-epoch `builder_pending_payments` window (e.g., by extending the window, or by applying the quorum check at the point where the entry would otherwise be evicted, rather than dropping the weight information and re-deriving payment purely from the bid).

### Proof of Concept
1. A builder submits a bid with `value > 0` for slot `N`; `process_execution_payload_bid` records a `BuilderPendingPayment` with `weight = 0`.
2. No attesters vote for this block in the same slot (or too few to reach quorum) — `weight` stays `0`, below `get_builder_payment_quorum_threshold`.
3. More than 2 epochs pass without a block importing this parent's payload (e.g., via `next_epoch_with_full_participation` cycles in the test, or naturally via missed slots) so `process_builder_pending_payments` evicts/rotates the entry without creating a withdrawal (weight `0 < quorum`).
4. A later block finally includes `parent_execution_requests` committing to this parent's payload. `process_parent_execution_payload` → `apply_parent_execution_payload` computes `parent_epoch < get_previous_epoch(state)`, hits the `elif parent_bid.value > 0` branch, and unconditionally appends a `BuilderPendingWithdrawal` for `parent_bid.value`, as reproduced by `test_builder_payment_after_missed_epochs` [6](#0-5) , where the builder ends up charged/paid despite `payment.weight == 0` at commitment time.

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

**File:** specs/gloas/beacon-chain.md (L2382-2404)
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
```

**File:** tests/core/pyspec/eth_consensus_specs/test/gloas/sanity/test_blocks.py (L782-855)
```python
@with_gloas_and_later
@spec_state_test
def test_builder_payment_after_missed_epochs(spec, state):
    """
    Test that a builder is correctly charged when their canonical payload
    is processed after 2+ epochs of missed blocks.
    """
    # Advance to get finalization
    for _ in range(4):
        next_epoch_with_full_participation(spec, state)
    assert state.finalized_checkpoint.epoch == 2

    # Build Block 1 with a non-zero value bid from a builder
    block_1 = build_empty_block_for_next_slot(spec, state)
    builder_index = 0
    value = spec.Gwei(1000000)  # 0.001 ETH
    fee_recipient = b"\xab" * 20
    block_hash = spec.Hash32(b"\x42" * 32)

    bid = block_1.body.signed_execution_payload_bid.message
    bid.block_hash = block_hash
    bid.builder_index = builder_index
    bid.value = value
    bid.fee_recipient = fee_recipient
    bid.execution_requests_root = spec.hash_tree_root(spec.ExecutionRequests())

    # Sign the bid with the builder's private key
    signature = spec.get_execution_payload_bid_signature(
        state, bid, builder_privkeys[builder_index]
    )
    block_1.body.signed_execution_payload_bid = spec.SignedExecutionPayloadBid(
        message=bid,
        signature=signature,
    )

    # Ensure builder can cover the bid
    state.builders[builder_index].balance = spec.MIN_DEPOSIT_AMOUNT + value

    yield "pre", state

    # Process Block 1 — creates a pending payment for the builder
    signed_block_1 = state_transition_and_sign_block(spec, state, block_1)

    # Verify pending payment was created
    payment_idx = spec.SLOTS_PER_EPOCH + block_1.slot % spec.SLOTS_PER_EPOCH
    payment = state.builder_pending_payments[payment_idx]
    assert payment.withdrawal.amount == value
    assert payment.withdrawal.builder_index == builder_index
    assert payment.weight == 0

    pre_builder_balance = state.builders[builder_index].balance

    # Build Block 2 with 2+ epochs of missed slots. During the slot advancement,
    # process_builder_pending_payments runs at each epoch boundary:
    #   1st boundary: shifts payment from second half to first half
    #   2nd boundary: checks quorum on first half — weight 0 < quorum → evicted
    # When Block 2 is processed, parent is FULL so apply_parent_execution_payload
    # runs. Since parent_epoch is older than previous_epoch, payment_index is None.
    # The fix creates the withdrawal directly from the bid in this case.
    block_1_epoch = spec.compute_epoch_at_slot(block_1.slot)
    block_2_slot = spec.compute_start_slot_at_epoch(block_1_epoch + 2) + 1
    block_2 = build_empty_block(spec, state, slot=block_2_slot)
    block_2.body.signed_execution_payload_bid.message.parent_block_hash = block_hash
    signed_block_2 = state_transition_and_sign_block(spec, state, block_2)

    yield "blocks", [signed_block_1, signed_block_2]
    yield "post", state

    # Verify apply_parent_execution_payload actually ran (parent was FULL)
    parent_slot_index = bid.slot % spec.SLOTS_PER_HISTORICAL_ROOT
    assert state.execution_payload_availability[parent_slot_index]

    # Verify the builder was charged — balance decreased by the bid value
    assert state.builders[builder_index].balance == pre_builder_balance - value
```
