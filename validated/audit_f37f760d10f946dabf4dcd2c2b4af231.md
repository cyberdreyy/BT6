### Title
v1 chainstate's tenure-change duplicate check only looks at globally-accepted blocks, allowing a second signature on a locally-accepted-only tenure - (File: stacks-signer/src/chainstate/v1.rs)

### Summary
`SortitionsView::validate_tenure_change_payload` in the v1 chainstate module queries `signer_db.get_last_globally_accepted_block` to detect whether the signer has already signed a block for the tenure, whereas the v2 equivalent (`GlobalStateView::validate_tenure_change_payload`) deliberately queries the broader `get_last_signed_block`. Because v1 only checks global acceptance, a locally-accepted (but sub-threshold) block that this signer already signed for a tenure is invisible to the duplicate check, allowing a second, conflicting tenure-start block for the same `consensus_hash` to pass validation and be signed.

### Finding Description
`validate_tenure_change_payload` in v1 does:
```
let last_in_current_tenure = signer_db
    .get_last_globally_accepted_block(&block.header.consensus_hash)?;
if let Some(last_in_current_tenure) = last_in_current_tenure {
    ... return Err(RejectReason::DuplicateBlockFound);
}
``` [1](#0-0) 

Compare to v2, which explicitly documents why it checks a broader set:
```
// Only blocks we have signed (locally or globally accepted) count
// here: a block we have merely pre-committed to carries no signature from us, so it is safe to
// accept a competing tenure-start block in its place if it failed to reach consensus.
let last_in_current_tenure = signer_db
    .get_last_signed_block(&block.header.consensus_hash)?;
``` [2](#0-1) 

The comment on v2 makes the intended invariant explicit: any block the signer has actually *signed* (LocallyAccepted or GloballyAccepted) for a tenure must block a second, competing tenure-start signature — only pre-committed (unsigned) blocks are exempt. v1 does not implement this invariant: it only excludes a second tenure-change block if the first block already reached *global* acceptance. If the attacker (an unprivileged miner-slot holder) submits `block_B` with the same `consensus_hash` as `block_A` (which this v1 signer already `LocallyAccepted`-signed) before `block_A` reaches the network's global-acceptance threshold, `get_last_globally_accepted_block` returns `None`, the duplicate check passes, and the rest of `validate_tenure_change_payload`/`check_proposal` contains no other place that checks for a locally-signed conflicting block in the same tenure. The signer then signs `block_B`, producing two signatures from the same signer for two conflicting tenure-start blocks at the same consensus hash — a UNIQUENESS violation.

### Impact Explanation
This breaks the uniqueness/non-equivocation safety property for signers still running v1 chainstate rules: the same signer signs two mutually exclusive blocks for one tenure. If this happens across enough signers (each independently vulnerable under the same race), it can produce two blocks each with partial-but-conflicting signature sets, potentially enabling a fork/equivocation at the tenure-start boundary — matching the Critical category ("a signer signing an invalid, non-canonical, or conflicting block"). This is repeatable per-tenure whenever the precondition (signer's own block sub-threshold) recurs.

### Likelihood Explanation
Preconditions: the signer must be operating under v1 chainstate rules, must have already `LocallyAccepted`/signed a tenure-start block (`block_A`), and that block must not yet have reached global acceptance threshold. The attacker needs no privileged role — a single miner slot is enough to craft a second tenure-change block proposal (`block_B`) with the same `consensus_hash` and gossip it as a `BlockProposal`, matching the allowed unprivileged attacker capabilities. Feasibility depends on real-world timing (whether other signers are slow to reach global threshold), but it does not require majority signer control, compromised keys, or the auth_token — only gossip and single-slot mining, so it is within scope and repeatable across tenures.

### Recommendation
Change v1's `validate_tenure_change_payload` to use the same broader check as v2, i.e., replace `signer_db.get_last_globally_accepted_block(&block.header.consensus_hash)` with `signer_db.get_last_signed_block(&block.header.consensus_hash)` (or equivalent logic covering both `LocallyAccepted` and `GloballyAccepted` states), so a locally-signed-but-sub-threshold block in the current tenure is also treated as a duplicate and blocks signing of a second tenure-start block for that tenure.

### Proof of Concept
```rust
// stacks-signer/src/chainstate/tests/v1.rs
// Mirrors check_tenure_change_rejects_when_locally_accepted_block_exists (v2) but targets v1::SortitionsView.

#[test]
fn v1_tenure_change_allows_duplicate_when_only_locally_accepted() {
    // 1. Build a SignerDb, insert block_A for tenure consensus_hash CH,
    //    with BlockInfo state = BlockState::LocallyAccepted (signed, sub-threshold).
    //    Do NOT insert/mark it as GloballyAccepted.
    //
    // 2. Construct block_B: a NakamotoBlock with a TenureChangePayload whose
    //    consensus_hash == CH (same tenure), differing content/parent choice from block_A,
    //    but otherwise valid (correct prev_tenure_consensus_hash, correct miner pubkey, etc.).
    //
    // 3. Call v1::SortitionsView::validate_tenure_change_payload(...) with signer_db containing
    //    only the LocallyAccepted block_A.
    //
    // 4. Assert:
    assert!(
        result.is_ok(),
        "BUG: v1 validate_tenure_change_payload did not reject a duplicate tenure-change \
         block when only a locally-accepted (sub-threshold) block exists for the tenure; \
         get_last_globally_accepted_block returned None, missing the local-only signature."
    );

    // 5. For contrast, run the same scenario through v2::GlobalStateView::validate_tenure_change_payload
    //    (which uses get_last_signed_block) and assert it correctly returns
    //    Err(RejectReason::DuplicateBlockFound), demonstrating the v1/v2 semantic gap explicitly.
}
```

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

**File:** stacks-signer/src/chainstate/v2.rs (L340-348)
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
```
