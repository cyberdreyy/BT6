### Title
Stale, unconfirmed same-tenure conflicts let a signer sign two conflicting blocks at the same height in the same tenure - ([File: stacks-signer/src/v0/signer.rs])

### Summary
`handle_block_pre_commit`'s own-tenure conflict guard treats a stale, node-unconfirmed prior signature as safe to override, without requiring any burnchain-fork evidence that the earlier signed block is actually dead. A single miner (plus normal StackerDB gossip, no majority-key compromise) that delays getting its first tenure-start block adopted by the node can obtain a second, conflicting signature set over a different tenure-start block at the same height in the same tenure once `tenure_last_block_proposal_timeout` elapses.

### Finding Description
This is a batch/single-path asymmetry analogous to the `mintBatch` bug: the `DuplicateBlockFound` check that would reject a second tenure-start block for a tenure runs only once, at proposal arrival, inside `validate_tenure_change_payload` (`stacks-signer/src/chainstate/v1.rs` and `v2.rs`), and is documented as never re-run at validate-ok or at signing time [1](#0-0) . The redundant backstop for this gap is the height-based, cross-tenure "signed conflicts" check performed in `handle_block_pre_commit` via `get_signed_conflicts` [2](#0-1) .

That backstop, however, only blocks while a conflict is both *fresh* (`last_endorsed > freshness_cutoff`, i.e. within `tenure_last_block_proposal_timeout`) and *still live* per `conflict_still_blocks` [3](#0-2) . For a conflict in the block's *own* tenure, once it goes stale, the design explicitly falls through to a check of whether the node's own view of that tenure already confirms a block at or above the proposed height (`get_tenure_tip`); if the node has *never confirmed* that tenure at that height (e.g. block A's signature was never pushed to/adopted by the node), the flow explicitly allows signing to proceed [4](#0-3) .

Unlike the cross-tenure case - where staleness alone is not trusted, and the code demands a burnchain-fork 404 from `get_sortition_by_burn_hash` (proof the tenure was actually orphaned) before treating a conflict as dead - the own-tenure branch requires no such burnchain evidence at all. It only requires (a) enough wall-clock time has passed and (b) the node hasn't yet reported that tenure as being at/above that height. Neither condition proves block A is dead; it may simply not have been broadcast to the node yet. A miner (or any party relaying gossip) that withholds pushing the first signed block from the node until `tenure_last_block_proposal_timeout` expires can then get remaining/same signers to sign a second, different tenure-start block B at the identical height/tenure, since the pre-commit/threshold machinery treats each `signer_signature_hash` independently (`block_info.signed_self.is_some()` is checked per-block-hash, not per-height/tenure) [5](#0-4) .

The root cause is the missing re-check equivalent to `DuplicateBlockFound` at signing time, combined with a staleness-only (no fork-proof) override for same-tenure conflicts - breaking the "one-per-height/tenure, canonical" equality signers are supposed to enforce.

### Impact Explanation
This lets the signer set produce two independently-valid signature sets (each ≥70% weight) over two different, mutually exclusive tenure-start blocks in the same tenure. That is a signer-set equivocation: the chain has two competing "canonical" candidates each carrying a legitimate group signature, which is the Critical bucket "a rejection recounted as acceptance"/"signer signing a conflicting block" scenario this scan is meant to catch.

### Likelihood Explanation
Requires only a single miner slot (no majority signer compromise, no key theft): the miner needs to (1) get block A signed, (2) avoid/delay pushing A to the node (or the push fails/racing/slow), (3) wait out `tenure_last_block_proposal_timeout`, and (4) propose block B for the same tenure/height. Steps 2-3 are entirely within a lone miner's control (it need not even actively censor - ordinary network delay past the timeout suffices), making this more a timing/liveness condition than an adversarial exploit requiring sophistication, though it does depend on the node not adopting A in that window.

### Recommendation
Re-run (or strengthen) the same-tenure duplicate check at pre-commit/signing time using signed (not just node-confirmed) state regardless of staleness, or require the same burnchain-fork proof (`get_sortition_by_burn_hash` 404) for own-tenure conflicts that cross-tenure conflicts already require before allowing a stale conflict to be overridden. At minimum, the own-tenure branch should not permit signing a second block at the same height/tenure absent proof the tenure's sortition itself was orphaned or the first block was rejected/never crossed the pre-commit threshold.

### Proof of Concept
1. Miner proposes tenure-start block A for tenure T; signers pass `validate_tenure_change_payload` (no prior globally/locally accepted block in T), pre-commit, cross 70% weight, and sign A (`mark_locally_accepted`), per `store_and_process_block_signature`.
2. Miner withholds/delays pushing A to its node (or push is slow/fails) so the node's tenure tip for T never reaches A's height within `tenure_last_block_proposal_timeout`.
3. After the timeout, miner proposes block B (different transactions) as a competing tenure-start block for the same tenure T, same chain_length as A.
4. `validate_tenure_change_payload` again checks only globally/locally-accepted state for T (which may itself have been superseded/never reach that stage cleanly in v1), and even if it did flag duplication and get rejected, `handle_block_pre_commit`'s own-tenure logic is the actual gate exercised once B reaches pre-commit threshold: `get_signed_conflicts` finds A as a conflict but `last_endorsed <= freshness_cutoff` (stale) triggers the own-tenure branch; `get_tenure_tip` for T shows no confirmation at/above B's height (because A was never pushed) → signers proceed to sign B.
5. Result: signer set has produced valid ≥70% signature sets over both A and B at the same height in tenure T.

### Citations

**File:** docs/signer-flows.md (L263-268)
```markdown
    FRESH -- "no — all stale" --> OWN{"a conflict in this block's<br/>OWN tenure?"}
    OWN -- yes --> TIP{"own tenure confirmed<br/>at ≥ this height?<br/>get_tenure_tip(own tenure)"}
    TIP -- yes --> HOLD2["refuse to sign"]:::hold
    TIP -- "no — never confirmed" --> SIGN
    TIP -- "node unreachable" --> SIGN
    OWN -- no --> SIGN["SIGN: mark_locally_accepted,<br/>handle_block_signature,<br/>broadcast acceptance"]:::good
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

**File:** stacks-signer/src/v0/signer.rs (L1316-1321)
```rust
        if block_info.signed_self.is_some() {
            debug!(
                "{self}: Received pre-commit for a block that we have already signed. Doing nothing...",
            );
            return;
        }
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

**File:** stacks-signer/src/v0/signer.rs (L1393-1421)
```rust
        let freshness_cutoff = get_epoch_time_secs().saturating_sub(
            self.proposal_config
                .tenure_last_block_proposal_timeout
                .as_secs(),
        );
        // A fresh signature only blocks while the block it covers could still be part of the
        // chain: see `conflict_still_blocks`, which asks the node whether it is. Check
        // freshness first: it is a local timestamp comparison, while `reorg_permit_stands`
        // and `conflict_still_blocks` each query the node, so stale conflicts cost no
        // round-trips.
        if let Some(conflict) = conflicts.iter().find(|conflict| {
            conflict.last_endorsed > freshness_cutoff
                && !self.reorg_permit_stands(stacks_client, conflict)
                && self.conflict_still_blocks(
                    stacks_client,
                    conflict,
                    block_info.block.header.chain_length,
                )
        }) {
            warn!(
                "{self}: Reached the pre-commit threshold for a block, but we have recently signed or accepted a different block at the same or higher height. Refusing to sign.";
                "signer_signature_hash" => %block_hash,
                "block_height" => block_info.block.header.chain_length,
                "conflicting_signer_signature_hash" => %conflict.signer_signature_hash,
                "conflicting_block_height" => conflict.stacks_height,
                "conflicting_consensus_hash" => %conflict.consensus_hash,
            );
            return;
        }
```
