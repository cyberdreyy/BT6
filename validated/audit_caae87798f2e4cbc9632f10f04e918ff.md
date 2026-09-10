### Title
Builder payment settled and unconditionally paid before PTC quorum weight can gate it, escaping the quorum requirement - (specs/gloas/beacon-chain.md)

### Summary
`settle_builder_payment`, invoked from `apply_parent_execution_payload` during `process_block`, pays out a builder's pending payment unconditionally as soon as the amount is non-zero, without checking the PTC (Payload Timeliness Committee) `weight` field that `process_builder_pending_payments` uses as its quorum gate. Because `process_parent_execution_payload` runs before `process_operations` (which processes the attestations that increment `weight`) within the same block, and because it settles the parent's payment on the very next block, the quorum check in `process_builder_pending_payments` can never meaningfully withhold payment: the entry has already been emptied by the time any weight could accumulate against it.

### Finding Description
`process_block` calls `process_parent_execution_payload` first, then later `process_operations` (which processes attestations): [1](#0-0) 

`process_parent_execution_payload` → `apply_parent_execution_payload` settles the builder's pending payment for the parent slot as soon as the parent block is processed (i.e., on the very next block), using `settle_builder_payment`, which pays out unconditionally based only on `payment.withdrawal.amount > 0` — it never inspects `payment.weight`: [2](#0-1) [3](#0-2) 

Separately, `process_builder_pending_payments` (run once per epoch from `process_epoch`) is the function that actually implements the quorum gate: it only converts a pending payment into a withdrawal if `payment.weight >= quorum`; otherwise the payment is simply dropped (forfeited): [4](#0-3) 

`payment.weight` is incremented only inside `process_attestation`, gated on `payment.withdrawal.amount > 0` and `is_attestation_same_slot`, using the exact same `builder_pending_payments` index (`SLOTS_PER_EPOCH + data.slot % SLOTS_PER_EPOCH` or `data.slot % SLOTS_PER_EPOCH`) as `settle_builder_payment` uses for that slot: [5](#0-4) 

The race: for parent slot `N`, `apply_parent_execution_payload` (called at the very start of block `N+1`'s processing) settles `builder_pending_payments[index(N)]` immediately, appending the withdrawal to `state.builder_pending_withdrawals` and zeroing the slot — before `process_operations` in the *same* block `N+1` has a chance to process any attestation for slot `N` and add to `payment.weight`. Since `parent_epoch == get_current_epoch(state)` or `parent_epoch == get_previous_epoch(state)` covers essentially every non-degenerate case (no multi-epoch of skipped slots), this unconditional settlement fires on effectively every block, well before `process_builder_pending_payments` (which only runs once per epoch, on the epoch boundary, and only inspects entries that have survived that long) ever gets a chance to enforce `weight >= quorum`. By the time the epoch-boundary quorum check runs, the entry for that slot has already been zeroed out by the earlier unconditional settlement, so the quorum check is a no-op for it.

This breaks the equality the PTC-quorum mechanism is supposed to enforce: *"builder payment is withdrawn to `fee_recipient` if and only if the PTC quorum attested payload timeliness for that slot."* Instead, the builder is paid regardless of whether any PTC attestation confirming timely payload delivery was ever included.

### Impact Explanation
This is a builder payment that "escapes" its required gating condition — the block did not commit (via attested quorum) to the builder actually having delivered the payload on time, yet the payment is still made. This matches the High severity category: "a builder payment or withdrawal misdirected, doubled or escaped, a duty selection one participant can steer." Any proposer/builder combination benefits, since the builder is always paid as long as their bid was included and the parent block hash matched, independent of downstream committee confirmation — nullifying the intended economic safeguard that PTC quorum was designed to provide (protecting against late/incomplete payload delivery while still charging the builder).

### Likelihood Explanation
This triggers on essentially every ordinary block with a non-zero bid amount — it requires no adversarial coalition, malicious peer, or economic stake advantage. It is a deterministic ordering/logic issue reachable by any spec-following block sequence, not a race that depends on network timing.

### Recommendation
Make `settle_builder_payment` (as invoked from `apply_parent_execution_payload`) respect the same quorum condition as `process_builder_pending_payments` — i.e., only append to `builder_pending_withdrawals` if `payment.weight >= get_builder_payment_quorum_threshold(state)`, otherwise drop the payment. Alternatively, remove the immediate per-block settlement path entirely and rely solely on the epoch-boundary `process_builder_pending_payments` quorum-gated settlement, ensuring `process_operations` (attestation processing, which accumulates `weight`) always completes before any settlement decision is made for that slot.

### Proof of Concept
Conceptual trace (spec-only, no client bugs required):
1. Builder submits a bid for slot `N` with `value > 0`; `process_execution_payload_bid` records a `BuilderPendingPayment` with `weight = 0` at `builder_pending_payments[SLOTS_PER_EPOCH + N % SLOTS_PER_EPOCH]` (specs/gloas/beacon-chain.md, execution payload bid section).
2. Block `N`'s parent execution payload is applied as normal; the child block header at slot `N` becomes the new `latest_block_header`.
3. Block `N+1` is processed: `process_block` calls `process_parent_execution_payload(state, block)` first. Since `bid.parent_block_hash == parent_bid.block_hash`, `apply_parent_execution_payload` runs, computing `parent_epoch == get_current_epoch(state)` (typical case) and calling `settle_builder_payment(state, SLOTS_PER_EPOCH + N % SLOTS_PER_EPOCH)`. Because `payment.withdrawal.amount > 0`, the withdrawal is appended to `state.builder_pending_withdrawals` immediately, and the payment slot is zeroed — all while `payment.weight` is still `0` (no attestation for slot `N` has been processed yet in this or any prior block).
4. Later in the same block `N+1`, `process_operations` processes an attestation for slot `N`; `process_attestation` would have incremented `payment.weight`, but the operation is now mutating an already-emptied `BuilderPendingPayment.empty()` entry — it has no effect on payment outcome.
5. At the epoch boundary, `process_builder_pending_payments` inspects `builder_pending_payments[:SLOTS_PER_EPOCH]`; the entry for slot `N` is already `BuilderPendingPayment.empty()` (weight 0, amount 0), so the quorum check contributes nothing — the payment was already made in step 3 regardless of PTC quorum.

Net result: the builder is paid the full bid value even though zero PTC attestations were ever counted toward the quorum before settlement, demonstrating that the quorum-gate (`weight >= quorum`) never actually withholds payment in the normal block-processing path.

*Caveat: This is a control-flow/ordering analysis derived purely from the spec pseudocode (specs/gloas/beacon-chain.md); I could not execute the actual pyspec test suite to empirically confirm the runtime behavior, so this should be verified by running the existing gloas builder-payment tests (e.g. tests/core/pyspec/eth_consensus_specs/test/gloas/epoch_processing/test_process_builder_pending_payments.py and tests/core/pyspec/eth_consensus_specs/test/gloas/block_processing/test_process_parent_execution_payload.py) with an added attestation-weight assertion to confirm no case exists where `apply_parent_execution_payload`'s settlement is deferred until after quorum could be reached.*

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

**File:** specs/gloas/beacon-chain.md (L1699-1716)
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

**File:** specs/gloas/beacon-chain.md (L2357-2403)
```markdown
    # [Modified in Gloas:EIP7732]
    if data.target.epoch == get_current_epoch(state):
        current_epoch_target = True
        epoch_participation = state.current_epoch_participation
        payment = state.builder_pending_payments[SLOTS_PER_EPOCH + data.slot % SLOTS_PER_EPOCH]
    else:
        current_epoch_target = False
        epoch_participation = state.previous_epoch_participation
        payment = state.builder_pending_payments[data.slot % SLOTS_PER_EPOCH]

    proposer_reward_numerator = 0
    for index in get_attesting_indices(state, attestation):
        # [New in Gloas:EIP7732]
        had_no_participation = epoch_participation[index] == 0b0000_0000
        will_set_new_flag = False

        for flag_index, weight in enumerate(PARTICIPATION_FLAG_WEIGHTS):
            if flag_index in participation_flag_indices and not has_flag(
                epoch_participation[index], flag_index
            ):
                epoch_participation[index] = add_flag(epoch_participation[index], flag_index)
                proposer_reward_numerator += get_base_reward(state, index) * weight
                # [New in Gloas:EIP7732]
                will_set_new_flag = True

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
