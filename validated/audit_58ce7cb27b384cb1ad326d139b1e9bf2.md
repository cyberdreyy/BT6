### Title
Builder-set `fee_recipient` in `ExecutionPayloadBid` is never validated at the state-transition level, allowing a builder to burn the payment owed to the proposer - (File: specs/gloas/beacon-chain.md)

### Summary
In Gloas (EIP-7732), a builder pays a proposer for including its bid by committing to a `fee_recipient` and `value` in the `ExecutionPayloadBid`. The honest-builder procedure requires setting `bid.fee_recipient` to the proposer's preferred address obtained from `SignedProposerPreferences` [1](#0-0) , but this is only a p2p **gossip-layer** check with `IGNORE` severity [2](#0-1) , not a consensus rule. The actual state-transition function `process_execution_payload_bid` never checks `bid.fee_recipient` against the proposer's preference, nor rejects `fee_recipient == 0x00...00` [3](#0-2) . The `fee_recipient` is later used verbatim as the destination `address` of the actual `Withdrawal` that moves Gwei out of the builder's balance [4](#0-3) .

### Finding Description
The builder constructs and signs a `SignedExecutionPayloadBid` promising to pay `bid.value` Gwei to `bid.fee_recipient` if the proposer includes the bid [5](#0-4) . `process_execution_payload_bid` — the function that actually decides whether the block is valid — only validates the builder's signature, active/version status, balance coverage, slot/parent-hash/gas/randao consistency, and blob-commitment bounds. It never validates `bid.fee_recipient`:

```
def process_execution_payload_bid(...):
    ...
    if amount > 0:
        pending_payment = BuilderPendingPayment(
            weight=Gwei(0),
            withdrawal=BuilderPendingWithdrawal(
                fee_recipient=bid.fee_recipient,
                amount=amount,
                builder_index=builder_index,
            ),
            ...
        )
``` [6](#0-5) 

This pending payment is later settled into `state.builder_pending_withdrawals` verbatim by `settle_builder_payment` / `apply_parent_execution_payload` [7](#0-6) [8](#0-7) , and finally converted into a real `Withdrawal` by `get_builder_withdrawals`, which copies `withdrawal.fee_recipient` straight into `Withdrawal.address` with no non-zero check:

```
withdrawals.append(
    Withdrawal(
        index=withdrawal_index,
        validator_index=convert_builder_index_to_validator_index(builder_index),
        address=withdrawal.fee_recipient,
        amount=withdrawal.amount,
    )
)
``` [9](#0-8) 

`apply_withdrawals` then unconditionally deducts `amount` from the builder's balance regardless of the destination address:
```
if is_builder_index(withdrawal.validator_index):
    builder_index = convert_validator_index_to_builder_index(withdrawal.validator_index)
    builder_balance = state.builders[builder_index].balance
    state.builders[builder_index].balance -= min(withdrawal.amount, builder_balance)
``` [10](#0-9) 

The only place `bid.fee_recipient` is ever compared to the proposer's preference is `validate_execution_payload_bid_gossip`, and even there a mismatch only causes the bid to be `IGNORE`d for propagation on that specific gossip topic — it is not a `REJECT` and it has no bearing on block/state validity:
```
if bid.fee_recipient != proposer_preferences.fee_recipient:
    raise GossipIgnore("bid's fee recipient does not match the proposer's preference")
``` [11](#0-10) 

Because this check lives outside the state-transition function, any block whose bid sets `fee_recipient = ExecutionAddress()` (all-zero) is a fully valid block per `process_block`/`process_execution_payload_bid`. A single builder, without any peer, coalition, or client bug, can unilaterally sign such a bid and hand it directly to a proposer (out-of-band, not via the gossip network the `IGNORE` rule polices), and the consensus rules will accept it, decrement the builder's own balance by the promised value, and instruct the execution layer to credit `address(0)` — i.e. burn the ETH that was meant to compensate the proposer for including the block.

### Impact Explanation
This breaks the equality "the amount promised to the proposer's fee recipient is actually delivered to the proposer." Concretely:
- Before: builder balance = B, proposer expects to receive `value` Gwei.
- After processing a bid with `fee_recipient = 0x00…00` and `value = V`: builder balance = B − V (charged as normal), but the corresponding `Withdrawal.address` is the null address, so the V Gwei is permanently destroyed instead of being paid to the proposer.

This matches the "builder payment ... misdirected" / "Gwei ... destroyed" impact classes: a payment that a fully spec-following block validly commits to is not delivered to the party it was owed to, with no way for other honest nodes to detect or reject this at the consensus layer (only a heuristic, best-effort gossip filter exists, and it can be trivially bypassed by any direct proposer–builder relationship, e.g. relays, local builders, or any submission path that does not traverse the public gossip network).

### Likelihood Explanation
Likelihood is high in terms of ease of execution (a single non-conforming builder implementation, or a builder that races a gossip `IGNORE` window, or one that submits directly to a relay/proposer out-of-band) but the loss lands on the proposer, not the builder itself, so it is not merely "attacker burns their own stake" — it deprives another honest network participant (the proposer) of a payment the protocol was supposed to guarantee once the bid was accepted and the block built. Whether this is exploited depends on implementations enforcing the fee-recipient/proposer-preference match purely as a gossip nicety rather than a state-transition invariant, which is exactly what the current spec text does.

### Recommendation
Add an explicit state-transition-level check in `process_execution_payload_bid` (specs/gloas/beacon-chain.md) that rejects bids where `bid.fee_recipient == ExecutionAddress()` (the zero address), and/or enforce that `bid.fee_recipient` matches the block proposer's authenticated preference as part of consensus validity rather than only as an advisory gossip-propagation rule. This mirrors the Foundation marketplace fix pattern: treat `address(0)` as "no valid recipient" and fall back to rejecting the bid (or defaulting payment to the proposer's own withdrawal address) rather than allowing the value to be silently burned.

### Proof of Concept
1. Builder constructs `bid` with a legitimate `value = V > 0`, valid signature, correct slot/parent hash/randao/gas-limit, but sets `bid.fee_recipient = ExecutionAddress(b"\x00" * 20)` instead of the proposer's preferred address from `SignedProposerPreferences`.
2. Builder hands the signed bid directly to the proposer (bypassing/ignoring the `execution_payload_bid` gossip topic, e.g. via a private relay or local out-of-protocol channel), so `validate_execution_payload_bid_gossip`'s fee-recipient `IGNORE` check [11](#0-10)  is never invoked.
3. The proposer includes the bid in its block. `process_execution_payload_bid` accepts it — it performs no fee_recipient check [3](#0-2) .
4. At settlement, `state.builder_pending_withdrawals` receives a `BuilderPendingWithdrawal(fee_recipient=0x00...00, amount=V, builder_index=...)` [7](#0-6) .
5. During `process_withdrawals` → `get_builder_withdrawals`, a `Withdrawal(address=0x00...00, amount=V, ...)` is generated [9](#0-8) , and `apply_withdrawals` deducts V from the builder's balance [10](#0-9) .
6. The execution layer honors this withdrawal by crediting `address(0)` with V ETH — the payment is burned, and the proposer, despite including a "valid" bid with a promised positive `value`, receives nothing. The block and state transition are fully valid per spec rules throughout.

### Citations

**File:** specs/gloas/builder.md (L102-109)
```markdown
Builders have two optional activities: submitting bids and submitting payloads.
Builders can submit bids to produce execution payloads. They can broadcast these
bids in the form of `SignedExecutionPayloadBid` objects. These objects encode a
commitment to reveal an execution payload in exchange for a payment. When their
bids are chosen by the corresponding proposer, builders are expected to
broadcast an accompanying `SignedExecutionPayloadEnvelope` object honoring the
commitment. If a proposer accepts a builder's bid, the builder will pay the
proposer what it promised whether it submits the payload or not.
```

**File:** specs/gloas/builder.md (L134-139)
```markdown
07. Set `bid.fee_recipient` to be an execution address to receive the payment.
    The proposer's preferred fee recipient is obtained from the
    `SignedProposerPreferences` whose `message.proposal_slot` matches `bid.slot`
    and whose `message.dependent_root` matches
    `get_shuffling_dependent_root(store, bid.parent_block_root, compute_epoch_at_slot(bid.slot))`,
    where `store` is the fork choice store.
```

**File:** specs/gloas/p2p-interface.md (L969-979)
```markdown
    # [IGNORE] The matching proposer preferences have been seen
    dependent_root = get_shuffling_dependent_root(store, bid.parent_block_root, proposal_epoch)
    prefs_key = (bid.slot, dependent_root)
    if prefs_key not in seen.proposer_preferences:
        raise GossipIgnore("matching proposer preferences have not been seen")

    proposer_preferences = seen.proposer_preferences[prefs_key]

    # [IGNORE] The bid's fee recipient matches the proposer's preference
    if bid.fee_recipient != proposer_preferences.fee_recipient:
        raise GossipIgnore("bid's fee recipient does not match the proposer's preference")
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

**File:** specs/gloas/beacon-chain.md (L1802-1834)
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
        processed_count += 1

    return withdrawals, withdrawal_index, processed_count
```
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

**File:** specs/gloas/beacon-chain.md (L2084-2141)
```markdown
##### New `process_execution_payload_bid`

```python
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

    # Verify commitments are under limit
    assert (
        len(bid.blob_kzg_commitments)
        <= get_blob_parameters(get_current_epoch(state)).max_blobs_per_block
    )

    # Verify that the bid is for the current slot
    assert bid.slot == state.slot
    assert state.slot > GENESIS_SLOT
    # Verify that the bid is for the right parent block
    assert bid.parent_block_hash == state.latest_block_hash
    # Verify that the bid's block hash differs from its parent block hash
    assert bid.block_hash != bid.parent_block_hash
    assert bid.parent_block_root == get_block_root_at_slot(state, state.slot - 1)
    assert bid.prev_randao == get_randao_mix(state, get_current_epoch(state))

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
