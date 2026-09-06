### Title
Stale-signature bypass lets a signer double-sign two tenure-start blocks in the same tenure under signer protocol v1 - ([File: stacks-signer/src/chainstate/v1.rs])

### Summary
`SortitionsView::validate_tenure_change_payload` in the v1 chainstate module rejects a proposed tenure-start block as `DuplicateBlockFound` only if a block was **globally** accepted in that tenure, never if a block was only **locally** accepted. Combined with the staleness/"never confirmed" branch of the pre-commit conflict guard in `handle_block_pre_commit`, a single miner can get a v1 signer set to put a second, conflicting signature over a different tenure-start block in the same tenure once the first (locally-accepted, never pushed) block's endorsement goes stale — an equivocation the guard exists specifically to prevent.

### Finding Description
Duplicate-block protection for tenure-start proposals is split across two layers, as the design doc for this signer explicitly documents:

- **Proposal-time check** (`validate_tenure_change_payload`), which runs once when the proposal first arrives, and
- **Pre-commit-time conflict guard** (`handle_block_pre_commit` → `get_signed_conflicts` / freshness / own-tenure `get_tenure_tip`), which is supposed to be the sole remaining backstop for a block that "crosses the pre-commit threshold minutes later." [1](#0-0) 

In `chainstate/v1.rs`, the proposal-time duplicate check only looks at globally accepted blocks in the tenure:
```rust
let last_in_current_tenure = signer_db
    .get_last_globally_accepted_block(&block.header.consensus_hash)...
if let Some(last_in_current_tenure) = last_in_current_tenure {
    ...
    return Err(RejectReason::DuplicateBlockFound);
}
``` [2](#0-1) 

This is a materially weaker check than the corresponding v2 logic, which was hardened for exactly this gap — a companion test states this explicitly:
```
// The proposal should be rejected because there's already a locally-accepted
// block in this tenure. Before the fix, this would have incorrectly passed
// because get_last_globally_accepted_block would not find the locally-accepted block.
``` [3](#0-2) 

No equivalent fix exists in `chainstate/v1.rs`: it still calls `get_last_globally_accepted_block` unconditionally, so a tenure-start block A that reached only `LocallyAccepted` state (70% pre-commit → signature weight, but never handed to the node because global acceptance requires the node to actually process it) does **not** block a second, competing tenure-start proposal B for the same tenure from passing `check_proposal` under v1.

The remaining defense is the pre-commit conflict guard in `handle_block_pre_commit` (`get_signed_conflicts`, freshness, `conflict_still_blocks`, own-tenure `get_tenure_tip`). But this guard is explicitly designed to yield once a conflicting signature is *stale* and the node has never confirmed the conflicting tenure:
```
FRESH -- "no — all stale" --> OWN{"a conflict in this block's OWN tenure?"}
OWN -- yes --> TIP{"own tenure confirmed at ≥ this height? get_tenure_tip(own tenure)"}
TIP -- "no — never confirmed" --> SIGN
``` [4](#0-3) 

`get_signed_conflicts` does return A as a conflict for B, since it returns any block that was ever `signed_self`/`signed_group`, including merely `LocallyAccepted` blocks: [5](#0-4) 
But if A's `last_endorsed` timestamp is older than `tenure_last_block_proposal_timeout` (the "freshness cutoff"), and A was never globally accepted so the node's `get_tenure_tip` for that tenure shows nothing confirmed, `conflict_still_blocks`/`TIP` resolves to "never confirmed" and the code path proceeds to `SIGN`: [6](#0-5) 

The design doc itself notes signatures are "bearer instruments" that never expire and can still be aggregated toward the 70% node-push threshold even after rejection or staleness on the signer's own end: [7](#0-6) 
So block A's earlier signatures remain valid and aggregable while block B now also collects a fresh 70% of signatures — two conflicting blocks at the same tenure-start position both carrying enough signature weight to be pushed to the node, i.e. a genuine equivocation.

### Impact Explanation
This breaks the "one signature per position" safety invariant the pre-commit conflict guard exists to enforce: a single one-slot miner (using its own re-proposal, no majority signer collusion or key compromise needed) can, under signer protocol v1, cause the signer set to accumulate valid signatures over two different, conflicting tenure-start blocks in the same tenure. This maps directly to the Critical impact class: "a signer signing an invalid, non-canonical, or conflicting block."

### Likelihood Explanation
Requires: (a) a v1-protocol signer set (older/mixed-version fleet, since v2 already patched the underlying gap), (b) the first tenure-start block reaching local (not global) acceptance — plausible whenever the block-push to the node is delayed, dropped, or the miner never gets it to the node — and (c) the miner waiting past `tenure_last_block_proposal_timeout` before re-proposing a competing tenure-start block. All of this is achievable by the single active miner without any other signer's cooperation, making it a realistic, if timing-dependent, attack.

### Recommendation
Backport the v2 fix to `chainstate/v1.rs::validate_tenure_change_payload`: check for the last *locally-or-globally* accepted block in the tenure (e.g. via `get_last_accepted_block`/`get_last_signed_block` rather than `get_last_globally_accepted_block` alone), matching v2's semantics, so the proposal-time `DuplicateBlockFound` check catches this case before the weaker, staleness-tolerant pre-commit guard is ever relied upon as the sole backstop.

### Proof of Concept
1. Configure a signer set on protocol v1.
2. Miner proposes tenure-start block A for tenure T; signer set pre-commits and signs A (reaches 70% signature weight, `LocallyAccepted`), but A is never successfully pushed to / processed by the node (e.g. simulate a dropped `broadcast_signed_block`/node outage), so A never reaches `GloballyAccepted`.
3. Wait past `tenure_last_block_proposal_timeout`.
4. Miner (same tenure, same slot) proposes a second tenure-start block B for tenure T with different transactions.
5. Observe: `SortitionsView::check_proposal` → `validate_tenure_change_payload` calls `get_last_globally_accepted_block(T)`, which returns `None` (A was never globally accepted) — B is **not** rejected as `DuplicateBlockFound`.
6. B is validated OK and pre-committed; at the pre-commit threshold, `handle_block_pre_commit` finds A via `get_signed_conflicts`, but A's `last_endorsed` is now older than the freshness cutoff and `get_tenure_tip(T)` shows nothing confirmed (A never reached the node) — the guard falls through to `SIGN`.
7. The signer set now has valid signatures over both A and B for the same tenure-start position — verifiable by inspecting `signerdb` for two `signed_self`/`signed_group` entries at the same tenure/height with different `signer_signature_hash`.

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

**File:** docs/signer-flows.md (L280-286)
```markdown
- the re-check only ever looks at _one_ tenure (a tenure-change block's parent,
  or any other block's own), so a signed sibling at the same height in a third
  tenure is invisible to it;
- the `DuplicateBlockFound` check that would catch a second block in the same
  tenure lives in `check_proposal` and runs only at proposal arrival, never
  again. A block that crosses the pre-commit threshold minutes later has no
  other guard, which is what the own-tenure branch above covers.
```

**File:** docs/signer-flows.md (L322-327)
```markdown
A conflict is any block a signature was ever put over — ours, or a group
threshold we observed — whatever its state now. In particular rejection, even
_global_ rejection, does not clear one: a rejection is a revocable opinion,
while a signature is a bearer instrument that can still be aggregated toward
the 70% threshold if rejecting signers change their minds. Only staleness or
node-derived death (the two questions above) clears a conflict.
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

**File:** stacks-signer/src/chainstate/tests/v2.rs (L838-849)
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
```

**File:** stacks-signer/src/signerdb.rs (L1587-1619)
```rust
    /// Return every signed block at or above the given Stacks height, in ANY tenure, excluding
    /// the block with the given signer signature hash, ordered by height (highest first). A
    /// block is considered signed if a signature was ever put over it, ours (`signed_self`)
    /// or the observed group's (`signed_group`). Blocks that were only pre-committed carry no
    /// signature and are never returned. Each row carries the most recent endorsement time
    /// (`signed_self`/`signed_group`, whichever is later) so the caller can judge freshness per
    /// conflict.
    ///
    /// The search deliberately spans all tenures: two blocks at the same height are siblings
    /// no matter which tenure they belong to (e.g. a tenure-start block conflicts with the
    /// previous tenure's block at the same height), so a signature over either may conflict
    /// with a fresh signature over the other.
    ///
    /// Blocks in tenures whose reorg we sanctioned under the reorg-timing rules (see
    /// [`SignerDb::mark_tenure_superseded`]) are still returned, but annotated with the
    /// permitting tenure's sortition (`superseded_by_*`): the permit only holds while that
    /// sortition is canonical, which the caller derives from the node per evaluation (see
    /// `Signer::reorg_permit_stands`) -- like every other question about whether a conflict is
    /// still *live* (`Signer::conflict_still_blocks`), it is not recorded.
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
```

**File:** stacks-signer/src/v0/signer.rs (L1192-1206)
```rust
        let node_reaches_conflict = match stacks_client.get_tenure_tip(&conflict.consensus_hash) {
            Ok(tip) => tip.anchored_header.height() >= conflict.stacks_height,
            // A 404 is an answer, not a failure: the node has no blocks in that tenure at all.
            Err(ClientError::RequestFailure(reqwest::StatusCode::NOT_FOUND)) => false,
            Err(e) => {
                warn!("{self}: Failed to fetch the canonical tip of a conflicting block's tenure: {e:?}. Leaving the conflict in place.";
                    "conflicting_consensus_hash" => %conflict.consensus_hash,
                    "conflicting_block_height" => conflict.stacks_height,
                );
                return true;
            }
        };
        node_reaches_conflict
            || (!conflict.globally_accepted && conflict.stacks_height <= proposed_height)
    }
```
