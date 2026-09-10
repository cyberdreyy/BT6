### Title
Builder payment can silently escape enforcement when same-slot attestation weight never reaches quorum - ([File: specs/gloas/beacon-chain.md])

### Summary
In the Gloas fork, a builder's payment obligation — committed on-chain via `SignedExecutionPayloadBid` in the block body — is only unconditionally honored when the corresponding parent payload is processed synchronously as FULL (via `settle_builder_payment`). When that synchronous path is not taken (i.e. the payload is never confirmed as FULL within the 2-epoch tracking window), the payment's fate is decided solely by whether accumulated same-slot attestation weight reaches `get_builder_payment_quorum_threshold`. If the weight never reaches quorum, `process_builder_pending_payments` silently discards the pending payment with no withdrawal ever created — the builder keeps the funds it committed to pay, and the proposer (the intended payee) receives nothing, even though the block body committed to that payment.

### Finding Description
When a proposer accepts a non-zero-value bid, `process_execution_payload_bid` records a `BuilderPendingPayment` entry committing the builder to pay `bid.value` to `bid.fee_recipient`: [1](#0-0) 

This commitment is meant to be honored "whether [the builder] submits the payload or not," per the builder documentation: [2](#0-1) 

There are two, inconsistent, enforcement paths for this commitment:

1. **Synchronous settlement** — if the parent payload is confirmed FULL in the very next block, `apply_parent_execution_payload` calls `settle_builder_payment`, which unconditionally appends the withdrawal as long as `amount > 0`, with no quorum check: [3](#0-2) [4](#0-3) 

2. **Epoch-level fallback** — for entries still pending after the 2-epoch window (i.e. never settled synchronously), `process_builder_pending_payments` only creates a withdrawal if the tracked attestation `weight` reaches the quorum threshold. Otherwise the entry is simply zeroed and dropped, with no withdrawal, no penalty, and no compensation to the proposer: [5](#0-4) 

The `weight` accumulated for this fallback comes purely from same-slot attesters, gated only on `is_attestation_same_slot` and `payment.withdrawal.amount > 0` — it has no direct requirement that the payload was actually confirmed FULL: [6](#0-5) 

Because same-slot ("timely head") attestation participation for any given slot is not guaranteed to reach `get_builder_payment_quorum_threshold` (e.g. due to normal network latency, partial committee participation, or a builder who bids but simply never reveals the payload so no meaningful same-slot confirmation activity accrues), the payment obligation recorded in the block body can be dropped entirely — an outcome functionally identical to the Astaria bug where "if there is no bidder... the debts will all be [written] off," except here it is "if quorum is never reached, the payment obligation is written off," letting the builder keep funds it publicly committed, on-chain, to pay.

### Impact Explanation
This breaks the invariant that a payment explicitly committed to in a block (`SignedExecutionPayloadBid.value` to `bid.fee_recipient`) is either paid or the builder is otherwise penalized. Here, the builder can retain the committed Gwei with no consequence — a builder payment "escapes" as explicitly called out as a High-severity impact class (builder payment misdirected, doubled, or escaped). The proposer that accepted the bid loses the promised payment through no fault of its own, purely due to attestation-weight variance outside its control.

### Likelihood Explanation
This does not require any adversarial coalition, equivocation, or malicious peer — it can occur under entirely honest operation whenever same-slot attestation participation for a given proposal is below the quorum threshold for the full 2-epoch window (e.g. the builder never reveals the payload, so no FULL settlement ever triggers, and ordinary attestation timing/latency keeps weight under quorum). It is a deterministic consequence of the spec logic given a plausible, non-adversarial state (low same-slot participation on a given slot), similar in spirit to the "auction ends with zero bids" corner case that Sherlock rated as Medium/valid.

### Recommendation
Ensure that a builder payment committed on-chain is always ultimately enforced regardless of attestation-weight outcomes: either (a) unconditionally settle any surviving `builder_pending_payments` entry via a `settle_builder_payment`-style append once its 2-epoch tracking window expires (mirroring the FULL-payload synchronous path), independent of `weight`, or (b) if the quorum-weight gate is intentionally meant to represent something else (e.g. re-org protection), decouple it from outright fund forfeiture and instead retry/carry the obligation forward rather than silently zeroing it.

### Proof of Concept
1. A proposer accepts a bid with `bid.value = V > 0` and `bid.fee_recipient = P`; `process_execution_payload_bid` creates `BuilderPendingPayment(weight=0, withdrawal=BuilderPendingWithdrawal(fee_recipient=P, amount=V, builder_index=B))` at the corresponding `builder_pending_payments` slot — see `specs/gloas/beacon-chain.md:2124-2140`.
2. The builder never submits (or the network never includes) the `SignedExecutionPayloadEnvelope`; the payload for that parent slot stays EMPTY indefinitely, so `apply_parent_execution_payload`/`settle_builder_payment` is never invoked for that slot (`specs/gloas/beacon-chain.md:1785-1798`).
3. Over the following slots, same-slot attester weight for that slot accumulates to some value `W < get_builder_payment_quorum_threshold(state)` (a realistic outcome under normal network conditions) — accrual logic at `specs/gloas/beacon-chain.md:2382-2389`.
4. After the entry ages past the 2-epoch window, `process_builder_pending_payments` runs: since `payment.weight (W) < quorum`, no withdrawal is appended and the entry is zeroed — `specs/gloas/beacon-chain.md:1664-1676`.
5. Result: builder `B`'s balance is never decreased by `V`, proposer `P` is never paid `V`, and the on-chain commitment recorded in step 1 is permanently unenforced — the committed Gwei effectively vanishes from the accounting rather than being paid to the committed recipient.

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

**File:** specs/gloas/builder.md (L105-109)
```markdown
commitment to reveal an execution payload in exchange for a payment. When their
bids are chosen by the corresponding proposer, builders are expected to
broadcast an accompanying `SignedExecutionPayloadEnvelope` object honoring the
commitment. If a proposer accepts a builder's bid, the builder will pay the
proposer what it promised whether it submits the payload or not.
```
