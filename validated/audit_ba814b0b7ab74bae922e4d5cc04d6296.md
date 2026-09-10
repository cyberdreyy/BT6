### Title
Builder can misdirect the proposer's promised payment because `process_execution_payload_bid` never validates `bid.fee_recipient` - ([File: specs/gloas/beacon-chain.md])

### Summary
The Gloas builder-payment design intends that a builder's `ExecutionPayloadBid.fee_recipient` be set to the *proposer's* declared preferred address (from `SignedProposerPreferences`), so that when the bid is honored the builder's payment is delivered to the proposer who accepted it. However, the state-transition function `process_execution_payload_bid` — the sole consensus-binding validity check for a bid — never asserts any relationship between `bid.fee_recipient` and the proposer's registered preference. The only place this equality is checked is in gossip validation, and there only as a non-binding `[IGNORE]`. This is the same bug class as the reported PoolTogether `_canAwardExternal` finding: a comparison meant to bind an entitlement (here, "payment goes to the correct proposer address") to the correct identity is missing from the code path that actually authorizes the state mutation, so the invariant can be silently broken by an unprivileged party (the builder) with no cooperation from any other role required to still be committed to consensus.

### Finding Description
`specs/gloas/beacon-chain.md`'s `process_execution_payload_bid` performs a series of `assert` checks on `bid` (signature, active/payload-builder status, funds coverage, slot/parent/randao/blob-commitment consistency) but at no point checks `bid.fee_recipient`: [1](#0-0) 

The recorded `BuilderPendingPayment.withdrawal.fee_recipient` is taken verbatim from the bid: [2](#0-1) 

This value is later paid out unconditionally (via `process_builder_pending_payments` → `builder_pending_withdrawals` → the builder withdrawal sweep), with the recipient address again taken from the stored `fee_recipient`: [3](#0-2) 

By contrast, `specs/gloas/builder.md` documents that the builder is *supposed* to set `bid.fee_recipient` to match the proposer's declared preference (obtained via `SignedProposerPreferences`): [4](#0-3) 

The only actual enforcement of "`bid.fee_recipient` matches the proposer's preference" anywhere in the spec is a gossip-layer `[IGNORE]` — explicitly a *soft*, best-effort filter, not a `[REJECT]` and not part of block validity: [5](#0-4) 

Because it is only an `[IGNORE]`, a non-conforming bid is simply not *forwarded* over that particular gossip topic by honest relayers — it does not make the bid invalid, and it does not prevent the bid from reaching a proposer by other means. Indeed, `specs/gloas/builder.md` itself sanctions out-of-band delivery: "the block proposer MAY obtain these signed messages by other off-protocol means." The proposer-side construction algorithm in `specs/gloas/validator.md` lists the verification conditions the proposer must apply before including a bid, and this list mirrors `process_execution_payload_bid`'s asserts exactly — it contains no fee_recipient check: [6](#0-5) 

So a proposer following the specification's own honest-proposer algorithm to the letter (accepting a bid via off-protocol channels, checking exactly the enumerated conditions) has no requirement to verify `bid.fee_recipient` matches their own preference, and the state-transition function that actually finalizes the payment binding never checks it either. A builder who wants to avoid paying out — e.g., by setting `bid.fee_recipient` to an address it controls instead of the proposer's registered address — produces a bid that is 100% valid under `process_execution_payload_bid` and fully spec-compliant if delivered outside the advisory gossip filter (or even via gossip if the "matching preferences not yet seen" `[IGNORE]` window is missed, since seeing preferences is itself best-effort/opportunistic and not a REJECT precondition).

This breaks the equality the protocol design implies must hold: `payment.recipient == accepting_proposer.declared_fee_recipient`. Nothing in the consensus rules enforces this binding; it is enforced only by social convention and non-binding gossip hygiene.

### Impact Explanation
This falls under the explicitly listed High-impact category: "a builder payment or withdrawal misdirected." The Gwei paid out of the builder's balance (`can_builder_cover_bid`/`settle_builder_payment` path) is real value the protocol design promises to the accepting proposer, yet the code never binds the recipient to that proposer's identity/preference. A builder can construct a bid that is entirely consensus-valid while sending the promised payment to an address of its own choosing, effectively nullifying the payment owed to the honest proposer who accepted the bid in good faith, expecting the payment terms advertised via `SignedProposerPreferences` to be honored. No malicious node, client bug, or coalition is required — a single unprivileged builder acting alone and strictly within protocol rules can do this.

### Likelihood Explanation
Likelihood is moderate-to-high for a profit-motivated builder: constructing a bid with an arbitrary `fee_recipient` requires no special access — only a valid BLS signature over the bid, which any registered builder already produces. The only friction is that gossip relayers apply a non-binding `[IGNORE]` filter, but (a) the spec explicitly allows and describes proposers receiving bids "by other off-protocol means," and (b) even within gossip, the filter only fires if `seen.proposer_preferences` already contains a matching entry for that slot/dependent-root — an opportunistic, timing-dependent condition, not a hard precondition for block inclusion. There is no on-chain penalty or invalidation for a mismatched `fee_recipient`.

### Recommendation
Bind the equality directly in the state-transition function rather than relying on gossip hygiene. Concretely, `process_execution_payload_bid` should assert that `bid.fee_recipient` matches the fee recipient declared by the accepting proposer for that slot (e.g., by requiring/verifying the proposer's most recent valid `SignedProposerPreferences` for `bid.slot`/`dependent_root` and asserting `bid.fee_recipient == preferences.fee_recipient`), so the payment recipient becomes a consensus-enforced invariant instead of an advisory gossip-only check.

### Proof of Concept
1. A registered active payload builder `B` observes an upcoming proposal slot `S` for proposer `P`, and (optionally) sees `P`'s `SignedProposerPreferences` declaring `fee_recipient = F_P`.
2. `B` constructs `ExecutionPayloadBid` with `bid.value = V > 0` and `bid.fee_recipient = F_B` (an address `B` controls), satisfying all other fields (`parent_block_hash`, `parent_block_root`, `prev_randao`, `slot`, `blob_kzg_commitments`, `execution_requests_root`) required by `process_execution_payload_bid` [7](#0-6) , and signs it validly.
3. `B` sends this bid to `P` off-protocol/directly (sanctioned per `specs/gloas/builder.md`) rather than solely via the `execution_payload_bid` gossip topic, bypassing the `[IGNORE]` fee-recipient filter in `specs/gloas/p2p-interface.md`.
4. `P`, following `specs/gloas/validator.md`'s listed verification conditions (none of which reference `fee_recipient`) [8](#0-7) , includes `signed_execution_payload_bid` in its block.
5. `process_execution_payload_bid` accepts the block (all asserts pass; `fee_recipient` is never checked) and records `BuilderPendingPayment.withdrawal.fee_recipient = F_B, amount = V` [2](#0-1) .
6. Once quorum is reached, `process_builder_pending_payments` moves this into `builder_pending_withdrawals`, and the eventual sweep pays `V` Gwei to `F_B` instead of `F_P` [3](#0-2) .
7. Result: `P` accepted the bid expecting payment to `F_P`, but the payment was fully redirected to `F_B`, with the entire block remaining valid under every consensus rule.

### Citations

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

**File:** specs/gloas/beacon-chain.md (L2086-2141)
```markdown
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

**File:** specs/gloas/validator.md (L195-221)
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
```
