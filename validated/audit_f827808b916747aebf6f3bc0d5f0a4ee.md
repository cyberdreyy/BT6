### Title
Builder payment `fee_recipient` is committed and paid with no consensus-level check that it matches the proposer's own preference - ([File: specs/gloas/beacon-chain.md])

### Summary
`process_execution_payload_bid` in `specs/gloas/beacon-chain.md` records a `BuilderPendingPayment`/`BuilderPendingWithdrawal` using `bid.fee_recipient` taken verbatim from the builder-signed `ExecutionPayloadBid`, with no state-transition check that this address matches the proposer's actual registered/preferred fee recipient for that slot. The "match" is only checked as a soft, non-binding gossip-layer `IGNORE` rule in `specs/gloas/p2p-interface.md`, which does not affect block/state validity and can be trivially bypassed since builders and proposers are explicitly permitted to exchange bids "by other off-protocol means" (`specs/gloas/validator.md`).

### Finding Description
The intended design (documented in `specs/gloas/builder.md` step 07 and `specs/gloas/validator.md` line 223) is: the builder should set `bid.fee_recipient` to the address the proposer specified in its signed `ProposerPreferences`, so that when the bid is accepted the builder's payment lands with the correct proposer. [1](#0-0) 

However, nothing in the state-transition function that actually processes the bid enforces this link. `process_execution_payload_bid` validates signature, builder activity/version, funds coverage, slot/hash/root/prev_randao correctness — but never checks `bid.fee_recipient` against anything tied to the current proposer: [2](#0-1) 

The list of MUST-verify conditions a proposer is instructed to check before including a bid in `specs/gloas/validator.md` likewise omits any `fee_recipient` check: [3](#0-2) 

The only place `fee_recipient` matching is checked at all is a gossip-network `IGNORE` (not `REJECT`) rule, which only affects message propagation, not block validity: [4](#0-3) 

Because the proposer is also explicitly allowed to obtain the bid "by other off-protocol means" (bypassing gossip validation entirely) per `specs/gloas/validator.md` lines 201–203, and because `process_execution_payload_bid` unconditionally trusts `bid.fee_recipient` when building the `BuilderPendingWithdrawal` that is later settled and paid out, a builder fully controls the destination of its own committed payment with zero consensus-enforced binding to the actual proposer's registered preference. This is structurally the same class of bug as the mTokenGateway report: an authorization/consistency check (`ifNotBlacklisted`/fee-recipient-matches-preference) exists only in a layer that can be bypassed (overwritten `receiver` / gossip-only `IGNORE`), while the state-mutating code path (`_outHere` / `process_execution_payload_bid`) performs the actual payment using an unchecked value.

### Impact Explanation
This falls under the "builder payment ... misdirected" High-impact category. A payment that the block commits to (via the signed bid included in `block.body.signed_execution_payload_bid`) can be redirected away from the intended fee recipient with no consensus rule preventing it — the check exists solely as an unenforced networking heuristic, not a state-transition invariant. Any consensus client that implements only the documented MUST-checks in `validator.md`/`process_execution_payload_bid` will accept and settle payments regardless of `fee_recipient` correctness.

### Likelihood Explanation
Likelihood is limited by the fact that a rational proposer normally would decline to include a bid that doesn't pay their own preferred address, since the market naturally discourages accepting "bad" bids — but the spec text provides no consensus safety net, and off-protocol (relay-based) bid delivery, which the spec explicitly sanctions, sidesteps gossip's `IGNORE` heuristic entirely. Client implementations that faithfully follow only the enumerated verification list in `validator.md` (which omits fee_recipient matching) would blindly include the builder-chosen fee_recipient, creating a real gap between documented intent and enforced spec behavior.

### Recommendation
Add an explicit assertion in `process_execution_payload_bid` (or in the proposer's block-construction MUST-list in `validator.md`) that `bid.fee_recipient` equals the proposer's registered/committed preference for that slot (e.g., verified against a signed `ProposerPreferences` structure referenced by the state), turning the current gossip-only `IGNORE` heuristic into a binding consensus rule enforced by `process_execution_payload_bid`, analogous to adding the missing `_sender` blacklist check directly in `_outHere`.

### Proof of Concept
1. A builder constructs a valid `ExecutionPayloadBid` with `bid.value = V > 0` and `bid.fee_recipient = attacker_address` (instead of the proposer's actual preferred address from `SignedProposerPreferences`).
2. The builder signs it correctly, satisfying `verify_execution_payload_bid_signature`.
3. The proposer (or a relay acting on its behalf) delivers this bid "by other off-protocol means" (permitted per `specs/gloas/validator.md` lines 201-203), bypassing the gossip-level `IGNORE` check that would otherwise flag the fee_recipient mismatch.
4. The proposer includes `signed_execution_payload_bid` in its block. `process_execution_payload_bid` passes all its assertions (lines 2094-2122 in `specs/gloas/beacon-chain.md`) since none of them reference the proposer's preferred address.
5. `state.builder_pending_payments[...]` is populated with `withdrawal.fee_recipient = attacker_address` (line 2129), and this is later settled into `state.builder_pending_withdrawals` and eventually paid out via the withdrawal-processing pipeline — with `V` Gwei paid to `attacker_address` instead of the proposer's intended recipient, with no consensus rule ever having verified the match.

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
