### Title
V1 `validate_tenure_change_payload` uses `get_last_globally_accepted_block` instead of `get_last_signed_block`, permitting a duplicate tenure-start block to be signed twice during a mixed-protocol-version signer-set upgrade window - (File: `stacks-signer/src/chainstate/v1.rs`)

### Summary
`stacks-signer/src/chainstate/v1.rs`'s `validate_tenure_change_payload` guards against re-signing a tenure-start block by calling `signer_db.get_last_globally_accepted_block(...)`, whereas the v2 equivalent in `stacks-signer/src/chainstate/v2.rs` deliberately uses `signer_db.get_last_signed_block(...)`, which also matches locally-accepted blocks. A signer still running the v1 checker will not detect a duplicate tenure-start proposal if its own prior block in that tenure is only locally accepted (not yet globally accepted), letting it sign a second, conflicting tenure-start block.

### Finding Description
The invariant that must hold is: for a given tenure (consensus hash), a signer must sign at most one tenure-start block, regardless of whether that prior signature reached global acceptance. `stacks-signer/src/chainstate/v2.rs::validate_tenure_change_payload` (lines 344-357) enforces this correctly: [1](#0-0) 

using `get_last_signed_block`, which the code comment explicitly documents as covering "blocks we have signed (locally or globally accepted)". [2](#0-1) 

The v1 checker, `stacks-signer/src/chainstate/v1.rs::validate_tenure_change_payload` (lines 461-520), instead calls `get_last_globally_accepted_block`: [3](#0-2) 

This function only returns a block once it has crossed the global-acceptance threshold; a block the signer has locally accepted (and thus already signed a `BlockResponse` for) but that has not yet gathered enough signatures is invisible to this query. Consequently, if the same tenure already contains a locally-accepted-but-not-globally-accepted block from an earlier proposal, a v1-checking signer's `DuplicateBlockFound` guard silently passes, and `check_proposal` returns `Ok(())` for a second, competing tenure-start block. The regression test `check_tenure_change_rejects_when_locally_accepted_block_exists` (`stacks-signer/src/chainstate/tests/v2.rs` lines 748-850) documents that this exact defect ("previously, the check used `get_last_globally_accepted_block`, which would miss blocks in `LocallyAccepted` or `PreCommitted` state") was identified and fixed specifically in the v2 path, but the fix was not mirrored into `v1.rs`: [4](#0-3) 

An attacker holding a single miner slot needs no coordination with signers beyond ordinary network timing: they propose a first tenure-start block, and due to normal propagation/timeout variance in a live network some signers reach local acceptance while global acceptance has not yet been reached network-wide (this window is inherent to threshold aggregation and is not something the attacker must engineer beyond timing their second proposal quickly). They then immediately propose a second, different tenure-start block for the same tenure. Any signer still evaluating proposals via the v1 code path (which occurs whenever `active_signer_protocol_version` puts that signer on the v1 chainstate machinery — a state explicitly permitted to coexist with v2 peers during rolling upgrades) will find no globally-accepted block, pass `check_proposal`, and sign the conflicting second block, producing an equivocating signature over two distinct tenure-start blocks in the same tenure by the same signer.

### Impact Explanation
This breaks the "at most one signed block per tenure at the tenure-start height" safety property — a signer ends up producing a valid signature over two conflicting/competing blocks in one tenure, i.e., chain-safety equivocation. This matches the Critical category: "a signer signing an invalid, non-canonical, or conflicting block." Because it hinges on the signer's own local signerdb state and code path rather than on the other signers' votes, it is repeatable by any miner-slot holder against any signer currently running the v1 checker, each time a locally-accepted (but not yet globally-accepted) tenure-start block exists.

### Likelihood Explanation
Preconditions: (1) a signer set that includes at least one signer whose `active_signer_protocol_version` still routes through `stacks-signer/src/chainstate/v1.rs` (explicitly permitted during upgrade windows per the affected signer-state-machine version metadata), and (2) at least one locally-accepted-but-not-globally-accepted block already recorded for the target tenure — a normal, frequent occurrence under real network latency/threshold-aggregation timing, not an artificial precondition. The attacker needs only their own miner slot plus the ability to gossip two competing `BlockProposal`s for the same tenure, well within the stated unprivileged capability (no majority of signers, no auth token, no signer-key or host access required). The attack is straightforward to repeat across tenures where the victim signer is on v1.

### Recommendation
Change `stacks-signer/src/chainstate/v1.rs::validate_tenure_change_payload` to call `signer_db.get_last_signed_block(&block.header.consensus_hash)` instead of `get_last_globally_accepted_block`, mirroring the v2 fix, so the duplicate check counts any block the signer has already signed (locally or globally accepted), not only globally-accepted ones.

### Proof of Concept
Add a v1 analogue of the existing v2 regression test in `stacks-signer/src/chainstate/tests/v1.rs` (or wherever v1 unit tests live):
1. Build a `SortitionsView`/`SortitionState` fixture equivalent to `setup_test_environment` in `stacks-signer/src/chainstate/tests/v2.rs`.
2. Insert a `BlockInfo` for the tenure's consensus hash marked `mark_locally_accepted(false)` only (not globally accepted) via `signer_db.insert_block`.
3. Construct a second, different tenure-start `NakamotoBlock` (tenure-change + coinbase) for the same consensus hash, signed by the miner key.
4. Call `SortitionsView::check_proposal` (v1) on this second block.
5. Assert (to demonstrate the bug): `result` is `Ok(())` rather than `Err(RejectReason::DuplicateBlockFound)`, contrasting directly with the v2 test `check_tenure_change_rejects_when_locally_accepted_block_exists`, which asserts `Err(RejectReason::DuplicateBlockFound)` for the identical scenario under `GlobalStateView::check_proposal` (v2). The differing outcome for the same signerdb state, keyed solely on which checker ran, proves the version-skew equivocation window.

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

**File:** stacks-signer/src/chainstate/tests/v2.rs (L748-756)
```rust
/// Test that a tenure change proposal is rejected when a locally-accepted
/// (but not globally-accepted) block already exists in the same tenure.
///
/// This is a regression test: previously, the check used
/// `get_last_globally_accepted_block`, which would miss blocks in
/// `LocallyAccepted` or `PreCommitted` state and incorrectly allow
/// a duplicate tenure change.
#[test]
fn check_tenure_change_rejects_when_locally_accepted_block_exists() {
```
