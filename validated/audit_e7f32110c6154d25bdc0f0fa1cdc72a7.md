### Title
V1 chainstate's tenure-duplicate check only queries globally-accepted blocks, letting a v1 signer sign two conflicting tenure-start blocks - (File: `stacks-signer/src/chainstate/v1.rs`)

### Summary
The Tapioca finding's root cause is a validation asymmetry: one code path checks a strict condition (collateral allowance) while a parallel, equally-necessary condition (borrow allowance) is checked against a weaker/absent standard, letting an attacker route around the missing check. The stacks-signer codebase has the same class of bug between its two chainstate implementations: `SortitionsView::validate_tenure_change_payload` (v1) uses a strictly weaker duplicate-block check than `GlobalStateView::validate_tenure_change_payload` (v2), and this weaker version was never fixed to match.

### Finding Description
Both protocol versions guard against a miner re-proposing a second, conflicting tenure-start block within a tenure the signer has already voted for. The check queries the signer's local DB for "has a block in this tenure already reached a state that would make a second, competing tenure-start block a conflict."

In `stacks-signer/src/chainstate/v2.rs::validate_tenure_change_payload` this is done correctly: [1](#0-0) 
The comment explicitly documents the intended semantics: "Only blocks we have signed (locally or globally accepted) count here." It calls `signer_db.get_last_signed_block(...)`, which covers both `LocallyAccepted` and `GloballyAccepted` states.

In `stacks-signer/src/chainstate/v1.rs::validate_tenure_change_payload`, the equivalent check instead calls `get_last_globally_accepted_block`, which — unlike the v2 version — ignores blocks that are merely `LocallyAccepted` (i.e., signed by this very signer, having crossed the pre-commit/signature threshold, but not yet confirmed by the node as the canonical tip): [2](#0-1) 

This exact discrepancy is independently confirmed by a regression test written against v2 that documents the prior (v1-style) bug and states it was fixed only for v2: [3](#0-2) 
"Before the fix, this would have incorrectly passed because `get_last_globally_accepted_block` would not find the locally-accepted block." No equivalent fix was applied to `v1.rs`, which still uses `get_last_globally_accepted_block` at line 506.

v1 is not dead code — it is the active chainstate implementation whenever a signer's negotiated protocol version is below `GLOBAL_SIGNER_STATE_ACTIVATION_VERSION` (2), including as the deliberate fallback when there is no fleet consensus on protocol version: [4](#0-3) [5](#0-4) 

### Impact Explanation
This breaks the "one signed tenure-start block per tenure" equality that the duplicate check exists to enforce, which the audit rules classify as Critical: "a signer signing an invalid, non-canonical, or conflicting block." Concretely, for a signer stuck on/negotiating protocol version 1 (a legitimate, reachable state — e.g., a mixed-version fleet or a version-consensus fallback), a miner (or byzantine peers colluding with a miner) can:

1. Propose tenure-start block A in tenure T. The signer validates it, the pre-commit threshold is reached, and the signer signs it, moving it to `LocallyAccepted` — but the stacks-node has not yet processed/adopted A as its canonical tip (global acceptance, per `docs/signer-flows.md`, is only granted on a `NewBlock` event or when `check_latest_block_in_tenure` observes the node's tip advance).
2. Before that happens, propose a second, conflicting tenure-start block B for the same tenure T (e.g., different transaction set).
3. On the v1 signer, `validate_tenure_change_payload` calls `get_last_globally_accepted_block(consensus_hash)`, which returns `None` because A is only locally accepted — the `DuplicateBlockFound` rejection never fires.
4. The rest of `check_proposal` may pass B's checks, and the signer proceeds to validate/pre-commit/sign B as well.
5. The same signer has now signed two conflicting tenure-start blocks (A and B) for the same tenure, which is exactly the invariant the check exists to prevent, and which downstream consensus code assumes cannot happen for a single honest signer.

### Likelihood Explanation
The trigger requires only a single miner (who naturally controls block proposal timing/content within a tenure) plus normal signer behavior — no majority of signers, no other signer's key, and no auth-token/local access is needed. The precondition (a block being `LocallyAccepted` but not yet `GloballyAccepted`) is a routine, always-present window in the protocol (the gap between reaching pre-commit/signature threshold and the node broadcasting/adopting the block), not an edge case. The only additional requirement is that the affected signer(s) be running/negotiated to protocol version 1 chainstate logic, which is an in-scope, reachable code path (`SortitionStateVersion::from_protocol_version`, used as the default fallback on version-consensus failure).

### Recommendation
Update `stacks-signer/src/chainstate/v1.rs::validate_tenure_change_payload` to use `signer_db.get_last_signed_block(&block.header.consensus_hash)` instead of `get_last_globally_accepted_block`, mirroring the v2 fix, so that a locally-accepted (signed) tenure-start block is also recognized as blocking a competing tenure-start proposal in the same tenure.

### Proof of Concept
Conceptual reproduction using the existing v1 test harness (`stacks-signer/src/chainstate/tests/v1.rs`), analogous to the v2 regression test that already demonstrates the underlying condition:
1. Build a `SortitionsView` (v1) with `setup_test_environment`.
2. Insert a `BlockInfo` for tenure-start block A into `signer_db` with `mark_locally_accepted(true)` (simulating that this signer already reached the pre-commit/signature threshold and signed A), but do not call `mark_globally_accepted`.
3. Construct tenure-start block B for the same tenure (same `consensus_hash`), with a `TenureChangePayload` of cause `BlockFound` and a coinbase tx, differing from A only in its transaction set.
4. Call `SortitionsView::check_proposal(..., &block_B, ...)`.
5. Observe that the check incorrectly returns `Ok(())` (or fails on an unrelated check but *not* `RejectReason::DuplicateBlockFound`), because `validate_tenure_change_payload` queries `get_last_globally_accepted_block`, which finds nothing for the merely `LocallyAccepted` block A — unlike the equivalent v2 test `check_tenure_change_rejects_when_locally_accepted_block_exists` at `stacks-signer/src/chainstate/tests/v2.rs:756-850`, which asserts `RejectReason::DuplicateBlockFound` is correctly returned by the fixed v2 code path.

### Citations

**File:** stacks-signer/src/chainstate/v2.rs (L340-357)
```rust
        // We already confirmed in check miner activity that the current tenure is valid. So check we are not
        // reorging the tenure blocks. Only blocks we have signed (locally or globally accepted) count
        // here: a block we have merely pre-committed to carries no signature from us, so it is safe to
        // accept a competing tenure-start block in its place if it failed to reach consensus.
        let last_in_current_tenure = signer_db
            .get_last_signed_block(&block.header.consensus_hash)
            .map_err(|e| {
                SignerChainstateError::from(ClientError::InvalidResponse(e.to_string()))
            })?;
        if let Some(last_in_current_tenure) = last_in_current_tenure {
            warn!(
                "Miner block proposal contains a tenure change, but we've already signed a block in this tenure. Considering proposal invalid.";
                "proposed_block_consensus_hash" => %block.header.consensus_hash,
                "proposed_block_signer_signature_hash" => %block.header.signer_signature_hash(),
                "last_in_tenure_signer_signature_hash" => %last_in_current_tenure.block.header.signer_signature_hash(),
            );
            return Err(RejectReason::DuplicateBlockFound);
        }
```

**File:** stacks-signer/src/chainstate/v1.rs (L505-518)
```rust
        let last_in_current_tenure = signer_db
            .get_last_globally_accepted_block(&block.header.consensus_hash)
            .map_err(|e| {
                SignerChainstateError::from(ClientError::InvalidResponse(e.to_string()))
            })?;
        if let Some(last_in_current_tenure) = last_in_current_tenure {
            warn!(
                "Miner block proposal contains a tenure change, but we've already signed a block in this tenure. Considering proposal invalid.";
                "proposed_block_consensus_hash" => %block.header.consensus_hash,
                "proposed_block_signer_signature_hash" => %block.header.signer_signature_hash(),
                "last_in_tenure_signer_signature_hash" => %last_in_current_tenure.block.header.signer_signature_hash(),
            );
            return Err(RejectReason::DuplicateBlockFound);
        }
```

**File:** stacks-signer/src/chainstate/tests/v2.rs (L843-849)
```rust
    // The proposal should be rejected because there's already a locally-accepted
    // block in this tenure. Before the fix, this would have incorrectly passed
    // because get_last_globally_accepted_block would not find the locally-accepted block.
    assert!(
        matches!(result, Err(RejectReason::DuplicateBlockFound)),
        "Expected DuplicateBlockFound rejection when a locally-accepted block exists in the tenure, got: {result:?}"
    );
```

**File:** stacks-signer/src/chainstate/mod.rs (L532-547)
```rust
impl SortitionStateVersion {
    /// Convert the protocol version to a sortition state version
    pub fn from_protocol_version(version: u64) -> Self {
        if version < GLOBAL_SIGNER_STATE_ACTIVATION_VERSION {
            Self::V1
        } else {
            Self::V2
        }
    }
    /// Uses global state version
    pub fn uses_global_state(&self) -> bool {
        match self {
            Self::V1 => false,
            Self::V2 => true,
        }
    }
```

**File:** stacks-signer/src/v0/signer.rs (L792-806)
```rust
        let local_state_version = SortitionStateVersion::from_protocol_version(local_version);
        self
            .global_state_evaluator
            .determine_latest_supported_signer_protocol_version().map(|version| {
                SortitionStateVersion::from_protocol_version(version)
            })
            .or_else(|| {
                // Don't default if we are in a global consensus activation state as its pointless
                if local_state_version.uses_global_state() {
                    None
                } else {
                    warn!("{self}: No consensus on signer protocol version. Defaulting to local state version: {local_version}.");
                    Some(local_state_version)
                }
            })
```
