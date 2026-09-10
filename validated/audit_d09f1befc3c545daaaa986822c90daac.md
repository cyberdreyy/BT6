### Title
Proposer's fee-recipient preference is unenforceable at consensus level, allowing builder-steerable payment misdirection - (File: specs/gloas/beacon-chain.md, function `process_execution_payload_bid`)

### Summary
In Gloas (ePBS), a proposer publishes a `SignedProposerPreferences` message declaring their `fee_recipient` off-chain, and builders are instructed to set `bid.fee_recipient` to match it [1](#0-0) . But this binding is never checked by the state-transition function. `process_execution_payload_bid` records a `BuilderPendingWithdrawal` using `bid.fee_recipient` taken directly and unconditionally from the signed bid, with no comparison against any state-stored proposer preference [2](#0-1) . The only place `fee_recipient` is checked against the proposer's declared preference is the p2p gossip validator, and even there it is a soft `[IGNORE]`, not a `[REJECT]` [3](#0-2) . `ProposerPreferences` is a pure networking-layer type — it is never added to `BeaconState` and never referenced anywhere in `specs/gloas/beacon-chain.md`'s state transition, confirmed by the fact that `ProposerPreferences`/`target_gas_limit` only appear in `p2p-interface.md`, `validator.md`, and test helpers, never in the state-transition spec files.

### Finding Description
This is the same bug class as the report: an authoritative destination value (there, `redirect_uri`; here, the proposer's `fee_recipient`) is supposed to be pinned to a value the legitimate party controls, but the enforcement of that binding is delegated to a soft, bypassable layer (there, an optional nginx `Host` header check; here, a `[IGNORE]`-level gossip rule) instead of the authoritative validation path (there, the OIDC handler; here, the consensus state-transition function). A validator's block-building software that trusts the gossip-network-level "best bid" tracking (`seen.best_execution_payload_bid`, filtered supposedly by fee_recipient at gossip time per `validate_execution_payload_bid_gossip`) can end up accepting a bid whose `fee_recipient` was never actually re-validated against the proposer's own preference at inclusion time, because the consensus layer imposes no such constraint. Any block containing a `SignedExecutionPayloadBid` with an arbitrary `fee_recipient` set by the builder is fully valid per `process_execution_payload_bid`, and the resulting `BuilderPendingWithdrawal.fee_recipient` (ultimately paid out from the builder's balance) is whatever the builder chose [2](#0-1) .

Because gossip-level `fee_recipient` matching is only `[IGNORE]` (not `[REJECT]`) [4](#0-3) , a bid with a mismatched `fee_recipient` is not equivocation and is not something the p2p layer treats as invalid content — it is simply deprioritized for relay. A node that receives such a bid directly (not through normal global gossip relay — e.g., builder-to-proposer direct submission, common in real MEV-boost-style architectures) or whose validator client naively selects the highest-value bid without independently re-checking `fee_recipient == own_preference` will construct/accept a block committing payment to the wrong address, and this block is 100% valid under the state-transition rules since consensus never checks the binding.

### Impact Explanation
This matches the "builder payment ... misdirected" High-impact category from the rules. The equality broken is: *the Gwei paid to the proposer as their builder payment should go to the proposer's declared `fee_recipient`, but the protocol allows it to be silently redirected to any address the builder chooses*, because the only check tying `bid.fee_recipient` to the proposer's `ProposerPreferences` lives in an advisory, ignorable networking rule rather than in `process_execution_payload_bid`. This is not the attacker spending their own stake for no gain — the "attacker" here is the builder, who unilaterally controls where the payment (their own funds, transferred to "the proposer") lands, defeating the entire purpose of the `ProposerPreferences` mechanism as a binding commitment.

### Likelihood Explanation
Likelihood depends on how strictly validator/proposer client implementations independently re-verify `fee_recipient` before including a bid, beyond what gossip validation already filtered. Because the spec's own gossip rule for this is explicitly `[IGNORE]` rather than `[REJECT]`, the spec text itself signals this is not treated as an invalid/malicious message, increasing the chance that client implementations (built to the spec's letter) omit an independent recheck at inclusion time, especially for bids received via non-gossip channels (direct builder API/relay submission, which the spec text does not preclude).

### Recommendation
Enforce the `fee_recipient` binding at the consensus layer, not just at the optional gossip layer. Options: (a) commit the proposer's `ProposerPreferences.fee_recipient` into `BeaconState` (e.g., via a registered preference keyed by validator index) and have `process_execution_payload_bid` `assert bid.fee_recipient == state's registered preference for the current proposer`; or (b) upgrade the gossip-level check from `[IGNORE]` to `[REJECT]` and explicitly document that proposer/validator client software MUST independently reject any bid whose `fee_recipient` does not match its own signed preference before including it in a block, regardless of gossip-layer filtering.

### Proof of Concept
1. Builder B observes proposer P's `SignedProposerPreferences` with `fee_recipient = X` (broadcast/known via gossip per `specs/gloas/p2p-interface.md`).
2. B crafts `ExecutionPayloadBid` with `bid.fee_recipient = Y` (attacker-controlled), signs it correctly, and satisfies all other checks in `process_execution_payload_bid` (`is_active_builder`, `can_builder_cover_bid`, valid signature, correct slot/parent/randao) [5](#0-4) .
3. B submits this bid directly to P (or via a relay/builder API not subject to global gossip `[IGNORE]` filtering), bypassing the soft gossip check entirely.
4. P's block-building software includes the highest-value bid without an independent re-check of `fee_recipient`.
5. State transition processes the block: `process_execution_payload_bid` accepts the bid unconditionally with respect to `fee_recipient`, recording `BuilderPendingWithdrawal(fee_recipient=Y, amount=bid.value)` [2](#0-1) .
6. The builder's payment is later delivered to address `Y` instead of P's declared `X`, with the block being fully valid — no consensus rule was violated.

### Citations

**File:** specs/gloas/builder.md (L134-139)
```markdown
07. Set `bid.fee_recipient` to be an execution address to receive the payment.
    The proposer's preferred fee recipient is obtained from the
    `SignedProposerPreferences` whose `message.proposal_slot` matches `bid.slot`
    and whose `message.dependent_root` matches
    `get_shuffling_dependent_root(store, bid.parent_block_root, compute_epoch_at_slot(bid.slot))`,
    where `store` is the fork choice store.
```

**File:** specs/gloas/beacon-chain.md (L2094-2123)
```markdown
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

```

**File:** specs/gloas/beacon-chain.md (L2124-2131)
```markdown
    # Record the pending payment if there is some payment
    if amount > 0:
        pending_payment = BuilderPendingPayment(
            weight=Gwei(0),
            withdrawal=BuilderPendingWithdrawal(
                fee_recipient=bid.fee_recipient,
                amount=amount,
                builder_index=builder_index,
```

**File:** specs/gloas/p2p-interface.md (L975-980)
```markdown
    proposer_preferences = seen.proposer_preferences[prefs_key]

    # [IGNORE] The bid's fee recipient matches the proposer's preference
    if bid.fee_recipient != proposer_preferences.fee_recipient:
        raise GossipIgnore("bid's fee recipient does not match the proposer's preference")

```
