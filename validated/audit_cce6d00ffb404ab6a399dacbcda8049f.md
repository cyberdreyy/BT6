### Title
Builder Payments Are Permanently Forfeited (Never Paid to Proposer) When Same-Slot Attestation Weight Fails to Reach Quorum - (File: specs/gloas/beacon-chain.md)

### Summary
`process_builder_pending_payments` only converts a `BuilderPendingPayment` into an actual `BuilderPendingWithdrawal` if the accumulated attestation `weight` for that slot reaches `get_builder_payment_quorum_threshold(state)` by the end of the following epoch. If the threshold is not reached, the payment is silently dropped ("evicted") with no other mechanism to pay the proposer, mirroring the reported "zero-activity epoch → tokens locked forever" bug class: a payment that was promised in a committed bid can vanish with no recovery path.

### Finding Description
When a builder submits a bid with `bid.value > 0`, `process_execution_payload_bid` records a `BuilderPendingPayment` with `weight=0` [1](#0-0) . The weight is only incremented when validators submit attestations for that exact slot as the target ("same-slot" attestations) that set a *new* participation flag [2](#0-1) .

At the epoch boundary, `process_builder_pending_payments` checks the accumulated weight against the quorum and either turns the payment into a real withdrawal, or discards it forever by rotating it out with no amount recorded: [3](#0-2) 

The quorum itself is a fixed fraction of the average per-slot active balance: [4](#0-3) 

Critically, the builder is **never debited** at bid time — `process_execution_payload_bid` only checks `can_builder_cover_bid`, it does not decrease `builder.balance` [5](#0-4) . The balance is only decreased later, when the withdrawal is actually applied in `apply_withdrawals` [6](#0-5) . This means: if the quorum is not reached, the builder simply keeps 100% of the promised value while the proposer — who accepted the bid and built the block on the promise of that payment — receives nothing, with no way to reclaim it. The repo's own test suite explicitly documents this eviction path (weight below quorum → no withdrawal, payment zeroed) [7](#0-6) , and a sanity test shows the same eviction occurring purely from epochs elapsing without settlement, even though a fallback exists for the "older than previous epoch" case (append the withdrawal directly from the bid) [8](#0-7)  — that fallback only fires from `apply_parent_execution_payload` (triggered by the *next* proposed block importing the parent's payload), not from the epoch-processing quorum check itself. If the block whose payment is pending never gets enough same-slot attesters within the ~1-epoch window (e.g., low participation from an inactivity leak, network partitions among honest-but-non-adversarial nodes, or simply low overall attester turnout for that particular slot due to normal randomness in duty scheduling combined with proposer/attester timing), the payment is unconditionally zeroed by `process_builder_pending_payments`, and unlike the parent-execution-payload path, there is no code that re-derives the withdrawal from the original bid at that point.

This is the direct analog of the Sherlock M-18 finding: a value transfer that has been *committed to* (the builder's signed bid) is contingent on "activity" (attesting weight) that is not always executed at (equivalent to no lending/borrowing activity in an epoch), and once that activity window closes, the committed value is unrecoverable — except here the beneficiary who loses the value is the honest proposer, not the depositor.

### Impact Explanation
This breaks the "Gwei ... paid to a non-owner" / "builder payment ... misdirected" equality class: the builder's bid is a binding commitment ("If a proposer accepts a builder's bid, the builder will pay the proposer what it promised whether it submits the payload or not" [9](#0-8) ), yet the spec's own settlement mechanism can silently release the builder from that obligation based on an unrelated quantity (same-slot attestation weight), effectively letting the builder keep Gwei that the protocol's own builder-market rules say belongs to the proposer. No node or client bug, malicious peer, or coalition is required — this is a pure spec-state-transition outcome given honest attesters simply not reaching quorum on a particular slot in the required window.

### Likelihood Explanation
The quorum requires the equivalent of a large slice of average per-slot active balance (`BUILDER_PAYMENT_THRESHOLD_NUMERATOR/DENOMINATOR = 6/10`, i.e. 60% of the average per-slot total active balance) to specifically attest to *that exact slot as target* with a *newly-set* participation flag, within roughly one epoch. Under normal but degraded network conditions (finality delay / inactivity leak, high proposer-boost-driven reorgs of adjacent slots, or simply below-average attester participation for a given slot), this condition is plausible to miss even for legitimately canonical, non-adversarial blocks — this is a likelihood driven by ordinary variance in honest network behavior, not by any active attacker, consistent with the rules' focus on breaking an equality without needing a malicious coalition.

### Recommendation
Add a guaranteed settlement/recovery path for evicted `BuilderPendingPayment`s in `process_builder_pending_payments`, e.g., always append a withdrawal to `state.builder_pending_withdrawals` for `payment.withdrawal.amount > 0` regardless of quorum result (using quorum only to decide priority/inclusion rather than forfeiture), or make the fallback in `apply_parent_execution_payload` (which re-derives a withdrawal from the bid for "older than previous epoch" payments) also apply to same-epoch quorum failures — so a committed builder payment can never simply be zeroed out with no state that ever reconstructs it.

### Proof of Concept
Not runnable in this ask-only session; conceptually reproducible via the existing spec test `test_process_builder_pending_payments_below_quorum` [7](#0-6) : seed a `BuilderPendingPayment` with `amount = MIN_ACTIVATION_BALANCE`, `weight = quorum - 1`, run `process_builder_pending_payments`, and observe `state.builder_pending_withdrawals` unchanged while `state.builder_pending_payments[0]` is zeroed — the committed amount disappears from state with no corresponding credit anywhere.

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

**File:** specs/gloas/beacon-chain.md (L1754-1774)
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

**File:** specs/gloas/beacon-chain.md (L2087-2107)
```markdown
def process_execution_payload_bid(
    state: BeaconState, signed_bid: SignedExecutionPayloadBid
) -> None:
    bid = signed_bid.message
    builder_index = bid.builder_index
    amount = bid.value

    # For self-builds, amount must be zero regardless of withdrawal credential prefix
    if builder_index == BUILDER_INDEX_SELF_BUILD:
        assert amount == 0
        assert signed_bid.signature == bls.G2_POINT_AT_INFINITY
    else:
        # Verify that the builder is active
        assert is_active_builder(state, builder_index)
        # Verify that the builder is a payload builder
        assert state.builders[builder_index].version == PAYLOAD_BUILDER_VERSION
        # Verify that the builder has funds to cover the bid
        assert can_builder_cover_bid(state, builder_index, amount)
        # Verify that the bid signature is valid
        assert verify_execution_payload_bid_signature(state, signed_bid)

```

**File:** specs/gloas/beacon-chain.md (L2124-2137)
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
```

**File:** specs/gloas/beacon-chain.md (L2382-2400)
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
```

**File:** tests/core/pyspec/eth_consensus_specs/test/gloas/epoch_processing/test_process_builder_pending_payments.py (L52-77)
```python
@with_gloas_and_later
@spec_state_test
def test_process_builder_pending_payments_below_quorum(spec, state):
    """Test payment below quorum threshold - should not be processed."""
    # Advance past genesis epochs
    next_epoch(spec, state)
    next_epoch(spec, state)

    builder_index = 0
    amount = spec.MIN_ACTIVATION_BALANCE
    quorum = spec.get_builder_payment_quorum_threshold(state)
    weight = quorum - 1  # Below threshold

    # Add pending payment with weight below quorum
    payment = create_builder_pending_payment(spec, builder_index, amount, weight)
    state.builder_pending_payments[0] = payment

    pre_builder_pending_withdrawals = len(state.builder_pending_withdrawals)

    yield from run_epoch_processing_with(spec, state, "process_builder_pending_payments")

    # No withdrawal should be added since weight is below quorum
    assert len(state.builder_pending_withdrawals) == pre_builder_pending_withdrawals

    # Payment should be rotated out of the first SLOTS_PER_EPOCH
    assert state.builder_pending_payments[0].weight == 0
```

**File:** specs/gloas/builder.md (L105-109)
```markdown
commitment to reveal an execution payload in exchange for a payment. When their
bids are chosen by the corresponding proposer, builders are expected to
broadcast an accompanying `SignedExecutionPayloadEnvelope` object honoring the
commitment. If a proposer accepts a builder's bid, the builder will pay the
proposer what it promised whether it submits the payload or not.
```
