### Title
Builder payments settle against a `builder_index`, which is reusable and can point to a different builder by the time the deferred withdrawal is applied - ([File: specs/gloas/beacon-chain.md])

### Summary
This is an analog of the "stale delegation" class from the referenced report: an operation captured against a mutable/reusable identifier (`builder_index`) is not re-validated against the actual owner (`pubkey`/`execution_address`) at the time it is actually executed, days/epochs after the identifier could have been legitimately reassigned to a different party.

### Finding Description
In Gloas, when a builder's bid is accepted, the beacon state does **not** immediately debit the builder; it records a `BuilderPendingPayment`/`BuilderPendingWithdrawal` keyed by `builder_index`, and the actual balance change happens later at settlement time (`settle_builder_payment`, `process_builder_pending_payments`), which can be deferred by multiple epochs if blocks are missed: [1](#0-0) [2](#0-1) 

`process_execution_payload_bid` checks `can_builder_cover_bid` (a balance-sufficiency check) at inclusion time only, using `builder_index`: [3](#0-2) 

Meanwhile, `builder_index` is explicitly documented as reusable: once a builder exits and is swept to a zero balance, a brand-new builder (new pubkey) can be assigned the very same index: [4](#0-3) 

The withdrawal/payment record (`BuilderPendingWithdrawal`) that is eventually settled carries only `builder_index` (plus `fee_recipient`/`amount`), not the builder's pubkey: [5](#0-4) 

The spec test `test_builder_payment_after_missed_epochs` confirms the debit of `state.builders[builder_index].balance` happens only at final settlement, and that this settlement can be pushed out across 2+ missed epochs: [6](#0-5) 

If the original builder at `builder_index` exits, is swept (`balance == 0`), and `MIN_BUILDER_WITHDRAWABILITY_DELAY` elapses before the deferred bid settlement finally runs, `get_index_for_new_builder` can hand that exact index to an unrelated new builder. When the delayed settlement later executes, it operates on `state.builders[builder_index]`, which is now the new builder, not the party that actually made the bid and whose balance was checked by `can_builder_cover_bid`. This is structurally the same root cause as the report: an authorization/commitment is tracked by a mutable key (the delegate-registry `(delegate, rights)` pair there; `builder_index` here) instead of the immutable identity it was granted against, so a later operation intended for the original party is silently misapplied to whoever now holds that key.

### Impact Explanation
If reached, this breaks the "Gwei created, destroyed or paid to a non-owner" invariant: a new builder's balance could be decremented for a payment commitment it never made (Gwei destroyed from a non-committing party), or the original builder's committed payment is settled without ever debiting the correct account (Gwei effectively created/never collected), while the proposer's `fee_recipient` still receives the promised amount. This would qualify as Critical under the stated impact bar ("Gwei created, destroyed or paid to a non-owner").

### Likelihood Explanation
Low confidence/likelihood. I was not able to retrieve and verify the exact code path that performs the final balance decrement (`get_builder_withdrawals` / `apply_withdrawals` in `specs/gloas/beacon-chain.md`) within the available iterations, so I cannot confirm whether the debit actually keys off `builder_index` alone at settlement, or whether an additional pubkey/identity check is performed there that would prevent this. The scenario also requires `MIN_BUILDER_WITHDRAWABILITY_DELAY` plus builder-registry index reuse to occur within the (likely short, at most a couple of epochs per the observed test) settlement-deferral window, which is plausible only if that delay constant is small relative to realistic missed-block sequences — a parameter I did not confirm.

### Recommendation
Store (or re-validate against) the builder's `pubkey` — not just the reusable `builder_index` — in `BuilderPendingPayment`/`BuilderPendingWithdrawal`, or refuse to reuse a `builder_index` for a new registrant while any pending payment/withdrawal still references that index.

### Proof of Concept
Conceptual, given the uncertainty above:
1. Builder B is assigned `builder_index = 5`, submits a winning bid of value V; `can_builder_cover_bid` passes; a `BuilderPendingPayment{builder_index=5, ...}` is queued.
2. B immediately submits a `BuilderExitRequest`; B's stake sweeps to zero and `withdrawable_epoch` passes `MIN_BUILDER_WITHDRAWABILITY_DELAY`.
3. A new builder C deposits and, via `get_index_for_new_builder`, is assigned the freed `builder_index = 5`.
4. Due to missed blocks (as in `test_builder_payment_after_missed_epochs`), settlement of B's original payment is deferred past this point and finally executes against `state.builders[5]`, which is now C — misapplying B's payment obligation to C.

Given the unresolved uncertainty about the exact settlement code, this should be treated as a lead requiring further verification of `get_builder_withdrawals`/`apply_withdrawals` in `specs/gloas/beacon-chain.md` before being considered conclusively proven.

### Citations

**File:** specs/gloas/beacon-chain.md (L677-684)
```markdown
#### `BuilderPendingWithdrawal`

```python
class BuilderPendingWithdrawal(Container):
    fee_recipient: ExecutionAddress
    amount: Gwei
    builder_index: BuilderIndex
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

**File:** specs/gloas/beacon-chain.md (L2084-2113)
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

```

**File:** specs/gloas/beacon-chain.md (L2212-2254)
```markdown
###### New `get_index_for_new_builder`

```python
def get_index_for_new_builder(state: BeaconState) -> BuilderIndex:
    for index, builder in enumerate(state.builders):
        if builder.withdrawable_epoch <= get_current_epoch(state) and builder.balance == 0:
            return BuilderIndex(index)
    return BuilderIndex(len(state.builders))
```

###### New `add_builder_to_registry`

```python
def add_builder_to_registry(
    state: BeaconState,
    pubkey: BLSPubkey,
    version: Uint8,
    execution_address: ExecutionAddress,
    amount: Gwei,
    slot: Slot,
) -> None:
    set_or_append_list(
        state.builders,
        get_index_for_new_builder(state),
        Builder(
            pubkey=pubkey,
            version=version,
            execution_address=execution_address,
            balance=amount,
            deposit_epoch=compute_epoch_at_slot(slot),
            withdrawable_epoch=FAR_FUTURE_EPOCH,
        ),
    )
```

###### New `process_builder_deposit_request`

*Note*: Builder indices are reusable. When a builder exits, its index may later
be reassigned to a different builder with a new public key. Any deposit sent to
an exited builder will be withdrawn to the builder’s execution address. Exited
builders cannot be reactivated, although a newly registered builder’s public key
may have previously appeared in the builder set. Implementations that rely on
caching should account for this behavior.
```

**File:** tests/core/pyspec/eth_consensus_specs/test/gloas/sanity/test_blocks.py (L782-856)
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
