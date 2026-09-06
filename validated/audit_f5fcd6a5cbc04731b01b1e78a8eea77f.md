### Title
V1 chainstate duplicate-block guard checks only globally-accepted blocks, missing locally-signed blocks in the same tenure - ([File: stacks-signer/src/chainstate/v1.rs])

### Summary
`SortitionsView::validate_tenure_change_payload` in the v1 chainstate path only queries `signer_db.get_last_globally_accepted_block` to detect whether the signer already signed a block in the current tenure before accepting a new tenure-change block. Since a locally-accepted (signed) block that never reached global acceptance is invisible to this check, a signer can be induced to approve and sign a second, conflicting tenure-change block for the same tenure.

### Finding Description
The uniqueness invariant that should hold is: for a given tenure (`consensus_hash`), a signer must sign at most one block. In `validate_tenure_change_payload` [1](#0-0)  the duplicate check is:

```
let last_in_current_tenure = signer_db
    .get_last_globally_accepted_block(&block.header.consensus_hash)?;
if let Some(last_in_current_tenure) = last_in_current_tenure {
    ...
    return Err(RejectReason::DuplicateBlockFound);
}
```

This only detects a prior block once it has reached *global* acceptance (i.e., a threshold of signers signed it and the node confirmed it). It does not consult any locally-signed-but-not-yet-globally-accepted block info in `signer_db`. Compare this to the v2 chainstate path, `GlobalStateView::validate_tenure_change_payload`, which was fixed to use `signer_db.get_last_signed_block` instead, with an explicit comment: "Only blocks we have signed (locally or globally accepted) count here" [2](#0-1) .

Exploit flow for the v1 path:
1. Attacker wins a miner slot for tenure T and proposes a tenure-change block B1 targeting T.
2. The victim signer validates and signs B1 (recorded in `signer_db` as locally accepted / `signed_self`), but B1 never accumulates enough signatures from other signers to become globally accepted (attacker only controls one signer's weight, so this is achievable simply by not gossiping/aggregating further, or by the natural asynchrony of the network — no majority collusion required to *cause* the gap, only to exploit it while it exists).
3. Attacker (or same miner slot) then proposes a second tenure-change block B2, also targeting the same tenure T's consensus hash, with a different transaction set/parent linkage.
4. The victim signer's `check_proposal` calls `validate_tenure_change_payload` for B2. `get_last_globally_accepted_block(T)` returns `None` because B1 was never globalized, so the duplicate check is bypassed and returns `Ok(())`.
5. The signer proceeds to sign B2, now having signed two conflicting blocks in the same tenure.

This defeats the equivocation guard for the v1 chainstate signers specifically in the window where a signature exists but has not yet reached global-acceptance status — a state that is expected and routine (partial signature collection is inherently asynchronous).

### Impact Explanation
This breaks the uniqueness/equivocation safety property that the signer's protocol depends on to guarantee that a signer never signs two conflicting blocks for one tenure. If enough signers of the same v1-cohort are induced into this state, two conflicting tenure-change blocks can both accumulate signatures, causing chain safety loss (fork/equivocation) — matching the "Critical: a signer signing an invalid, non-canonical, or conflicting block (chain safety)" category. It is repeatable per-tenure-change opportunity since the race window (locally-signed-but-not-globally-accepted) recurs whenever a block hasn't yet reached global acceptance.

### Likelihood Explanation
Preconditions are readily achievable by a single unprivileged miner-slot holder: win a tenure slot, get a tenure-change block signed by the victim signer, and propose a competing tenure-change block before the first reaches global acceptance. This does not require majority signer collusion, compromised keys, or local access — it only needs the attacker's one miner slot plus normal gossip of the two competing `BlockProposal` messages, exactly matching the allowed attacker capability model. It is feasible any time not-yet-globalized state exists, which is a normal and common transient signer-db state, not a rare edge case.

### Recommendation
In `stacks-signer/src/chainstate/v1.rs`'s `validate_tenure_change_payload`, replace the call to `signer_db.get_last_globally_accepted_block` with `signer_db.get_last_signed_block` (or equivalent function that also captures locally-accepted/`signed_self` blocks), mirroring the fix already present in the v2 chainstate implementation at `stacks-signer/src/chainstate/v2.rs` lines 344-348.

### Proof of Concept
```rust
// stacks-signer/src/chainstate/tests/v1.rs (new test)
#[test]
fn v1_duplicate_block_guard_misses_locally_accepted_block() {
    // 1. Build a SortitionsView (v1) with cur_sortition for tenure T.
    // 2. Insert into signer_db a BlockInfo for tenure T that is locally
    //    accepted (signed_self = true / vote recorded) but NOT globally accepted:
    //    signer_db.insert_block(&block_info_signed_but_not_global)?;
    //    assert!(signer_db.get_last_globally_accepted_block(&T)?.is_none());
    //    assert!(signer_db.get_last_signed_block(&T)?.is_some()); // exists in v2 helper

    // 3. Construct a second tenure-change NakamotoBlock B2 for the same
    //    consensus_hash T, with prev_tenure_consensus_hash matching parent tenure,
    //    and a parent block that "confirms_expected_parent" per check_tenure_change_confirms_parent.

    // 4. Call view.validate_tenure_change_payload(&proposed_by, &tenure_change, &B2, &mut signer_db, &client)

    // ASSERTION demonstrating the bug (current v1 behavior):
    assert!(result.is_ok()); // BUG: should be Err(RejectReason::DuplicateBlockFound)

    // Desired behavior after fix (using get_last_signed_block):
    // assert_eq!(result, Err(RejectReason::DuplicateBlockFound));
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
