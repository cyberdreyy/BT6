### Title
v1 Signer's `validate_tenure_change_payload` Duplicate-Block Check Ignores Locally-Accepted Blocks, Allowing Signer Equivocation Within a Tenure - ([File: stacks-signer/src/chainstate/v1.rs])

### Summary
`SortitionsView::validate_tenure_change_payload` in the v1 signer chainstate module only rejects a competing tenure-start block if the signer's local `SignerDb` already holds a **globally accepted** block for that tenure, via `signer_db.get_last_globally_accepted_block(&block.header.consensus_hash)`. The v2 chainstate module (`GlobalStateView::validate_tenure_change_payload`) closed this exact gap by switching to `signer_db.get_last_signed_block`, which also covers **locally accepted (signed but not yet globally accepted)** blocks, explicitly to prevent re-signing a second, conflicting tenure-start block. Any v1-configured signer therefore can be tricked by the tenure's own miner into signing two different, conflicting tenure-change blocks for the same tenure.

### Finding Description
The equality that must hold for the uniqueness safety property is:
`{blocks the signer has already signed in tenure T} == {blocks the duplicate-check consults}`.

In v1 (`stacks-signer/src/chainstate/v1.rs`, `validate_tenure_change_payload`, lines 505-518), the duplicate-check set is restricted to blocks marked *globally accepted*: [1](#0-0) 

In v2 (`stacks-signer/src/chainstate/v2.rs`, `validate_tenure_change_payload`, lines 340-357), the equivalent check explicitly broadens the set to include locally-accepted blocks, with an accompanying comment stating the intent is to prevent re-signing a competing tenure-start block once *any* signature (local or global) has been produced by this signer for that tenure: [2](#0-1) 

Root cause: a signer's own act of signing a block first marks it "locally accepted" in `SignerDb`; it only becomes "globally accepted" once the signer later learns (via gossip/StackerDB or node observation) that enough other signers also signed it. Between those two points there is a window where `get_last_globally_accepted_block` returns `None` even though this exact signer has already committed a signature to a block in tenure T.

Exploit flow (attacker = the current tenure's winning miner, one slot + gossip only):
1. Miner proposes tenure-start block X for tenure T (contains a `TenureChangePayload`) and gossips it to the signer set.
2. Signer S (running v1 chainstate logic) validates X, passes all checks including the duplicate-check (no prior block exists), and signs X. `SignerDb` now marks X as locally accepted for S, but S has not yet observed that a global threshold of signers also signed X (e.g., because the miner deliberately gossips slowly/selectively, or a network partition/timing gap exists before X's aggregate signature is observed).
3. Before S learns of global acceptance of X, the miner crafts a second, different block Y also containing a `TenureChangePayload` referencing the same `prev_tenure_consensus_hash`/parent tenure, and gossips it as a competing proposal for the same tenure T (same `consensus_hash`).
4. S's `validate_tenure_change_payload` calls `get_last_globally_accepted_block(&Y.header.consensus_hash)`, which returns `None` (X is still only locally accepted), so the duplicate-check passes and S proceeds to sign Y.
5. S has now produced signatures on two conflicting blocks (X and Y) for the same tenure T — an equivocation that v2's broader check (`get_last_signed_block`) would have caught and rejected with `DuplicateBlockFound`.

Existing guards that fail to prevent this: `check_tenure_change_confirms_parent` and `check_parent_tenure_choice` validate the *parent* tenure relationship, not intra-tenure duplication; they do not detect that the signer already signed a different block within the same tenure. The only mechanism meant to catch this — the duplicate-block check — is the one with the narrowed, stale view in v1.

### Impact Explanation
This breaks the uniqueness safety property ("at most one signed block per tenure per signer"), which is foundational to chain safety: if enough v1-configured signers are driven through the same sequence (each locally signs X, then is later fed Y before global acceptance of X propagates to them), both X and Y could independently accumulate signer signatures toward the aggregate threshold, producing two valid-looking, conflicting tenure-start blocks for the same tenure. That is a signer-side equivocation directly enabling a conflicting/non-canonical block to be finalized — a Critical chain-safety violation. It is repeatable once per tenure the attacker mines, and requires no privileged role, only the attacker's own miner slot and normal gossip capability.

### Likelihood Explanation
Preconditions: the target signer(s) must be running the v1 chainstate logic (i.e., not yet upgraded to the v2 signer-state-machine protocol) and must be in the narrow window where they have locally signed a block but have not yet observed its global acceptance — a timing condition entirely controllable by the attacker/miner, since they control gossip ordering/timing of their own proposals. The attacker cost is exactly one won miner slot plus the ability to gossip two block proposals, matching the allowed attacker capability. This is feasible and repeatable across any tenure the attacker wins while any signers remain on v1.

### Recommendation
Change `stacks-signer/src/chainstate/v1.rs`'s `validate_tenure_change_payload` to use the same broadened lookup as v2: replace `signer_db.get_last_globally_accepted_block(&block.header.consensus_hash)` with `signer_db.get_last_signed_block(&block.header.consensus_hash)` (or equivalent), so that any block this signer has already signed — locally or globally accepted — blocks a competing tenure-start proposal in the same tenure.

### Proof of Concept
In `stacks-signer/src/chainstate/tests/v1.rs`, add a test that:
1. Constructs a `SignerDb` and inserts a block `X` for tenure `T` marked as locally accepted (signed) but not globally accepted (e.g. via the same helper used for `has_signed_block_in_tenure`/local-accept state, mirroring the setup pattern in `v2.rs` tests).
2. Builds a second block `Y` for the same tenure `T` with a `TenureChangePayload` whose `prev_tenure_consensus_hash` matches the expected parent (so all other checks pass).
3. Calls `SortitionsView::validate_tenure_change_payload` (v1) with `Y`.
4. Asserts the current (buggy) behavior returns `Ok(())` instead of `Err(RejectReason::DuplicateBlockFound)`, demonstrating the gap; after applying the fix (switching to `get_last_signed_block`), the same test should assert `Err(RejectReason::DuplicateBlockFound)`.

### Citations

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
