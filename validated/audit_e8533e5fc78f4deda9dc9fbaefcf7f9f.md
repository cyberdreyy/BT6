### Title
Builder Payment Recipient Is Never Validated At The Consensus Layer, Allowing A Builder To Misdirect Its Bid Payment Away From The Proposer's Intended Address - (File: specs/gloas/beacon-chain.md)

### Summary
`process_execution_payload_bid` in `specs/gloas/beacon-chain.md` accepts `bid.fee_recipient` — a field fully controlled by the (unprivileged) builder — and writes it verbatim into `BuilderPendingWithdrawal.fee_recipient`, which later determines where the builder's payment Gwei is settled. Nowhere in the state transition, nor in the canonical list of "MUST satisfy" verification conditions that a proposer's client is instructed to check before including a bid (`specs/gloas/validator.md`), is `bid.fee_recipient` checked against the proposer's actual designated recipient. The only place this is checked is a non-binding, bypassable p2p gossip heuristic.

### Finding Description
`process_execution_payload_bid` records the pending builder payment using the recipient address supplied inside the signed bid itself, with no cross-check against any state-tracked, proposer-authorized address: [1](#0-0) 

There is no field in `BeaconState` that records the block proposer's committed fee-recipient preference and no assertion tying `bid.fee_recipient` to it. The commitment mechanism that is supposed to bind `fee_recipient` to the proposer's wishes — `SignedProposerPreferences`, matched by `dependent_root`/`proposal_slot` — exists purely at the gossip layer: [2](#0-1) 

Critically, this check is a `GossipIgnore` (soft, drop-from-relay-only) rather than a `GossipReject` (protocol-violation), and it is explicitly optional to route through: the validator specification permits a proposer to obtain a bid "by other off-protocol means," bypassing gossip validation entirely: [3](#0-2) 

The list of conditions the proposer's client is told it "MUST" verify before including a bid (mirroring `process_execution_payload_bid`) does **not** include any fee_recipient match — it only checks signature validity, builder solvency, slot/parent/randao alignment. This means the state-transition function that actually commits the payment recipient — the consensus-layer analog of `ZeroExAdapter.getExecutionData` in the original report — trusts the caller-supplied `fee_recipient` completely, exactly as `ZeroExAdapter` trusted the caller-supplied 0x order's recipient field without validating it against the vault (`from`).

### Impact Explanation
This breaks the equality "a builder payment ... misdirected," which is explicitly listed as a High-impact class. A builder is an unprivileged bid author; by simply signing `bid.fee_recipient` to any address of its choosing (e.g. its own `execution_address`) and delivering that bid to the proposer, the builder can cause the payment Gwei that is later moved out of `builder_pending_payments`/`builder_pending_withdrawals` (via `settle_builder_payment` and eventual withdrawal processing) to go to an address the proposer never authorized — instead of the proposer's `SignedProposerPreferences.fee_recipient`. Because consensus rules never assert this binding, an implementation that faithfully follows the literal "MUST" list in `validator.md` (or any proposer client that consumes external builder relays / off-protocol bid sources, which the spec explicitly sanctions) has no protocol-level protection: the block is still validly accepted by every spec-following node even though the fee_recipient diverges from what the proposer actually wanted.

### Likelihood Explanation
No coalition, malicious peer, or client bug is required — only an ordinary unprivileged builder submitting a signed bid with an arbitrary `fee_recipient`, routed to the proposer via any off-protocol channel the spec explicitly allows. Any spec-compliant proposer implementation that trusts the bid's signature/slot/parent checks (the only ones enumerated as mandatory) accepts it, since `process_execution_payload_bid` performs no fee_recipient validation whatsoever.

### Recommendation
Bind `bid.fee_recipient` to the proposer's committed value at the consensus layer rather than relying solely on an ignorable gossip heuristic. Concretely, either (a) have the block proposer's `BeaconBlockBody` (or a dedicated field) commit the proposer's chosen `fee_recipient` for the slot, and add an assertion in `process_execution_payload_bid` that `bid.fee_recipient == committed_fee_recipient`, or (b) promote the existing gossip-level fee_recipient match from `GossipIgnore` to a state-transition-level `assert`, so that any block including a bid whose `fee_recipient` doesn't match the proposer's authenticated `SignedProposerPreferences` is invalid and rejected by all spec-following nodes.

### Proof of Concept
1. Builder `B` (active, sufficiently funded) constructs `bid` with `bid.builder_index = B`, a valid `value`, and `bid.fee_recipient = B.execution_address` (i.e., itself) rather than the actual proposer's preferred address from `SignedProposerPreferences`.
2. `B` signs the bid per `get_execution_payload_bid_signature` [4](#0-3)  and transmits it directly to the proposer off-protocol (permitted per [5](#0-4) ), skipping the gossip `validate_execution_payload_bid_gossip` fee_recipient IGNORE check entirely.
3. The proposer includes `signed_execution_payload_bid` in its block; `process_execution_payload_bid` runs and passes all its assertions (signature, slot, parent_block_hash, prev_randao, solvency) — none of which reference fee_recipient [6](#0-5) .
4. `state.builder_pending_payments[...]` is populated with `withdrawal.fee_recipient = B.execution_address`. When `settle_builder_payment`/`apply_parent_execution_payload` later processes this entry, the builder's payment Gwei is transferred to `B`'s own address instead of the proposer's intended recipient — the payment is misdirected, and no spec-following node treats the block as invalid.

### Citations

**File:** specs/gloas/beacon-chain.md (L2087-2137)
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
```

**File:** specs/gloas/p2p-interface.md (L969-980)
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

**File:** specs/gloas/validator.md (L197-219)
```markdown
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
```

**File:** specs/gloas/builder.md (L162-169)
```markdown
```python
def get_execution_payload_bid_signature(
    state: BeaconState, bid: ExecutionPayloadBid, privkey: int
) -> BLSSignature:
    domain = get_domain(state, DOMAIN_BEACON_BUILDER, compute_epoch_at_slot(bid.slot))
    signing_root = compute_signing_root(bid, domain)
    return bls.Sign(privkey, signing_root)
```
```
