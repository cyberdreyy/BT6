### Title
v1 signer's `validate_tenure_change_payload` misses locally-signed-but-not-globally-accepted blocks, allowing a signer to sign two conflicting tenure-start blocks - ([File: stacks-signer/src/chainstate/v1.rs])

### Summary
`SortitionsView::validate_tenure_change_payload` in the v1 chainstate module checks for a duplicate tenure-start block using `signer_db.get_last_globally_accepted_block(&block.header.consensus_hash)` [1](#0-0)  instead of `get_last_signed_block`, which the v2 implementation deliberately uses [2](#0-1) . Because global acceptance requires a quorum of signers, a v1 signer that has only *locally* accepted/signed a first tenure-start block A (but that block hasn't reached global acceptance yet) will see `None` from `get_last_globally_accepted_block` and will not raise `RejectReason::DuplicateBlockFound` when asked to validate a second, conflicting tenure-start block B for the same tenure — letting that signer sign both A and B.

### Finding Description
The claimed equality is: "distinct blocks a signer signs per (tenure, height) == at most one" (UNIQUENESS/no-equivocation guarantee). The v1 code path breaks this equality because its duplicate-block guard is scoped to *globally* accepted blocks only:

```
let last_in_current_tenure = signer_db
    .get_last_globally_accepted_block(&block.header.consensus_hash)
    ...
if let Some(last_in_current_tenure) = last_in_current_tenure {
    ...
    return Err(RejectReason::DuplicateBlockFound);
}
``` [3](#0-2) 

The v2 implementation explicitly acknowledges and fixes this gap, with a comment stating the guard must count "blocks we have signed (locally or globally accepted)" and use `get_last_signed_block`:
```
// We already confirmed in check miner activity that the current tenure is valid. So check we are not
// reorging the tenure blocks. Only blocks we have signed (locally or globally accepted) count
// here: ...
let last_in_current_tenure = signer_db
    .get_last_signed_block(&block.header.consensus_hash)
``` [2](#0-1) 

Exploit flow: a miner (attacker with one slot) submits `BlockProposal(A)` — a valid tenure-start block for tenure X. The v1 signer runs `check_proposal` → `validate_tenure_change_payload`, finds no prior block, and signs A, marking it locally accepted in `SignerDb` (not yet globally accepted, e.g., because other signers haven't yet responded). The attacker then submits `BlockProposal(B)`, a second, conflicting tenure-start block for the same tenure X (e.g., different transactions/parent choice, same height). `check_proposal` calls `validate_tenure_change_payload` again; `get_last_globally_accepted_block(X)` still returns `None` because A never reached global acceptance, so the duplicate check is skipped and the signer signs B too — producing two signatures over conflicting blocks in the same tenure from the same signer.

This applies whenever the victim signer is running the v1 chainstate/protocol implementation (e.g., pinned via `TEST_PIN_SUPPORTED_SIGNER_PROTOCOL_VERSION=1`, or naturally on a network/epoch still using protocol v1, or mixed v1/v2 signer fleets where some signers run v1). No other guard in `check_proposal` (sortition/miner-pkh checks, bitvec, parent-tenure choice, pubkey match) checks for duplicate signing on the same tenure independent of this specific duplicate-block check; those checks validate different properties (miner identity, parent linkage) and do not prevent equivocation across two blocks proposed for the same tenure.

### Impact Explanation
This breaks the UNIQUENESS/no-equivocation safety property for chain safety: a single signer's signature is supposed to be committed to at most one block per tenure-height, since two signed conflicting blocks contribute toward two different forks reaching threshold, directly enabling a chain split/double-signing scenario. This matches the "Critical" impact category: a signer signing two conflicting blocks at the same height/tenure. The bug is repeatable for every tenure where the attacker (winning the miner slot) can race a second tenure-start proposal before global acceptance is reached on a v1 signer.

### Likelihood Explanation
Preconditions: the victim signer must be running the v1 chainstate implementation (a real, currently supported code path in the codebase, not hypothetical — reachable via `TEST_PIN_SUPPORTED_SIGNER_PROTOCOL_VERSION=1` or any deployment/epoch where v1 is active, or mixed-version fleets). The attacker needs only to win a single miner slot (feasible with their own BTC) and race two BlockProposal messages for the same tenure before global acceptance completes — well within the timing window during normal signer round-trip latency. No majority of signers, no privileged role, and no auth_token are required; this is achievable by a single unprivileged miner slot plus normal StackerDB/P2P gossip of proposals, which is in-scope attacker capability. It is repeatable across tenures as long as v1 signers are present.

### Recommendation
In `stacks-signer/src/chainstate/v1.rs`, change `validate_tenure_change_payload` to use `signer_db.get_last_signed_block(&block.header.consensus_hash)` (the same signed-block set v2 uses, covering both locally and globally accepted blocks) instead of `get_last_globally_accepted_block`, so the duplicate-block/no-equivocation check also considers blocks this signer has locally accepted but that have not yet reached global acceptance.

### Proof of Concept
```rust
// stacks-signer/src/chainstate/tests/v1.rs

#[test]
fn duplicate_tenure_start_not_detected_for_locally_accepted_block() {
    let (stacks_client, mut signer_db, block_sk, mut view, mut block) =
        setup_test_environment(function_name!());

    // Block A: first tenure-start block for tenure X, proposed and LOCALLY accepted
    // (signed) by this signer but NOT globally accepted.
    let tenure_x = view.cur_sortition.data.consensus_hash.clone();
    let block_proposal_a = BlockProposal {
        block: /* construct tenure-start NakamotoBlock for tenure_x, height H */,
        burn_height: 2,
        reward_cycle: 1,
        block_proposal_data: BlockProposalData::empty(),
    };
    let mut block_info_a = BlockInfo::from(block_proposal_a);
    block_info_a.mark_locally_accepted(false).unwrap(); // signed, but not globally accepted
    signer_db.insert_block(&block_info_a).unwrap();

    // Sanity: get_last_globally_accepted_block sees nothing (A is only local).
    assert!(signer_db
        .get_last_globally_accepted_block(&tenure_x)
        .unwrap()
        .is_none());

    // Block B: a second, conflicting tenure-start block for the SAME tenure X.
    let mut block_b = block.clone();
    block_b.header.consensus_hash = tenure_x.clone();
    // ... attach a TenureChangePayload for tenure_x with same prev_tenure, different content ...
    block_b.header.sign_miner(&block_sk).unwrap();

    // BUG: validate_tenure_change_payload wrongly succeeds because it only
    // checks get_last_globally_accepted_block, not the locally-signed block A.
    let result = view.check_proposal(
        &stacks_client,
        &mut signer_db,
        &block_b,
        false,
        ReplayTransactionSet::none(),
    );

    // Expected (fixed) behavior: Err(RejectReason::DuplicateBlockFound)
    // Actual (buggy) behavior: Ok(()) -- signer would sign a second conflicting block.
    assert!(
        matches!(result, Err(RejectReason::DuplicateBlockFound)),
        "v1 signer should reject a second tenure-start block when a locally-signed \
         (not yet globally accepted) block already exists for this tenure, but got {:?}",
        result
    );
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
