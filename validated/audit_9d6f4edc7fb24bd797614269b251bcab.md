### Title
Unbacked Gwei minting via self-certified `execution_requests_root` never validated by the execution engine in state transition - (File: specs/gloas/beacon-chain.md)

### Summary
In Gloas (ePBS/EIP-7732), a builder's bid commits to an `execution_requests_root` hash [1](#0-0)  that is accepted into state by `process_execution_payload_bid` without any check against the execution engine [2](#0-1) . The following block's `process_parent_execution_payload` decides whether the parent payload was "FULL" and applies `parent_execution_requests` (minting deposits, processing withdrawals, etc.) purely by checking that the supplied requests hash-match the bid's self-declared `execution_requests_root` [3](#0-2) . Neither of these functions, nor `process_block`/`state_transition`, ever calls `execution_engine.verify_and_notify_new_payload` — that call exists only in `verify_execution_payload_envelope`, which is invoked solely from the fork-choice handler `on_execution_payload_envelope`, not from the deterministic state transition [4](#0-3) [5](#0-4) .

### Finding Description
This mirrors the Solana bridge bug exactly: a value (a "deposit"/execution-request set) is trusted and applied to state without ever confirming with the authoritative execution engine that it actually succeeded/was produced by real EL execution.

Concretely:
1. `process_execution_payload_bid` accepts an `ExecutionPayloadBid` and caches it as `state.latest_execution_payload_bid = bid` after checking builder solvency/signature/slot/parent-hash consistency — it never inspects or validates `bid.execution_requests_root` against anything real [6](#0-5) . A builder (especially a self-builder, `BUILDER_INDEX_SELF_BUILD`, which requires zero value/no signature verification burden beyond `G2_POINT_AT_INFINITY`) can set this root to the hash of an arbitrary, fabricated `ExecutionRequests` object (e.g., containing large `DepositRequest`s) that was never produced by any real execution engine.
2. `process_parent_execution_payload`, called from `process_block` of the *next* block, decides FULL vs EMPTY purely from `bid.parent_block_hash == parent_bid.block_hash` — a field entirely controlled by whoever proposes that next block — and then only checks `hash_tree_root(requests) == parent_bid.execution_requests_root` before calling `apply_parent_execution_payload`, which unconditionally runs `process_deposit_request`, `process_withdrawal_request`, `process_consolidation_request`, etc. on the supplied requests [7](#0-6) [8](#0-7) .
3. The only place that ever calls `execution_engine.verify_and_notify_new_payload` to confirm a payload is genuinely valid EL-side is `verify_execution_payload_envelope`, invoked from the fork-choice-only handler `on_execution_payload_envelope` [5](#0-4) . This is explicitly described as separate from block validity: "State transitions that trigger an unhandled exception ... are considered invalid" vs. "The validity of a signed execution payload envelope ... is checked by `verify_execution_payload_envelope`" [9](#0-8) .

Because `state_transition()`/`process_block()` (the deterministic function that defines whether a block is a valid state transition) never calls the execution engine for the parent's payload, a block that "reveals" self-consistent but entirely fabricated `parent_execution_requests` (matching a `execution_requests_root` that the same actor freely chose in an earlier bid) passes state-transition validity with no assertion failure. Only the separate, weight-based fork-choice/PTC-attestation mechanism (`payload_timeliness`, `is_payload_verified`) would down-weight such a block if the envelope was never genuinely delivered/verified — but that is a liveness/weighting property, not a state-transition-level validity guarantee. A single validator who is selected to build (self-build) one slot and, after any number of missed slots, is later selected to propose a subsequent block still extending that same head, can supply the fabricated requests to itself and mint Gwei that never passed through real EL execution.

### Impact Explanation
This breaks the core equality "Gwei created on the CL corresponds to a genuine EL-confirmed execution payload." A validator can mint arbitrary deposits (or trigger fabricated withdrawal/consolidation/builder-exit requests) into `state` via `apply_parent_execution_payload` without the execution engine ever validating the underlying payload, since that check is deferred entirely to fork choice rather than encoded in `state_transition`. This is a Critical-severity class of finding under the stated rubric ("Gwei created ... a payload ... applied that the block did not commit to" — here the *commitment* itself was never checked against reality by the deterministic validity rule).

### Likelihood Explanation
Requires only a single validator acting as a self-builder for one slot (trivial, no signature burden, `bid.value` can be 0) and later being selected as proposer of a subsequent block that still treats that slot's bid as the latest (which happens naturally whenever intervening slots are missed, a routine occurrence on the network). No coalition, no majority stake, and no client bug is needed — purely a gap in the specified state-transition validity rules for parent-payload processing in Gloas.

### Recommendation
`process_parent_execution_payload`/`apply_parent_execution_payload` (or an earlier stage of the deterministic state transition) should require cryptographic/engine-level proof that the claimed parent payload was actually validated by the execution engine (e.g., require and verify a `SignedExecutionPayloadEnvelope` with a valid `execution_engine.verify_and_notify_new_payload` result as part of `process_block`/`state_transition`, rather than relying solely on hash self-consistency between the bid's `execution_requests_root` and the block-supplied `parent_execution_requests`). Alternatively, bind builder payment settlement and request application strictly to `store.payloads`/envelope verification results rather than to values freely chosen by the block proposer.

### Proof of Concept
Conceptual state-transition trace (no test/config files referenced, purely core spec functions):
1. Slot N: Validator B is self-builder. B submits `SignedExecutionPayloadBid` with `builder_index=BUILDER_INDEX_SELF_BUILD`, `value=0`, and `execution_requests_root = hash_tree_root(fabricated_requests)` where `fabricated_requests` contains a `DepositRequest` crediting B's own validator with a large `amount` that was never processed by any real execution engine. `process_execution_payload_bid` accepts this unconditionally (no check on `execution_requests_root`) [10](#0-9) .
2. B never broadcasts a real `SignedExecutionPayloadEnvelope` (or broadcasts one that gets rejected by honest nodes' gossip/fork-choice checks) — so on honest nodes, `store.payloads` never contains an entry for block N, and fork choice would treat N's payload as EMPTY at the next decision point.
3. Some slots are missed (N+1 .. N+k-1), so `state.latest_execution_payload_bid` (still block N's bid) and `state.latest_block_header` (still block N) remain unchanged in any node replaying the chain.
4. At slot N+k, the SAME validator B (or a colluding proposer B gave the pre-image to) is selected to propose. B sets `block.body.signed_execution_payload_bid.message.parent_block_hash = parent_bid.block_hash` (claiming N was FULL) and `block.body.parent_execution_requests = fabricated_requests`.
5. Running `state_transition(state, block_Nk, validate_result=True)`: `process_parent_execution_payload` checks `bid.parent_block_hash == parent_bid.block_hash` (true) and `hash_tree_root(fabricated_requests) == parent_bid.execution_requests_root` (true by construction) [11](#0-10) , then calls `apply_parent_execution_payload`, which runs `process_deposit_request` on the fabricated deposit, minting Gwei into B's balance — with no call to `execution_engine.verify_and_notify_new_payload` anywhere in this path.
6. The resulting state transition passes with no assertion error, i.e., it is "valid" per the spec's own definition of block validity, despite the minted Gwei never having been backed by any real, engine-verified execution payload.

### Citations

**File:** specs/gloas/beacon-chain.md (L745-763)
```markdown
#### `ExecutionPayloadBid`

```python
class ExecutionPayloadBid(ProgressiveContainer):
    ACTIVE_FIELDS = active_fields(width=12)

    parent_block_hash: Hash32
    parent_block_root: Root
    block_hash: Hash32
    prev_randao: Bytes32
    fee_recipient: ExecutionAddress
    gas_limit: Uint64
    builder_index: BuilderIndex
    slot: Slot
    value: Gwei
    execution_payment: Gwei
    blob_kzg_commitments: BlobKZGCommitments
    execution_requests_root: Root
```
```

**File:** specs/gloas/beacon-chain.md (L1531-1548)
```markdown
State transition is fundamentally modified in Gloas. The full state transition
is broken in two parts, first importing a signed block and then importing an
execution payload.

The post-state corresponding to a pre-state `state` and a signed beacon block
`signed_block` is defined as `state_transition(state, signed_block)`. State
transitions that trigger an unhandled exception (e.g. a failed `assert` or an
out-of-range list access) are considered invalid. State transitions that cause a
`Uint64` overflow or underflow are also considered invalid.

The validity of a signed execution payload envelope `signed_envelope` against a
pre-state `state` is checked by
`verify_execution_payload_envelope(state, signed_envelope, execution_engine)`.
Payload processing is deferred to the next beacon block via
`process_parent_execution_payload`. Payloads that trigger an unhandled exception
(e.g. a failed `assert` or an out-of-range list access) are considered invalid.
Payloads that cause a `Uint64` overflow or underflow are also considered
invalid.
```

**File:** specs/gloas/beacon-chain.md (L1728-1774)
```markdown
```python
def apply_parent_execution_payload(
    state: BeaconState,
    requests: ExecutionRequests,
) -> None:
    parent_bid = state.latest_execution_payload_bid
    parent_slot = state.latest_block_header.slot
    parent_epoch = compute_epoch_at_slot(parent_slot)

    assert len(requests.withdrawals) <= MAX_WITHDRAWAL_REQUESTS_PER_PAYLOAD
    assert len(requests.consolidations) <= MAX_CONSOLIDATION_REQUESTS_PER_PAYLOAD
    assert len(requests.builder_deposits) <= MAX_BUILDER_DEPOSIT_REQUESTS_PER_PAYLOAD
    assert len(requests.builder_exits) <= MAX_BUILDER_EXIT_REQUESTS_PER_PAYLOAD

    # Process execution requests from parent's payload. The execution
    # requests are processed at state.slot (child's slot), not the parent's slot.
    def for_ops(operations: Sequence[Any], fn: Callable[[BeaconState, Any], None]) -> None:
        for operation in operations:
            fn(state, operation)

    for_ops(requests.deposits, process_deposit_request)
    for_ops(requests.withdrawals, process_withdrawal_request)
    for_ops(requests.consolidations, process_consolidation_request)
    for_ops(requests.builder_deposits, process_builder_deposit_request)
    for_ops(requests.builder_exits, process_builder_exit_request)

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

**File:** specs/gloas/beacon-chain.md (L1777-1798)
```markdown
##### New `process_parent_execution_payload`

*Note*: This function validates and processes the parent's execution payload.
`process_parent_execution_payload` must be called before
`process_execution_payload_bid` (which overwrites
`state.latest_execution_payload_bid`).

```python
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

**File:** specs/gloas/fork-choice.md (L656-690)
```markdown
### New `verify_execution_payload_envelope`

```python
def verify_execution_payload_envelope(
    state: BeaconState,
    signed_envelope: SignedExecutionPayloadEnvelope,
    execution_engine: ExecutionEngine,
) -> None:
    envelope = signed_envelope.message
    payload = envelope.payload

    # Verify signature
    assert verify_execution_payload_envelope_signature(state, signed_envelope)

    # Verify consistency with the beacon block
    header = state.latest_block_header.copy()
    header.state_root = hash_tree_root(state)
    assert envelope.beacon_block_root == hash_tree_root(header)
    assert envelope.parent_beacon_block_root == state.latest_block_header.parent_root

    # Verify consistency with the committed bid
    bid = state.latest_execution_payload_bid
    assert envelope.builder_index == bid.builder_index
    assert payload.prev_randao == bid.prev_randao
    assert payload.gas_limit == bid.gas_limit
    assert payload.block_hash == bid.block_hash
    assert hash_tree_root(envelope.execution_requests) == bid.execution_requests_root

    # Verify the execution payload is valid
    assert payload.slot_number == state.slot
    assert payload.parent_hash == state.latest_block_hash
    assert payload.timestamp == compute_time_at_slot(state, state.slot)
    assert hash_tree_root(payload.withdrawals) == hash_tree_root(state.payload_expected_withdrawals)
    assert execution_engine.verify_and_notify_new_payload(
        NewPayloadRequest(
```

**File:** specs/gloas/fork-choice.md (L1090-1117)
```markdown
### New `on_execution_payload_envelope`

The handler `on_execution_payload_envelope` is called when the node receives a
`SignedExecutionPayloadEnvelope` to sync.

```python
def on_execution_payload_envelope(
    store: Store, signed_envelope: SignedExecutionPayloadEnvelope
) -> None:
    """
    Run ``on_execution_payload_envelope`` upon receiving a new execution payload envelope.
    """
    envelope = signed_envelope.message
    # The corresponding beacon block root needs to be known
    assert envelope.beacon_block_root in store.block_states

    # Check if blob data is available
    # If not, this payload MAY be queued and subsequently considered when blob data becomes available
    assert is_data_available(envelope.beacon_block_root)

    state = store.block_states[envelope.beacon_block_root]

    # Verify the execution payload envelope
    verify_execution_payload_envelope(state, signed_envelope, EXECUTION_ENGINE)

    # Add execution payload envelope to the store
    store.payloads[envelope.beacon_block_root] = envelope
```
```
