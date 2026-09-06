## Analysis: Race between competing pre-committed tenure-start blocks bypasses the duplicate/conflict guards

The Cilium CVE describes HTTP policy that is defined correctly but *not consistently applied to all traffic in scope*, so some packets get through when they should have been dropped. The closest analog in `stacks-core` is the signer's "one signature per tenure/height" invariant, which is enforced by two separate guards — but both guards are defined in terms of *signed* blocks only, and neither is re-run against merely *pre-committed* siblings. That gap is reachable by a one-slot miner re-proposing a competing tenure-start block while the first one is still only pre-committed (not yet signed).

### Title
Signer can equivocate on two competing tenure-start blocks because the duplicate/conflict guards are blind to merely pre-committed siblings - (File: `stacks-signer/src/v0/signer.rs`, `stacks-signer/src/signerdb.rs`, `stacks-signer/src/chainstate/v1.rs`/`v2.rs`)

### Summary
The signer enforces "no two signatures at the same height/tenure" through two checks that are both scoped to *signed* blocks (`LocallyAccepted`/`GloballyAccepted`), never `PreCommitted` ones:
1. At proposal time, `validate_tenure_change_payload` rejects a second tenure-start block with `DuplicateBlockFound` only if it finds an already-*signed* block in the tenure.
2. At pre-commit-threshold crossing, the own-tenure conflict guard in `handle_block_pre_commit` queries `get_signed_conflicts`, which by design excludes pre-committed blocks.

Because a `PreCommitted` block is invisible to both checks, a signer can independently pre-commit to two different tenure-start blocks `A` and `B` proposed for the same tenure/height, and if their pre-commit thresholds cross at different times relative to when each gets locally signed, the same signer can end up signing both — an equivocation the whole guard chain exists to prevent.

### Finding Description
`SignerDb::get_last_signed_block` only returns blocks in `GloballyAccepted`/`LocallyAccepted` state: [1](#0-0) 

The v2 duplicate-tenure-start check uses exactly this query and rejects with `DuplicateBlockFound` only when it finds a signed block: [2](#0-1) 

(v1 is narrower still, checking only `GloballyAccepted` blocks via `get_last_globally_accepted_block`.) [3](#0-2) 

This check runs *only* at proposal arrival (`check_proposal`) and is documented as never re-run at validate-ok or signing time: [4](#0-3) 

The second guard — meant to cover exactly that gap — is the own-tenure conflict check inside `handle_block_pre_commit`, which queries `get_signed_conflicts`: [5](#0-4) 

`get_signed_conflicts` is explicitly defined to exclude pre-committed blocks — only rows with `signed_self` or `signed_group` set are returned: [6](#0-5) 

The doc's own reasoning names the gap and the (insufficient) backstop: [7](#0-6) 

The backstop's "own tenure" branch, when it does see a signed conflict, still lets the signature through if the node's canonical tip hasn't reached the conflicting block yet: [8](#0-7) 

**The break**: nothing anywhere in the pipeline ever compares two *merely pre-committed* siblings against each other. A signer's `BlockInfo` rows are keyed per `signer_signature_hash`, so a signer can hold `PreCommitted` state simultaneously for two distinct tenure-start blocks `A` and `B` in the same tenure/height — each proposal individually passed the `DuplicateBlockFound` check because, at the moment each arrived, the other was still unsigned. Whichever block's pre-commit weight (a network-wide tally, independent from the signature tally) crosses 70% first triggers this signer's own decision to sign it, checked only against *already-signed* conflicts (`get_signed_conflicts`) — which don't yet include the other sibling if it, too, is still merely pre-committed. If timing allows both `A`'s and `B`'s pre-commit thresholds to cross before either is signed by this particular signer, the signer can sign `A` and later sign `B` (or vice versa), because at each individual signing decision the *other* block is not yet in `get_signed_conflicts`' signed-only result set, and `get_tenure_tip` on the node has not yet caught up either (`"TIP -- no — never confirmed --> SIGN"`): [8](#0-7) 

This is structurally the same class of bug as the Cilium advisory: a security-relevant equality ("no two signed blocks per tenure/height") is enforced by mechanisms that are each individually correct for the traffic-shape they were designed to see, but the union of enforcement points has a scope gap (pre-committed-vs-pre-committed) that neither check covers, so specific timing lets prohibited "traffic" — a second signature — through.

### Impact Explanation
A signer that equivocates by signing two conflicting tenure-start blocks at the same height in the same tenure breaks the core "signed vs validated" / "one-per-height" safety equality the entire pre-commit/conflict-guard design exists to protect. This falls squarely under the rules' Critical bucket: "a signer signing an invalid, non-canonical, or conflicting block." If enough signers hit this race independently (each only needs its own local pre-commit/signing timing to line up, not the other signers'), both `A` and `B` could separately accumulate enough real signatures to reach the 70% signature threshold, producing two candidate canonical blocks at the same height/tenure with valid aggregate signer authorization — a chain split at the signer level.

### Likelihood Explanation
Reachable by a single miner (one slot) simply re-proposing a competing tenure-start block for the same tenure before the first one is signed, combined with the normal pre-commit gossip that already exists in the protocol — no majority of signers, no additional keys, and no auth-token/local access are required. The precise timing window (both blocks crossing 70% pre-commit before either is signed by the affected signer) is narrow but is exactly the kind of race the docs already flag as a known blind spot ("Two blind spots make the guard necessary" / "the `DuplicateBlockFound` check ... runs only at proposal arrival, never again"), i.e., the codebase's own documentation acknowledges the mechanism gap; it just does not consider the pre-committed-vs-pre-committed sub-case.

### Recommendation
Extend the conflict surface checked at pre-commit-threshold-crossing (and ideally the `DuplicateBlockFound` proposal-time check) to also consider other `PreCommitted` blocks in the same tenure/height that this signer itself has pre-committed to, not just already-signed ones — e.g., a `get_pending_conflicts`-style query alongside `get_signed_conflicts` that also treats a locally pre-committed sibling as blocking until it or the new block is dropped/rejected/stale. Alternatively, before signing, unconditionally re-run the tenure-scoped duplicate check against the local `PreCommitted` state (not just signed state) to guarantee mutual exclusivity of pre-commit-to-sign transitions within the same tenure/height.

### Proof of Concept
1. Miner proposes tenure-start block `A` for tenure `T` at height `h`. Signer `S` validates and calls `mark_pre_committed()` on `A` (`BlockState::PreCommitted`, no signature yet). `get_last_signed_block(T)` still returns `None` since `A` isn't signed.
2. Before `A` reaches the 70% pre-commit weight, the miner proposes a different tenure-start block `B` for the same tenure `T` at the same height `h` (different tx set ⇒ different `signer_signature_hash`). `validate_tenure_change_payload` for `B` calls `get_last_signed_block(T)`/`get_last_globally_accepted_block(T)`, finds nothing (A is only `PreCommitted`), so `B` passes the duplicate check. `S` validates and pre-commits `B` too.
3. Network-wide pre-commit weight for `A` reaches 70% first (other signers pre-committed to `A` earlier/faster). `S`'s `handle_block_pre_commit` for `A` calls `get_signed_conflicts(h, hash_A)` — `B` is not returned (still only pre-committed, no `signed_self`/`signed_group`). No conflict found ⇒ `S` calls `mark_locally_accepted` and signs `A`.
4. Shortly after, network-wide pre-commit weight for `B` also reaches 70% (independent tally). `S`'s `handle_block_pre_commit` for `B` calls `get_signed_conflicts(h, hash_B)`, which now finds `A` (freshly signed). If `A` has not yet been pushed to and processed by the stacks-node (it needs the *signature* threshold across the whole set, not just `S`'s own signing, to be pushed), `conflict_still_blocks`/`get_tenure_tip(T)` reports the node's tip in `T` has not reached `A`'s height, landing on `"TIP -- no — never confirmed --> SIGN"` ⇒ `S` signs `B` too.
5. `S` has now produced valid signatures over two conflicting tenure-start blocks (`A` and `B`) at the same height in the same tenure — the equivocation the guard chain was built to prevent.

### Citations

**File:** stacks-signer/src/signerdb.rs (L1572-1585)
```rust
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

**File:** stacks-signer/src/signerdb.rs (L1606-1625)
```rust
    pub fn get_signed_conflicts(
        &self,
        height: u64,
        excluded_signer_signature_hash: &Sha512Trunc256Sum,
    ) -> Result<Vec<SignedConflictInfo>, DBError> {
        let query = "SELECT b.consensus_hash, b.signer_signature_hash, b.stacks_height, b.state,
                MAX(COALESCE(b.signed_self, 0), COALESCE(b.signed_group, 0)) AS last_endorsed,
                st.superseded_by_consensus_hash, st.superseded_by_burn_block_hash
            FROM blocks b
            LEFT JOIN superseded_tenures st ON st.consensus_hash = b.consensus_hash
            WHERE (b.signed_self IS NOT NULL OR b.signed_group IS NOT NULL)
                AND b.stacks_height >= ?1
                AND b.signer_signature_hash != ?2
            ORDER BY b.stacks_height DESC";
        let args = params![
            u64_to_sql(height)?,
            excluded_signer_signature_hash.to_string(),
        ];
        query_rows(&self.db, query, args)
    }
```

**File:** stacks-signer/src/chainstate/v2.rs (L340-358)
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
        Ok(())
```

**File:** stacks-signer/src/chainstate/v1.rs (L505-519)
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
        Ok(())
```

**File:** docs/signer-flows.md (L263-268)
```markdown
    FRESH -- "no — all stale" --> OWN{"a conflict in this block's<br/>OWN tenure?"}
    OWN -- yes --> TIP{"own tenure confirmed<br/>at ≥ this height?<br/>get_tenure_tip(own tenure)"}
    TIP -- yes --> HOLD2["refuse to sign"]:::hold
    TIP -- "no — never confirmed" --> SIGN
    TIP -- "node unreachable" --> SIGN
    OWN -- no --> SIGN["SIGN: mark_locally_accepted,<br/>handle_block_signature,<br/>broadcast acceptance"]:::good
```

**File:** docs/signer-flows.md (L274-286)
```markdown
Order matters here: the chainstate re-check runs first and produces an explicit
(sticky) rejection when the block now conflicts with a signed one. The conflict
guard behind it is the silent backstop for what that re-check cannot see, and
silence keeps the door open to sign later once the conflict goes stale. Two
blind spots make the guard necessary:

- the re-check only ever looks at _one_ tenure (a tenure-change block's parent,
  or any other block's own), so a signed sibling at the same height in a third
  tenure is invisible to it;
- the `DuplicateBlockFound` check that would catch a second block in the same
  tenure lives in `check_proposal` and runs only at proposal arrival, never
  again. A block that crosses the pre-commit threshold minutes later has no
  other guard, which is what the own-tenure branch above covers.
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

**File:** stacks-signer/src/v0/signer.rs (L1383-1392)
```rust
        let conflicts = match self
            .signer_db
            .get_signed_conflicts(block_info.block.header.chain_length, &block_hash)
        {
            Ok(conflicts) => conflicts,
            Err(e) => {
                warn!("{self}: Failed to query the signed blocks. Refusing to sign block {block_hash}: {e:?}");
                return;
            }
        };
```
