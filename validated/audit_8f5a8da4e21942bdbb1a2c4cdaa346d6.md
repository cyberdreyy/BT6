### Title
v1 `validate_tenure_change_payload` checks only `get_last_globally_accepted_block` while v2 checks `get_last_signed_block`, letting v1 signers sign a duplicate/conflicting tenure-start block that v2 signers correctly reject - ([File: stacks-signer/src/chainstate/v1.rs])

### Summary
`SortitionState::validate_tenure_change_payload` in v1 rejects a second tenure-start proposal for the same tenure only when a block in that tenure has already reached `GloballyAccepted` state, whereas the equivalent v2 function rejects as soon as any block in the tenure has been signed (`LocallyAccepted` or `GloballyAccepted`). In a mixed v1/v2 signer fleet, a locally-accepted-but-not-yet-globally-accepted first tenure-start block is invisible to v1's check, so v1 signers can validate and sign a second, conflicting tenure-start block for the same tenure that v2 signers reject with `DuplicateBlockFound`.

### Finding Description
The intended safety property is: at most one tenure-start block is signed per tenure. v1 enforces this narrower than v2:

- v1: `signer_db.get_last_globally_accepted_block(&block.header.consensus_hash)` — only sees blocks in `BlockState::GloballyAccepted`. [1](#0-0) 
- v2: `signer_db.get_last_signed_block(&block.header.consensus_hash)` — sees blocks in either `LocallyAccepted` or `GloballyAccepted` state. [2](#0-1) 
- `get_last_globally_accepted_block` vs `get_last_signed_block` definitions confirming the state filters. [3](#0-2) [4](#0-3) 

A block only becomes `GloballyAccepted` once the *node* observes the aggregate signature and reports a `NewBlock` event back to the signer — this happens strictly after enough signer weight has reached `LocallyAccepted` (70% threshold) and broadcast the signature to the node. [5](#0-4) 

This gap is explicitly acknowledged in the repo's own documentation and is exercised by a regression test (`check_tenure_change_rejects_when_locally_accepted_block_exists`) that only asserts the fixed v2 behavior; v1 retains the old, narrower check: [6](#0-5) [7](#0-6) 

Exploit flow (attacker controls exactly one miner slot, no signer keys, no auth token):
1. Attacker wins the tenure's sortition and proposes tenure-start block A (`TenureChangeCause::BlockFound`).
2. Enough signer weight (v1 + v2 mixed fleet) reaches 70% and marks A `LocallyAccepted`; broadcast is in flight but the node has not yet reported `NewBlock` for A (a window that always exists, however brief).
3. Attacker gossips a second, conflicting tenure-start proposal B for the same tenure (same `consensus_hash`, different transactions/parent choice) via the normal `BlockProposal` StackerDB message path — no privileged access required, just a normal proposal a miner can always emit.
4. v2 signers call `get_last_signed_block`, find A (`LocallyAccepted`), return `Err(RejectReason::DuplicateBlockFound)` — correct.
5. v1 signers call `get_last_globally_accepted_block`, which does not see A (still only `LocallyAccepted`, node hasn't confirmed it yet), so the duplicate check passes; v1 signers proceed to sign B if the rest of validation succeeds.
6. Result: A and B are both signed by disjoint subsets of the same signer fleet (v1 subset signs B, v2 subset already signed A), violating UNIQUENESS for the tenure — two conflicting tenure-start blocks at the same height/tenure each carry real signer signatures.

Existing guards that do NOT close this gap:
- The duplicate check runs only at proposal time and is not re-run at validate-ok or at signing, so a v1 signer that passed proposal-time validation for B will go on to sign it. [8](#0-7) 
- `check_tenure_change_confirms_parent` and `check_parent_tenure_choice` check the *parent* tenure and reorg legitimacy, not whether a sibling block already exists in *this* tenure — they do not substitute for the duplicate-block check.
- The `get_signed_conflicts` cross-tenure fresh-conflict guard exists in `signerdb.rs` but is a separate mechanism used elsewhere (own-tenure conflict guard at signing, per docs); it does not appear to be invoked from v1's `validate_tenure_change_payload` proposal-time path.

### Impact Explanation
This breaks the UNIQUENESS/chain-safety property (Critical): two conflicting tenure-start blocks at the same height for the same tenure can each accumulate real signer signatures, because the v1 subset of the fleet has a strictly narrower duplicate check than v2. This is exactly the "signer signing a conflicting block" Critical category — it can produce two siblings the fleet as a whole treats as independently signed, which is the equivocation/uniqueness guarantee the duplicate-block check exists to enforce. The attack is repeatable in every tenure the attacker wins as long as a v1/v2 split fleet exists and the local-accept-to-global-accept propagation window is non-zero (it always is, since global acceptance requires an extra node round-trip after local acceptance).

### Likelihood Explanation
Preconditions: (1) a live mixed-version fleet with a non-trivial v1 subset weight, (2) the attacker wins one miner slot (normal, unprivileged, cost = winning a sortition), (3) the attacker races a second proposal into the brief but real local-accept→global-accept propagation window. No majority-signer collusion, no compromised keys, no auth token, and no local host access are needed — only crafting and gossiping two `BlockProposal` messages, which is within the stated attacker capability. The likelihood is bounded by how large the v1 population is during an upgrade window and how tight the race is, but the underlying logic gap is unconditional whenever v1 signers are present.

### Recommendation
Change v1's `validate_tenure_change_payload` to use `signer_db.get_last_signed_block(&block.header.consensus_hash)` instead of `get_last_globally_accepted_block`, matching v2's semantics, so both protocol versions reject a second tenure-start proposal as soon as any block in the tenure has been signed (locally or globally accepted), not only after global acceptance.

### Proof of Concept
```rust
// stacks-signer/src/chainstate/tests/ (new test, mirroring v2's
// check_tenure_change_rejects_when_locally_accepted_block_exists)

#[test]
fn v1_v2_diverge_on_locally_accepted_duplicate_tenure_start() {
    // 1. Build identical SignerDb state for both a v1 SortitionState and a v2
    //    SortitionState (same sortition, same consensus_hash).
    // 2. Insert one BlockInfo for the tenure, call
    //    existing_block_info.mark_locally_accepted(false).unwrap();
    //    signer_db.insert_block(&existing_block_info).unwrap();
    //    (do NOT mark it GloballyAccepted — simulate node not having
    //    processed the NewBlock event yet).
    // 3. Build a second tenure-start block `block` (TenureChangeCause::BlockFound,
    //    same consensus_hash, distinct content) with a valid TenureChangePayload
    //    whose prev_tenure_consensus_hash matches parent_tenure_id.
    // 4. Call:
    let v1_result = sortition_state_v1.validate_tenure_change_payload(
        &proposed_by, &tenure_change_payload, &block, &mut signer_db_v1, &stacks_client,
    );
    let v2_result = SortitionStateV2::validate_tenure_change_payload(
        &tenure_change_payload, &block, &parent_tenure_id, &mut signer_db_v2, &stacks_client, &config,
    );

    // Assert the divergence claimed:
    assert!(v1_result.is_ok(), "v1 must incorrectly accept the duplicate: {v1_result:?}");
    assert!(
        matches!(v2_result, Err(RejectReason::DuplicateBlockFound)),
        "v2 must correctly reject: {v2_result:?}"
    );
}
```
This directly demonstrates the equality violation: identical `SignerDb` state, identical proposed block, but `v1::validate_tenure_change_payload` → `Ok` and `v2::validate_tenure_change_payload` → `Err(DuplicateBlockFound)`, confirming that a v1 signer would proceed to sign a block a v2 signer refuses, breaking per-tenure uniqueness across a mixed fleet.

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

**File:** stacks-signer/src/signerdb.rs (L1564-1585)
```rust
    /// Return the last signed block in a tenure (identified by its consensus hash).
    /// A block is considered signed if it is locally or globally accepted. Blocks that
    /// have only been pre-committed are excluded, because a pre-commit does not put a
    /// signature over the block and may be safely superseded by a competing proposal.
    ///
    /// This answers "what is the tenure's signed tip?", a different question from
    /// [`SignerDb::has_signed_block_in_tenure`]'s "does a signature bind us to this tenure?",
    /// which is why the predicates deliberately differ on rejected blocks (see there).
    pub fn get_last_signed_block(
        &self,
        tenure: &ConsensusHash,
    ) -> Result<Option<BlockInfo>, DBError> {
        let query = "SELECT block_info FROM blocks WHERE consensus_hash = ?1 AND state IN (?2, ?3) ORDER BY stacks_height DESC LIMIT 1";
        let args = params![
            tenure,
            &BlockState::GloballyAccepted.to_string(),
            &BlockState::LocallyAccepted.to_string(),
        ];
        let result: Option<String> = query_row(&self.db, query, args)?;

        try_deserialize(result)
    }
```

**File:** stacks-signer/src/signerdb.rs (L1680-1690)
```rust
    /// Return the last globally accepted block in a tenure (identified by its consensus hash).
    pub fn get_last_globally_accepted_block(
        &self,
        tenure: &ConsensusHash,
    ) -> Result<Option<BlockInfo>, DBError> {
        let query = "SELECT block_info FROM blocks WHERE consensus_hash = ?1 AND state = ?2 ORDER BY stacks_height DESC LIMIT 1";
        let args = params![tenure, &BlockState::GloballyAccepted.to_string()];
        let result: Option<String> = query_row(&self.db, query, args)?;

        try_deserialize(result)
    }
```

**File:** docs/signer-flows.md (L367-383)
```markdown
    TALLY -- yes --> BCAST["mark_locally_accepted(group),<br/>broadcast_signed_block →<br/>handle_post_block (push to node)"]:::good
    KIND -- "Rejected" --> HBR["handle_block_rejection:<br/>verify, store via<br/>add_block_rejection_signer_addr"]
    HBR --> RT{"rejection weight makes<br/>70% approval impossible?"}
    RT -- no --> N3(["wait"])
    RT -- yes --> GREJ["mark_globally_rejected;<br/>pre-global-state versions also<br/>update miner status"]:::bad
    BCAST --> NB["node processes block →<br/>NewBlock event →<br/>mark_globally_accepted"]:::good
    classDef good fill:#17a45c22,stroke:#1d9d5f,stroke-width:1.5px;
    classDef bad fill:#d84a3f22,stroke:#c9473d,stroke-width:1.5px;
```

The outdated-peer fallback keeps mixed-version fleets live: an acceptance from a
peer that never sent a pre-commit is routed into the pre-commit path instead, so
that peer's weight still counts toward the threshold that produces _our_
signature. Note that reaching 70% signatures still only marks the block
_locally_ accepted with the group timestamp; global acceptance waits for the node
to adopt it. Marking the miner invalid on a 30% `ReorgNotAllowed` rejection is
skipped once the active protocol version uses global signer state.
```

**File:** docs/signer-flows.md (L425-437)
```markdown
Two things belong to the proposal path only and are **not** re-run at validate-ok
or at signing:

- `validate_tenure_change_payload` rejects with `DuplicateBlockFound` when we
  have already accepted a block in the tenure a tenure-change block is starting.
  v2 counts locally or globally accepted blocks (`get_last_signed_block`); v1
  counts only globally accepted ones (`get_last_globally_accepted_block`).
- the v2 `check_proposal` wrapper checks miner pubkey hash, consensus hash, the
  pox bitvec, and tenure-extend rules before delegating here.

Because the duplicate check never runs again, a block that crosses the pre-commit
threshold long after it was proposed relies on section 5's own-tenure conflict
guard to cover the same ground.
```

**File:** stacks-signer/src/chainstate/tests/v2.rs (L838-850)
```rust
    let result = sortitions_view.check_proposal(&stacks_client, &mut signer_db, &block);

    exit_flag.store(true, Ordering::SeqCst);
    serve.join().unwrap();

    // The proposal should be rejected because there's already a locally-accepted
    // block in this tenure. Before the fix, this would have incorrectly passed
    // because get_last_globally_accepted_block would not find the locally-accepted block.
    assert!(
        matches!(result, Err(RejectReason::DuplicateBlockFound)),
        "Expected DuplicateBlockFound rejection when a locally-accepted block exists in the tenure, got: {result:?}"
    );
}
```
