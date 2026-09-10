### Title
Builder can misdirect its own payment away from the proposer's designated `fee_recipient` because `process_execution_payload_bid` never checks it - ([File: specs/gloas/beacon-chain.md])

### Summary
`process_execution_payload_bid` records `bid.fee_recipient` from the builder-authored, builder-signed bid directly, without ever comparing it to the address the proposer actually wants to be paid at. The match check exists only as a soft, `IGNORE`-level gossip filter, not as a consensus rule.

### Finding Description
The proposer communicates its desired payout address out-of-band via a signed `SignedProposerPreferences.fee_recipient` [1](#0-0) . The only place this preference is checked against the builder's bid is in gossip validation, and a mismatch only causes the message to be `IGNORE`d during propagation, not rejected as invalid: [2](#0-1) 

However, the authoritative state-transition function `process_execution_payload_bid` takes `bid.fee_recipient` as-is and stores it verbatim into the `BuilderPendingPayment`/`BuilderPendingWithdrawal` that will later be paid out - there is no assertion anywhere in this function comparing it to the proposer's stated preference: [3](#0-2) 

The honest-validator duty specification for block construction likewise never requires the proposer to verify `bid.fee_recipient` before including the bid; it only lists signature, balance, slot, and hash-linkage checks (all reiterating `process_execution_payload_bid`), and explicitly notes that whatever the field says is who gets paid: [4](#0-3) 

Since the bid is entirely authored and signed by the builder (the builder sets `bid.fee_recipient` itself per the builder duty spec, `bid.fee_recipient` "to be an execution address to receive the payment"), the builder unilaterally controls where its own promised value ends up, and any recipient who receives the bid outside the gossip filter (directly from a builder/relay, which the validator spec explicitly permits: "The block proposer MAY obtain these signed messages by other off-protocol means") has no consensus-level guarantee that the payment lands at the proposer's chosen address [5](#0-4) . The value flows unchanged through epoch-boundary settlement into `builder_pending_withdrawals` with the same, unchecked `fee_recipient` [6](#0-5) .

### Impact Explanation
This breaks the equality "builder payment goes to the entity the block/proposer committed to receive it." All spec-following nodes will accept a block whose bid's `fee_recipient` diverges from the proposer's declared preference, since `process_execution_payload_bid` performs no such check. This matches the explicitly named High-impact category "a builder payment or withdrawal misdirected." Because a builder can route its payment to the zero address, the Gwei can also be effectively destroyed rather than paid to anyone, rather than benefiting the proposer's intended recipient - a Gwei not paid to its rightful owner.

### Likelihood Explanation
The bug requires no coalition, node bug, or foreign key — it requires only that a single builder submit its own bid (which it fully controls and signs) with a `fee_recipient` differing from the proposer's stated preference, delivered via any channel that bypasses the advisory gossip filter (explicitly sanctioned by the spec: "MAY obtain these signed messages by other off-protocol means", e.g. private relay/mev-boost style flows). Because the check is entirely absent from the state-transition function, every spec-conformant client will accept such a block; only the optional p2p relay layer discourages it, and only via `IGNORE`, not `REJECT`.

### Recommendation
Add an explicit assertion inside `process_execution_payload_bid` (or in the honest-validator block-construction steps in `specs/gloas/validator.md`) that `bid.fee_recipient` equals the currently accepted `SignedProposerPreferences.fee_recipient` for `bid.slot`/`dependent_root`, elevating the current gossip-only `IGNORE` check to a consensus-level `assert`, so a bid with a wrong `fee_recipient` is an invalid block rather than merely unrelayed gossip.

### Proof of Concept
1. Validator `V` broadcasts `SignedProposerPreferences{proposal_slot=S, fee_recipient=A}` for its upcoming proposal slot `S` [7](#0-6) .
2. Builder `B` (funded, active) constructs and signs `ExecutionPayloadBid{slot=S, value=v, fee_recipient=Z}` where `Z != A` (e.g. `Z` = the zero address), and sends it directly to `V` off-protocol (permitted per spec) instead of via gossip, thereby never triggering the p2p `fee_recipient` `IGNORE` check [8](#0-7) .
3. `V` includes this `SignedExecutionPayloadBid` in its block; `process_execution_payload_bid` validates signature/balance/slot/hash linkage only, accepts it, and records `withdrawal.fee_recipient = Z` [9](#0-8) .
4. At settlement, `v` Gwei is paid to `Z`, not `A`, even though every spec-following node accepts the block as valid — the payment was misdirected without violating any consensus rule.

### Citations

**File:** specs/gloas/p2p-interface.md (L975-980)
```markdown
    proposer_preferences = seen.proposer_preferences[prefs_key]

    # [IGNORE] The bid's fee recipient matches the proposer's preference
    if bid.fee_recipient != proposer_preferences.fee_recipient:
        raise GossipIgnore("bid's fee recipient does not match the proposer's preference")

```

**File:** specs/gloas/p2p-interface.md (L1044-1049)
```markdown
##### New `proposer_preferences`

This topic is used to propagate signed proposer preferences as
`SignedProposerPreferences`. These messages allow validators to communicate
their preferred `fee_recipient` and `target_gas_limit` to builders.

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

**File:** specs/gloas/validator.md (L155-186)
```markdown
A validator constructs each `SignedProposerPreferences` with
`get_signed_proposer_preferences` for each `proposal_slot` in
`get_upcoming_proposal_slots(state, validator_index)`. Let `head_root` be the
validator's current head root, `fee_recipient` be the execution address where
the validator wishes to receive the builder payment, and `target_gas_limit` be
the validator's preferred gas limit for the execution payload.

```python
def get_signed_proposer_preferences(
    store: Store,
    state: BeaconState,
    head_root: Root,
    proposal_slot: Slot,
    validator_index: ValidatorIndex,
    fee_recipient: ExecutionAddress,
    target_gas_limit: Uint64,
    privkey: int,
) -> SignedProposerPreferences:
    proposal_epoch = compute_epoch_at_slot(proposal_slot)
    dependent_root = get_shuffling_dependent_root(store, head_root, proposal_epoch)
    preferences = ProposerPreferences(
        dependent_root=dependent_root,
        proposal_slot=proposal_slot,
        validator_index=validator_index,
        fee_recipient=fee_recipient,
        target_gas_limit=target_gas_limit,
    )
    domain = get_domain(state, DOMAIN_PROPOSER_PREFERENCES, proposal_epoch)
    signing_root = compute_signing_root(preferences, domain)
    signature = bls.Sign(privkey, signing_root)
    return SignedProposerPreferences(message=preferences, signature=signature)
```
```

**File:** specs/gloas/validator.md (L195-224)
```markdown
##### Signed execution payload bid

To obtain `signed_execution_payload_bid`, a block proposer building a block on
top of a `state` MUST take the following actions in order to construct the
`signed_execution_payload_bid` field in `BeaconBlockBody`:

- Listen to the `execution_payload_bid` gossip global topic and save an accepted
  `signed_execution_payload_bid` from a builder. The block proposer MAY obtain
  these signed messages by other off-protocol means.
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

*Note*: The execution address encoded in the `fee_recipient` field in the
`signed_execution_payload_bid.message` will receive the builder payment.
```

**File:** specs/_features/eip8205/beacon-chain.md (L500-517)
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
