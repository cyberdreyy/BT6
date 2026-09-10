### Title
Builder payment settlement bypasses PTC quorum check, letting a builder collect payment for a payload no committee attested to - ([File: specs/gloas/beacon-chain.md])

### Summary
`apply_parent_execution_payload` settles a slot's builder payment via `settle_builder_payment`, which releases the payment unconditionally whenever `amount > 0`. This is the *only* gate for payments belonging to the current or previous epoch. The *actual* quorum requirement — that a payment should only be released once enough Payload Timeliness Committee (PTC)/attester weight has confirmed the payload — is implemented solely in the epoch-boundary function `process_builder_pending_payments`, which is reached only for stale payments that the fast path never processed. The two code paths that are supposed to enforce the same invariant ("only pay a builder if quorum attested the payload") disagree, and the path used in the overwhelming majority of blocks skips the check entirely.

### Finding Description
`process_block` for Gloas calls `process_parent_execution_payload` before `process_operations` (which processes the current block's attestations): [1](#0-0) 

`process_parent_execution_payload` calls `apply_parent_execution_payload` whenever the parent block was "full": [2](#0-1) 

Inside `apply_parent_execution_payload`, the payment for the parent's slot is settled with `settle_builder_payment` if the parent's slot falls in the current or previous epoch: [3](#0-2) 

`settle_builder_payment` itself performs **no weight/quorum check whatsoever** — it unconditionally moves the payment to `builder_pending_withdrawals` if `amount > 0`: [4](#0-3) 

Contrast this with the *only* place a quorum check is actually implemented, `process_builder_pending_payments`, run once per epoch on entries that reach the end of the two-epoch window without having been settled by the fast path: [5](#0-4) 

The `weight` field that this quorum check reads is accumulated only through `process_attestation`, which increases `payment.weight` when validators attest to the same slot and set a new participation flag: [6](#0-5) 

Because `process_parent_execution_payload` runs *before* `process_operations` in the very same block that would normally carry the first attestations for the parent slot, and because `settle_builder_payment` never inspects `weight` at all, the payment for slot N is settled the moment block N+1 is processed — independent of whether any PTC/attester weight has accumulated for slot N. The quorum threshold computed by `get_builder_payment_quorum_threshold` is completely inert for any payment settled through this normal, expected path: [7](#0-6) 

This is the same bug class as the ZetaChain report: an "enabled/disabled" gating condition (`IsOutboundEnabled` there, `weight >= quorum` here) is enforced in one code branch but silently omitted in a parallel branch that handles the common case, so the guarded action (sending funds / releasing a builder payment) proceeds even when the gate should have blocked it.

The broken equality: *a builder is paid the bid `amount` for slot N if and only if PTC/attester weight for slot N reaches quorum.* Before the attacker's block: `payment.weight = 0`, `amount = bid.value > 0`. After the attacker submits the very next block (with no attestations for slot N, or attestations withheld/absent): `settle_builder_payment` still appends `amount` to `builder_pending_withdrawals`, so the builder is paid `amount` in Gwei even though `weight (0) < quorum`. The withdrawal is later paid out to `fee_recipient` in `get_builder_withdrawals`/`apply_withdrawals`.

### Impact Explanation
A Gwei payment is unconditionally credited to a builder's `fee_recipient` (via `builder_pending_withdrawals` → `get_builder_withdrawals` → `apply_withdrawals`) without the intended committee confirmation that the payload was actually available/canonical/timely. This is a payment "escaped" beyond what the protocol's own quorum rule permits — the exact `weight >= quorum` gate exists precisely to prevent paying builders for payloads that were not actually attested/confirmed, and the fast-path settlement silently bypasses it for essentially all normal-case payments (anything settled within the current/previous epoch). This matches the "High" impact bucket: a builder payment escaped/misdirected without the safeguard the spec itself defines for it.

### Likelihood Explanation
This is not a rare edge case — it is the **default, most common path**. Every block that has a full parent triggers `apply_parent_execution_payload`, and in virtually all cases `parent_epoch` equals the current or previous epoch, so `settle_builder_payment` (the unconditional-pay function) is the code path exercised on essentially every slot. The epoch-boundary quorum-checked path (`process_builder_pending_payments`) is only reached for the rare case where a payment survives two full epochs without being settled by the fast path — which normally never happens. Consequently the quorum mechanism is effectively dead code for the overwhelming majority of payments, and any builder proposing a full parent payload is paid regardless of PTC participation.

### Recommendation
Make `settle_builder_payment` (or its caller `apply_parent_execution_payload`) apply the same `payment.weight >= get_builder_payment_quorum_threshold(state)` condition that `process_builder_pending_payments` uses, so that the fast path and the epoch-boundary sweep enforce an identical invariant. Alternatively, defer the fast-path settlement until enough slots have elapsed for the relevant attestations to be included and their weight tallied, so that the same-block ordering (`process_parent_execution_payload` before `process_operations`) cannot be used to settle a payment before any weight could possibly have accrued.

### Proof of Concept
1. Builder B submits a non-self bid for slot N with `value = V > 0`; the bid is accepted by `process_execution_payload_bid`, creating `builder_pending_payments[index]` with `weight = 0`, `withdrawal.amount = V` [8](#0-7) .
2. The payload for slot N is revealed and marked full (`execution_payload_availability[...] = True`) via `apply_parent_execution_payload`'s tail update [9](#0-8) .
3. The very next block (slot N+1) is proposed with **no attestations for slot N** in its `attestations` list (or attestations that arrive later, outside this block). `process_block` for slot N+1 runs `process_parent_execution_payload` → `apply_parent_execution_payload` → `settle_builder_payment(state, payment_index)` *before* `process_operations` has a chance to add any weight for slot N.
4. `settle_builder_payment` checks only `payment.withdrawal.amount > 0` (true) and appends the withdrawal to `state.builder_pending_withdrawals`, with `payment.weight` still `0`, which is `< get_builder_payment_quorum_threshold(state)`.
5. In a subsequent block's `process_withdrawals`, `get_builder_withdrawals` drains `state.builder_pending_withdrawals` and pays `V` Gwei to builder B's `fee_recipient` [10](#0-9)  — despite zero PTC/attester weight ever having confirmed slot N's payload, violating the quorum invariant that governs the equivalent epoch-boundary code path [11](#0-10) .

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

**File:** specs/gloas/beacon-chain.md (L1785-1798)
```markdown
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

**File:** specs/gloas/beacon-chain.md (L1802-1830)
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
