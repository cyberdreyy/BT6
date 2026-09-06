### Title
v1 chainstate's tenure-change duplicate check misses locally-accepted (unsigned-to-node) blocks, enabling equivocation - (File: `stacks-signer/src/chainstate/v1.rs`)

### Summary
`stacks-signer/src/chainstate/v1.rs`'s `validate_tenure_change_payload` uses `SignerDb::get_last_globally_accepted_block` to detect a duplicate tenure-start block in the current tenure, while the v2 equivalent in `stacks-signer/src/chainstate/v2.rs` was fixed to use `SignerDb::get_last_signed_block` (which also covers `LocallyAccepted`). This asymmetry — the exact same "rejects a competing tenure-change proposal" guarantee holding in one code path but not the other — mirrors the ZkSync report's pattern of a function whose behavior silently diverges depending on which context/caller invoked it, defeating a documented invariant ("we never restart a tenure we've already signed into").

### Finding Description
The v1 duplicate-tenure-change guard: [1](#0-0) 
only queries `get_last_globally_accepted_block`, i.e. it is blind to a block this very signer has already put its own signature over but that has not yet reached the 70% group threshold (`LocallyAccepted`/`signed_self`).

The v2 code path was patched to close exactly this gap: [2](#0-1) 
using `get_last_signed_block`, with an explicit regression test documenting the prior bug: [3](#0-2) 

No analogous test or fix exists for v1 in this codebase, so a v1-protocol signer's `check_proposal` (via `handle_block_proposal` → `check_block_against_state` → `check_block_against_local_state` → `SortitionsView::check_proposal`) will not reject a second, competing tenure-change block proposed for a tenure in which the signer has only `LocallyAccepted` (not yet `GloballyAccepted`) a prior tenure-start block: [4](#0-3) 

Because the `DuplicateBlockFound` check only runs at proposal time and is never re-run at validate-ok or at signing: [5](#0-4) 
the only remaining backstop is the "own-tenure conflict guard" at pre-commit/signing time (section 5), which relies on `get_signed_conflicts` (this does include `LocallyAccepted`/`signed_self`): [6](#0-5) 
But that guard only blocks signing if the conflicting signature is still "fresh" (within `tenure_last_block_proposal_timeout`) **or** the node's own tenure tip already confirms the earlier block at/above that height: [7](#0-6) [8](#0-7) 

Since the first block was only `LocallyAccepted` (never reached the 70% signature threshold needed to be broadcast to/confirmed by the node), the node's tenure tip will not show it, and once `tenure_last_block_proposal_timeout` elapses the conflict is treated as stale, taking the `OWN -- yes --> TIP -- no — never confirmed --> SIGN` branch. The v1 signer will then place `signed_self` on the second, conflicting tenure-start block — a second signature over a different block at the same tenure/height that it already signed once.

### Impact Explanation
This breaks the "one signed block per tenure-start height" equality that the signature protocol depends on: a v1-protocol signer can end up producing valid signatures over two conflicting (non-canonical-with-each-other) blocks at the same tenure/height. If a miner (a single actor able to control proposal timing/re-broadcast, no majority required) engineers this sequence — propose block A, collect partial LocallyAccepted signatures from some v1 signers, wait past `tenure_last_block_proposal_timeout`, then propose conflicting block B for the same tenure before the node ever confirms A — each such signer can be induced to also sign B, contributing to a genuine equivocation/fork risk. This matches the "Critical: a signer signing an invalid, non-canonical, or conflicting block" impact bucket.

### Likelihood Explanation
Triggerable entirely by a single block-proposing miner plus normal gossip/timing (no other signer's key, no majority, no auth_token needed): re-propose a competing tenure-start block for the same tenure after the freshness window elapses and before the original block gathers the group threshold. This is a plausible operational scenario (slow signer set, network partition, or a miner deliberately delaying/never rebroadcasting the first block to the node) rather than a purely theoretical one, and is scoped to signers still running/falling back to the v1 chainstate path.

### Recommendation
Change `stacks-signer/src/chainstate/v1.rs::validate_tenure_change_payload` to query `SignerDb::get_last_signed_block` (as v2 now does) instead of `get_last_globally_accepted_block`, so a locally-accepted-but-not-yet-globally-accepted block correctly blocks a competing tenure-change proposal in the same tenure, restoring parity with v2's behavior and closing the equivocation window.

### Proof of Concept
1. A v1-protocol signer processes tenure-start block A: it passes `check_proposal` (no prior block in tenure), gets validated OK by the node, reaches `mark_pre_committed`, then via `handle_block_pre_commit`'s own-tenure/pre-commit logic reaches `mark_locally_accepted` (sets `signed_self`) — but total pre-commit/signature weight from the reward set stays below the 70% group threshold (`signed_group` never set, block never becomes `GloballyAccepted`, node tenure tip never advances to A).
2. Time passes beyond `tenure_last_block_proposal_timeout` (`docs/signer-flows.md` freshness cutoff logic, `stacks-signer/src/v0/signer.rs:1393-1397`).
3. The miner proposes tenure-start block B for the same tenure (same `consensus_hash`, competing `previous_tenure_end`/txs). `handle_block_proposal` → `check_block_against_local_state` → `SortitionsView::check_proposal` → `validate_tenure_change_payload` (`stacks-signer/src/chainstate/v1.rs:505-518`) calls `get_last_globally_accepted_block`, which returns `None` (A is only `LocallyAccepted`), so no `DuplicateBlockFound` rejection is produced — B is accepted for further validation.
4. B validates OK at the node (node has no knowledge of A being signed, since A was never broadcast/confirmed). `handle_block_validate_ok` → `check_block_against_signer_db_state` does not re-run the duplicate-tenure check (per `docs/signer-flows.md:435-437`), so B is marked `PreCommitted`, pre-commit broadcast, and eventually reaches the 70% pre-commit threshold.
5. In `handle_block_pre_commit`'s signing-time re-check (`stacks-signer/src/v0/signer.rs:1383-1434`), `get_signed_conflicts` finds A as a conflict, but it is now stale (freshness cutoff passed) and the node's tenure tip does not confirm A (never having been globally accepted) — so `conflicts.iter().any(...)` for the "own tenure confirmed" branch is `false`, and the signer proceeds to `SIGN`, producing a second `signed_self` signature over B at the same tenure/height it already signed for A.

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

**File:** docs/signer-flows.md (L263-268)
```markdown
    FRESH -- "no — all stale" --> OWN{"a conflict in this block's<br/>OWN tenure?"}
    OWN -- yes --> TIP{"own tenure confirmed<br/>at ≥ this height?<br/>get_tenure_tip(own tenure)"}
    TIP -- yes --> HOLD2["refuse to sign"]:::hold
    TIP -- "no — never confirmed" --> SIGN
    TIP -- "node unreachable" --> SIGN
    OWN -- no --> SIGN["SIGN: mark_locally_accepted,<br/>handle_block_signature,<br/>broadcast acceptance"]:::good
```

**File:** docs/signer-flows.md (L425-431)
```markdown
Two things belong to the proposal path only and are **not** re-run at validate-ok
or at signing:

- `validate_tenure_change_payload` rejects with `DuplicateBlockFound` when we
  have already accepted a block in the tenure a tenure-change block is starting.
  v2 counts locally or globally accepted blocks (`get_last_signed_block`); v1
  counts only globally accepted ones (`get_last_globally_accepted_block`).
```

**File:** docs/signer-flows.md (L435-437)
```markdown
Because the duplicate check never runs again, a block that crosses the pre-commit
threshold long after it was proposed relies on section 5's own-tenure conflict
guard to cover the same ground.
```

**File:** stacks-signer/src/v0/signer.rs (L1383-1421)
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

**File:** stacks-signer/src/v0/signer.rs (L1423-1434)
```rust
        // No conflict is both fresh and still live. A conflict that no longer matters, i.e.
        // stale, or provably dead per `conflict_still_blocks`, cannot veto on its own. A
        // stale conflict in another tenure in particular no longer speaks for us: whether this
        // block may replace what another tenure built is settled by the chainstate checks above.
        // A stale conflict in this block's own tenure still blocks if the node already has that
        // tenure at or above the proposed height, since the proposal then duplicates state the
        // node has already built on. (The chainstate checks don't cover this for tenure-change
        // blocks: those check the parent tenure instead of their own.)
        // The permit check is deferred to here so that only same-tenure conflicts pay for it.
        if conflicts.iter().any(|conflict| {
            conflict.consensus_hash == block_info.block.header.consensus_hash
                && !self.reorg_permit_stands(stacks_client, conflict)
```
