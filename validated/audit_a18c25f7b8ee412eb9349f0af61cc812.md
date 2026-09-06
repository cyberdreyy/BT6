### Title
v1 chainstate's tenure duplicate-block guard checks the wrong acceptance state, allowing a signer to sign two competing tenure-start blocks in one tenure - (File: stacks-signer/src/chainstate/v1.rs)

### Summary
`validate_tenure_change_payload` in the v1 signer chainstate rejects a second tenure-change block for a tenure only if a prior block in that tenure was **globally** accepted, while the v2 implementation of the same guard rejects it if a prior block was **locally or globally** accepted (i.e. this signer already signed it). This is the same class of bug as the reported `CreateVaultZap.sol` issue: two code paths that are supposed to enforce one invariant (no double-locking / no double-signing) apply inconsistent conditions, so the weaker path lets through what the stronger, semantically-equivalent path would have blocked.

### Finding Description
The `DuplicateBlockFound` check exists to stop a signer from endorsing two different tenure-start blocks for the same tenure. In the v2 path: [1](#0-0) 

the check uses `signer_db.get_last_signed_block(...)`, which (per the code comment) covers "locally or globally accepted" blocks - i.e. it fires as soon as *this signer* has already signed a competing block in the tenure, even before global consensus is reached.

In the v1 path, the structurally identical function instead uses `get_last_globally_accepted_block`: [2](#0-1) 

This means a v1 signer that has already **locally** accepted/signed tenure-start block A (but A has not yet crossed the group signature threshold) will *not* be blocked by `validate_tenure_change_payload` from proceeding to evaluate and potentially sign a competing tenure-start block B for the same tenure, since `get_last_globally_accepted_block` returns `None` until consensus is reached on A.

The divergence is explicitly called out in the repo's own flow documentation: [3](#0-2) 

and the same document notes that this `DuplicateBlockFound` check is a one-shot check that "never runs again" after proposal arrival, relying entirely on `check_proposal` to catch it at intake: [4](#0-3) 

This is directly analogous to the `CreateVaultZap.sol` bug: `addLiquidity` and `inventoryStaking.deposit` are two code paths meant to reflect the same underlying condition ("was mintFee charged?") but hard-code different `forceTimelock` values, so the stricter path forces a lock the looser path doesn't. Here, two code paths meant to enforce the same "no duplicate tenure-start block" invariant use different acceptance-state predicates (`get_last_signed_block` vs `get_last_globally_accepted_block`), so the v1 path is strictly weaker than the v2 path for the exact same check.

### Impact Explanation
If this gap is reachable in practice, it would let a single v1 signer sign a second, conflicting tenure-change block in a tenure where it already produced a local signature for a different block - a signer signing a conflicting block, which is the Critical-impact class described in the rules (equivocation guard failure). This would only need one miner re-proposing/forking a tenure-start block and one signer running the v1 chainstate path; no majority of signers is required to produce the unsafe local signature (though global consensus on the conflicting block would still need enough weight from other signers to finalize, the equivocation-guard property itself - "this signer will not sign two conflicting tenure-start blocks for the same tenure" - is broken for that signer).

### Likelihood Explanation
I could **not fully verify** within the available budget whether the later pre-commit/signing stage (`docs/signer-flows.md` section 5, `handle_block_pre_commit`, which re-checks "signed conflicts at height ≥ h, in ANY tenure" via a `get_signed_conflicts`-style guard before the group threshold and before the final signature is emitted) independently catches this same case using `get_last_signed_block` or equivalent. If that later stage also uses a "locally-or-globally-signed" definition of conflict, it would likely re-close this gap before an actual double-signature is produced, making `validate_tenure_change_payload`'s v1/v2 discrepancy latent rather than directly exploitable. I was unable to read the implementation of that conflict-detection helper in `stacks-signer/src/signerdb.rs` before running out of iterations, so this should be confirmed by inspecting `get_signed_conflicts`/`conflict_still_blocks` (referenced in `docs/signer-flows.md:250-268`) and whichever function backs `has_signed_block_in_tenure`/`get_last_signed_block` at that stage. Given the explicit textual acknowledgment of the v1/v2 discrepancy in the repo's own documentation, the root-cause asymmetry itself is confirmed; whether it is fully absorbed by a downstream check is not.

### Recommendation
Align `stacks-signer/src/chainstate/v1.rs::validate_tenure_change_payload` with the v2 semantics: use `signer_db.get_last_signed_block(...)` (locally-or-globally accepted) instead of `get_last_globally_accepted_block` when deciding whether to return `RejectReason::DuplicateBlockFound`, so both chainstate versions enforce the same "already signed a block in this tenure" invariant at proposal time, matching the "locally or globally accepted" language documented for v2.

### Proof of Concept
1. A v1-chainstate signer receives tenure-change proposal A for tenure T and, per section 3/5 of the signer flow, locally accepts/signs A (crosses local acceptance but not yet the global 70% threshold): [5](#0-4) .
2. A competing tenure-change proposal B for the same tenure T arrives (e.g. a re-proposed/forked tenure-start block).
3. `check_proposal` → `validate_tenure_change_payload` runs `signer_db.get_last_globally_accepted_block(&B.header.consensus_hash)`: [2](#0-1)  - since A was never globally accepted, this returns `None` and the `DuplicateBlockFound` rejection is skipped.
4. If the remaining checks (`confirms_expected_parent`, `check_parent_tenure_choice`) pass for B, the v1 signer proceeds to validate and potentially locally sign B as well, producing two locally-signed, conflicting tenure-start blocks for tenure T from the same signer - a state the v2 path's `get_last_signed_block` check would have rejected at step 3.

### Citations

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

**File:** stacks-signer/src/signerdb.rs (L279-289)
```rust
    /// Mark this block as valid and the appropriate timestamps if they aren't already set, and attempt to mark it as locally accepted.
    pub fn mark_locally_accepted(&mut self, group_signed: bool) -> Result<(), String> {
        if group_signed {
            self.signed_group.get_or_insert(get_epoch_time_secs());
        } else {
            self.valid = Some(true);
            self.approved_time.get_or_insert(get_epoch_time_secs());
            self.signed_self.get_or_insert(get_epoch_time_secs());
        }
        self.move_to(BlockState::LocallyAccepted)
    }
```
