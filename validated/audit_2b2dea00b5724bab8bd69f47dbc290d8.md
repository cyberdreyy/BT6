### Title
v1 `validate_tenure_change_payload` duplicate-block guard checks only globally-accepted blocks, allowing a signer to sign two conflicting tenure-start blocks in the same tenure - (File: `stacks-signer/src/chainstate/v1.rs`)

### Summary
`SortitionsView::validate_tenure_change_payload` in v1.rs rejects a second tenure-change block for a tenure only if `signer_db.get_last_globally_accepted_block` finds a prior block, whereas the v2.rs equivalent explicitly uses `get_last_signed_block`, which the code comments state is required so that "locally or globally accepted" blocks count. This means a v1 signer that has locally accepted (signed) a tenure-start block which never reached global acceptance will not see it via `get_last_globally_accepted_block`, and can be induced to sign a second, competing tenure-start block for the same tenure.

### Finding Description
The intended equality (as documented in the v2.rs code comment) is: "a block we have already signed in this tenure" must include any block this signer has locally signed, not merely a block that reached the globally-accepted threshold - otherwise the uniqueness property (at most one signed tenure-start block per signer per tenure) is broken.

In `stacks-signer/src/chainstate/v1.rs`, `validate_tenure_change_payload` (lines 461-520) performs the duplicate check as: [1](#0-0) 
This calls `signer_db.get_last_globally_accepted_block(&block.header.consensus_hash)`, which only returns a block once it has reached global (majority) acceptance in signerdb - not merely a block this signer itself locally signed and accepted.

By contrast, `stacks-signer/src/chainstate/v2.rs`'s `validate_tenure_change_payload` (lines 306-359) explicitly uses `get_last_signed_block`: [2](#0-1) 
with an explicit comment stating: "Only blocks we have signed (locally or globally accepted) count here." This confirms the correct semantics were understood in v2 but the older v1 code path retains the weaker, incorrect check.

Exploit flow: A locally-accepted-but-not-globally-accepted block B1 exists in `signer_db` for `tenure_id` (e.g., because the miner that proposed B1 never gathered enough signatures from other signers, or gossip of B1's acceptance to the rest of the signer set was incomplete/delayed). An attacker who wins a subsequent miner slot (via their own BTC block-commit) crafts a second tenure-change `BlockProposal` B2 for the same `tenure_id`, sets `prev_tenure_consensus_hash` correctly, and gossips it to the victim's StackerDB slot. When the victim v1 signer runs `check_proposal` -> `validate_tenure_change_payload`, `get_last_globally_accepted_block(tenure_id)` returns `None` (since B1 never reached global acceptance), so the `DuplicateBlockFound` guard is skipped and B2 passes on to further checks/signing.

This directly breaks the UNIQUENESS property that a signer's tenure-start signature must be unique per tenure, which the v2 implementation and comment explicitly protect against.

### Impact Explanation
If B2 also passes the remaining validation (parent tenure choice, confirms-expected-parent, etc.) and is signed, this signer produces two valid signatures for two different, conflicting tenure-start blocks (B1, B2) in the same tenure. If different subsets of the signer set collect signatures for B1 vs B2, both could independently reach threshold and finalize competing chains, i.e., a chain-safety violation (equivocation/double-signing by a single signer contributing to two different finalized forks). This matches the Critical category: "a signer signing an invalid, non-canonical, or conflicting block (chain safety)."

### Likelihood Explanation
Preconditions: the victim signer must be running v1 semantics (i.e., a pre-signer-set-rotation / earlier protocol epoch) and must have a locally-accepted-but-not-globally-accepted block B1 in its signerdb for the target tenure - a state that is expected and reachable whenever consensus stalls on a tenure-start block (e.g., a minority of signers accepted it locally before communication/gossip completed, or the required signature threshold was never reached). The attacker only needs one miner slot (their own BTC funds) to produce a valid second sortition/tenure-change block commit, plus gossip capability to deliver a `BlockProposal` to the victim's StackerDB - both within the stated unprivileged attacker capabilities. No majority of signers, no compromised keys, and no auth_token are required. This is repeatable across tenures where the same local-acceptance-without-global-acceptance condition recurs.

### Recommendation
In `stacks-signer/src/chainstate/v1.rs`, change the duplicate-block check in `validate_tenure_change_payload` from `signer_db.get_last_globally_accepted_block(&block.header.consensus_hash)` to `signer_db.get_last_signed_block(&block.header.consensus_hash)`, mirroring the fix already present in v2.rs, so that any block this signer has locally or globally accepted (signed) is treated as "already signed in this tenure" and blocks a second tenure-start proposal.

### Proof of Concept
Rust test plan (to be placed alongside existing tests, e.g. in `stacks-signer/src/chainstate/tests/`):
1. Construct a `SignerDb` (v1) and insert a `BlockInfo` for tenure `tenure_id` with state `Locally accepted` (i.e., signed by this signer) but not marked globally accepted — matching whatever `get_last_signed_block` recognizes as "signed" per `signerdb.rs`.
2. Build a `SortitionsView` (v1) with `cur_sortition` pointing at `tenure_id`'s sortition data (matching `parent_tenure_id`, `miner_pkh`, etc.) so that all prior checks in `validate_tenure_change_payload` pass.
3. Construct a second `NakamotoBlock` B2 with a `TenureChangePayload` for the same `tenure_id` (`prev_tenure_consensus_hash` correctly set, confirming the expected parent).
4. Call `SortitionsView::validate_tenure_change_payload(...)` (or the outer `check_proposal`) on B2.
5. Assert (equality check before fix): the call incorrectly returns `Ok(())` because `get_last_globally_accepted_block` returns `None` for the locally-signed-only B1.
6. Assert (equality check after fix, using `get_last_signed_block` instead): the call returns `Err(RejectReason::DuplicateBlockFound)`, matching the v2.rs behavior and comment semantics at [3](#0-2) .

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
