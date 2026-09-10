### Title
Builder-supplied `fee_recipient` in `ExecutionPayloadBid` is never validated against the proposer's committed preference at the consensus level, letting a builder misdirect its own payment away from the entitled proposer - (File: `specs/gloas/beacon-chain.md`, `specs/gloas/validator.md`)

### Summary
The Gloas fork introduces a builder-payment mechanism where a builder submits a `SignedExecutionPayloadBid` promising to pay `bid.value` Gwei to `bid.fee_recipient` if the proposer accepts the bid. This is directly analogous to the reported `TeaVaultAmbient.executeSwap` bug: an address parameter that should represent "the intended, validated recipient" is instead attacker-supplied and never checked against the value the protocol expects it to equal, allowing the counterparty entitled to a payment to be silently paid nothing (or paid to someone else) while the ledger still records the payment as settled.

### Finding Description
`bid.fee_recipient` is documented as "the execution address where the validator [proposer] wishes to receive the builder payment" [1](#0-0) , and is meant to be copied by the builder from the proposer's `SignedProposerPreferences.fee_recipient` when constructing the bid [2](#0-1) .

However, the state-transition function that finalizes this commitment, `process_execution_payload_bid`, never checks `bid.fee_recipient` against anything — it only validates builder activity, balance coverage, blob-commitment limits, slot/parent/randao consistency, and the BLS signature, then unconditionally records the payment to whatever `bid.fee_recipient` was supplied: [3](#0-2) 

The list of conditions the proposer/validator is instructed to check before accepting a bid (mirroring `process_execution_payload_bid`) likewise omits any `fee_recipient` check: [4](#0-3) 

The only place `fee_recipient` matching is enforced at all is a gossip-network `IGNORE` rule, which is not a consensus/state-transition rule and does not gate whether a proposer can include the bid in a valid block: [5](#0-4) 

Once accepted, the pending payment (carrying `bid.fee_recipient`) is recorded and later settled as a real withdrawal via `apply_parent_execution_payload`/`settle_builder_payment`, moving Gwei out of the builder's balance to that address: [6](#0-5) [7](#0-6) 

Equality broken: the protocol's implicit invariant is `bid.fee_recipient == proposer's committed SignedProposerPreferences.fee_recipient` (the entity entitled to the builder's payment for that slot). Nothing in `process_execution_payload_bid` or the validator's MUST-check list enforces this equality, so a builder can supply any `ExecutionAddress` and the chain will treat the payment as validly settled to that address regardless of whether it matches the proposer's declared preference.

### Impact Explanation
This maps to the explicitly listed High-impact class "a builder payment or withdrawal misdirected, doubled or escaped." A malicious/self-interested builder can construct a fully valid, signature-passing bid whose `fee_recipient` is any address it chooses (e.g., its own address, or an address it colludes with) instead of the value published in the proposer's `SignedProposerPreferences`. If a proposer or relay includes this bid off the gossip network (explicitly permitted: "The block proposer MAY obtain these signed messages by other off-protocol means" [8](#0-7) ), the gossip-layer fee-recipient check is bypassed entirely, and `process_execution_payload_bid`/`apply_parent_execution_payload` will settle the payment to the builder-chosen address. The proposer that was promised payment receives nothing, even though the chain state (`builder_pending_withdrawals`, decremented builder balance) shows the payment as fulfilled — a real Gwei transfer to a party other than the one entitled to it.

### Likelihood Explanation
Likelihood is meaningful but bounded: it requires the bid to reach block inclusion without passing through gossip validation (via a relay/off-protocol channel, which the spec explicitly allows) or a proposer that fails to independently cross-check `bid.fee_recipient` against its own `SignedProposerPreferences` before signing the block (since validator.md's mandatory bid-acceptance checklist does not require this comparison, an implementation following the spec literally would not catch it). Any conforming block that includes such a bid is fully valid per `process_execution_payload_bid`, so no client/consensus-rule rejection would occur; the failure is purely a missing equality check in the spec-defined validity/acceptance logic, not a client bug.

### Recommendation
Add an explicit assertion in `process_execution_payload_bid` (or, at minimum, make it a mandatory MUST-check in the validator's bid-selection procedure in `specs/gloas/validator.md`) that `bid.fee_recipient` equals the proposer's currently valid `SignedProposerPreferences.fee_recipient` for `bid.slot` before the bid can be accepted/included, rather than relying solely on a gossip-layer `IGNORE` rule that can be bypassed via off-protocol bid delivery.

### Proof of Concept
1. Proposer P for slot `s` publishes `SignedProposerPreferences{fee_recipient = A}`.
2. Malicious builder B (already active, sufficiently funded) constructs `ExecutionPayloadBid{slot=s, value=v, fee_recipient=B_own_address, ...}` satisfying all fields required by `process_execution_payload_bid` (parent_block_hash, parent_block_root, prev_randao, valid signature).
3. B does not broadcast this bid over the `execution_payload_bid` gossip topic (avoiding the gossip `IGNORE` check in `specs/gloas/p2p-interface.md`), and instead delivers it to P "by other off-protocol means" as explicitly permitted by `specs/gloas/validator.md`.
4. P's block-production logic follows the exact checklist in `specs/gloas/validator.md` lines 204-221, which does not compare `bid.fee_recipient` to `A`; P includes the bid and proposes the block.
5. `process_execution_payload_bid` accepts the block (no fee_recipient check exists), queues `BuilderPendingWithdrawal{fee_recipient=B_own_address, amount=v, builder_index=B}`.
6. `apply_parent_execution_payload`/`settle_builder_payment` later executes the withdrawal to `B_own_address`, decrementing B's builder balance by `v` and crediting `B_own_address` instead of `A`.
7. Result: `v` Gwei that P was promised, and that the chain records as "paid," never reaches P's chosen `fee_recipient` — a builder payment misdirected away from its rightful owner without violating any consensus rule.

### Citations

**File:** specs/gloas/validator.md (L155-160)
```markdown
A validator constructs each `SignedProposerPreferences` with
`get_signed_proposer_preferences` for each `proposal_slot` in
`get_upcoming_proposal_slots(state, validator_index)`. Let `head_root` be the
validator's current head root, `fee_recipient` be the execution address where
the validator wishes to receive the builder payment, and `target_gas_limit` be
the validator's preferred gas limit for the execution payload.
```

**File:** specs/gloas/validator.md (L201-203)
```markdown
- Listen to the `execution_payload_bid` gossip global topic and save an accepted
  `signed_execution_payload_bid` from a builder. The block proposer MAY obtain
  these signed messages by other off-protocol means.
```

**File:** specs/gloas/validator.md (L204-221)
```markdown
- The `signed_execution_payload_bid` MUST satisfy the verification conditions
  found in `process_execution_payload_bid` with the alias
  `bid = signed_execution_payload_bid.message`, that is:
  - For external builders, the signature MUST be valid.
  - For self-builds, set `bid.builder_index` to `BUILDER_INDEX_SELF_BUILD`.
  - For self-builds, the signature MUST be `bls.G2_POINT_AT_INFINITY` and the
    `bid.value` MUST be zero.
  - The builder balance can cover the `bid.value`.
  - The `bid.slot` is for the proposal block slot.
  - The `bid.parent_block_hash` equals
    `state.latest_execution_payload_bid.block_hash` if
    `should_build_on_full(store, head, get_current_slot(store))` is true,
    otherwise `state.latest_execution_payload_bid.parent_block_hash`.
  - The `bid.parent_block_root` equals the current block's `parent_root`.
  - The `bid.prev_randao` equals
    `get_randao_mix(state, get_current_epoch(state))`.
- Select one bid and set
  `block.body.signed_execution_payload_bid = signed_execution_payload_bid`.
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

**File:** specs/gloas/beacon-chain.md (L1753-1774)
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

**File:** specs/gloas/p2p-interface.md (L977-979)
```markdown
    # [IGNORE] The bid's fee recipient matches the proposer's preference
    if bid.fee_recipient != proposer_preferences.fee_recipient:
        raise GossipIgnore("bid's fee recipient does not match the proposer's preference")
```
