### Title
Height-only equality check in `check_block_builds_on_highest_block_in_tenure` lets a signer pre-commit/sign a block built on a non-canonical sibling parent - ([File: stackslib/src/net/api/postblock_proposal.rs])

### Summary
The node-side proposal validator that backs the signer's "does this block build on the tenure tip?" check compares only **block height**, not block identity, between a proposal's declared parent and the tenure's actual highest known block. This is the same bug class as CVE-2021-32841 (SharpZipLib): a boundary/equality check that verifies a weaker property (`X`'s height equals `Y`'s height / `path` starts with `dir`) instead of the exact identity/containment relation (`X` *is* `Y` / `path` is truly inside `dir`), letting an attacker satisfy the check with something that only superficially matches.

### Finding Description
`check_block_builds_on_highest_block_in_tenure` fetches the tenure's highest known header and the block header referenced by the proposal's `parent_block_id`, then validates the relationship with: [1](#0-0) 

```rust
if parent_header.anchored_header.height() != highest_header.anchored_header.height() {
    ...
    return Err(BlockValidateRejectReason { reason_code: ValidateRejectCode::InvalidParentBlock, ... });
}
Ok(())
```

This is a **height equality** check, not an **identity** check (`parent_header.index_block_hash() == highest_header.index_block_hash()`, or equivalently comparing `StacksBlockId`s). If the node's local chainstate DB contains more than one processed/known header at the same Stacks height within the same tenure (consensus hash) — e.g. two blocks the miner proposed as equivocating siblings at the same height, both of which got locally/individually processed and stored as headers by this node before the network settled on one — then a proposal whose `parent_block_id` points at the *non-canonical* sibling instead of the actual highest header will still pass this check, because the heights match even though the blocks are different.

This function is explicitly `DO NOT CALL FROM CONSENSUS CODE` and used purely to give the *signer* an off-chain sanity signal via `postblock_proposal.rs`'s `validate()` path [2](#0-1) , which is invoked from `check_block_has_valid_parent` for non-tenure-start blocks and tenure-start blocks alike [3](#0-2) . Per `docs/signer-flows.md`, the signer treats a "validate OK" response as the gate that leads to `mark_pre_committed` → pre-commit weight accumulation → sign, and this parent check is one of the properties the node claims to guarantee "for the signer" [2](#0-1) .

Because the check only compares heights and not identity, a crafted proposal can make `check_block_has_valid_parent` succeed while its parent is actually a sibling/orphaned block rather than the true chain tip of the tenure — breaking the intended equality "approved-parent == canonical-tip" that this function is supposed to enforce.

### Impact Explanation
If exploitable, this breaks the "approved-parent vs canonical" equality the report's rules call out as a Critical-severity analog: a signer could receive a validate-OK response for, and subsequently pre-commit/sign, a block that does not actually build on the tenure's real highest block, i.e. a non-canonical/conflicting block. Signing such a block risks contributing a signature toward a fork/equivocation that the rest of the equality machinery (`check_latest_block_in_tenure`, `get_last_signed_block`, the `DuplicateBlockFound` guard) is designed to prevent.

### Likelihood Explanation
I could **not fully verify the reachability precondition** — specifically, whether `NakamotoChainState::get_block_header` / `find_highest_known_block_header_in_tenure` can ever return two *different* headers at the same height for the same tenure consensus hash in a single node's local chainstate DB (i.e., whether the headers table can hold more than one processed candidate at a given height/tenure pair, such as via locally-validated-but-not-yet-canonical sibling blocks). I was not able to load the full body of `find_highest_known_block_header_in_tenure` / `get_block_header` in `stackslib/src/chainstate/nakamoto/mod.rs` before running out of tool iterations, so I cannot confirm whether the headers table enforces a height/tenure uniqueness constraint that would make this equality check safe in practice (in which case the height check would be an accepted shorthand for identity, not a genuine flaw). This is the key open question that determines whether this is a real, one-slot-miner-triggerable bug or a benign simplification.

### Recommendation
Regardless of current reachability, the check should be hardened to compare block identity, not merely height, to remove any ambiguity and make the invariant robust against future changes to how/when headers get persisted:
```rust
if parent_header.index_block_hash() != highest_header.index_block_hash() {
    ...
}
```
This mirrors the SharpZipLib fix pattern: replace a weak boundary/prefix-style comparison with an exact identity/containment check.

### Proof of Concept
Not able to construct a concrete, verified PoC within the available context: doing so requires confirming (via `find_highest_known_block_header_in_tenure`/`get_block_header`'s full implementation, not fully retrieved) that the node's headers table can hold two distinct headers at the same height for the same tenure so that a proposal can name the non-canonical one as `parent_block_id` while `find_highest_known_block_header_in_tenure` returns the canonical one (or vice versa) with matching heights. Given this unresolved precondition, I present this as a **candidate analog requiring further investigation** rather than a confirmed exploit — a Devin session with full file access to `stackslib/src/chainstate/nakamoto/mod.rs` (`find_highest_known_block_header_in_tenure`, `get_block_header`) is needed to determine definitively whether same-tenure/same-height header collisions are possible in the headers table, which would confirm or refute exploitability.

### Citations

**File:** stackslib/src/net/api/postblock_proposal.rs (L435-448)
```rust
        if parent_header.anchored_header.height() != highest_header.anchored_header.height() {
            warn!(
                "Rejected block proposal";
                "reason" => "Block's parent is not the highest block in this tenure",
                "consensus_hash" => %tenure_id,
                "parent_header.height" => parent_header.anchored_header.height(),
                "highest_header.height" => highest_header.anchored_header.height(),
            );
            return Err(BlockValidateRejectReason {
                reason_code: ValidateRejectCode::InvalidParentBlock,
                reason: "Block is not higher than the highest block in its tenure".into(),
                failed_txid: None,
            });
        }
```

**File:** stackslib/src/net/api/postblock_proposal.rs (L494-522)
```rust
        if !is_tenure_start {
            // this is a well-formed block that is not the start of a tenure, so it must build
            // atop an existing block in its tenure.
            Self::check_block_builds_on_highest_block_in_tenure(
                chainstate,
                sortdb,
                &block.header.consensus_hash,
                &block.header.parent_block_id,
            )?;
        } else {
            // this is a tenure-start block, so it must build atop a parent which has the
            // highest height in the *previous* tenure.
            let parent_header = NakamotoChainState::get_block_header(
                chainstate.db(),
                &block.header.parent_block_id,
            )?
            .ok_or_else(|| BlockValidateRejectReason {
                reason_code: ValidateRejectCode::UnknownParent,
                reason: "No parent block".into(),
                failed_txid: None,
            })?;

            Self::check_block_builds_on_highest_block_in_tenure(
                chainstate,
                sortdb,
                &parent_header.consensus_hash,
                &block.header.parent_block_id,
            )?;
        }
```

**File:** stackslib/src/net/api/postblock_proposal.rs (L600-606)
```rust
        // (For the signer)
        // Verify that the block's tenure is on the canonical sortition history
        Self::check_block_has_valid_tenure(&db_handle, &self.block.header.consensus_hash)?;

        // (For the signer)
        // Verify that this block's parent is the highest such block we can build off of
        Self::check_block_has_valid_parent(chainstate, sortdb, &self.block)?;
```
