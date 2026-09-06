### Title
Signer duplicate-block guard on tenure-change proposals uses a weaker (`GloballyAccepted`-only) check in the v1 chainstate path than in v2, letting a signer sign two conflicting blocks in the same tenure - ([File: stacks-signer/src/chainstate/v1.rs])

### Summary
`SortitionsView::validate_tenure_change_payload` (v1 chainstate, used for the older signer-set protocol version) guards against a signer re-signing a competing tenure-change block in a tenure it has already signed by calling `signer_db.get_last_globally_accepted_block(&block.header.consensus_hash)` [1](#0-0)  . The parallel v2 chainstate path (`GlobalStateView::validate_tenure_change_payload`) performs the analogous guard with `signer_db.get_last_signed_block(&block.header.consensus_hash)` and explicitly documents why: pre-commits carry no signature and must not count, but both locally- and globally-accepted blocks do carry this signer's signature and must count [2](#0-1)  . The v1 path never applies that same widening, so it only rejects a competing tenure-change proposal once the earlier block reached the 70% group threshold (`GloballyAccepted`) - not once the signer merely signed it itself (`LocallyAccepted`, `signed_self` set).

### Finding Description
`BlockInfo::mark_locally_accepted` sets `signed_self` (a real signature is produced) and moves the block to `BlockState::LocallyAccepted`, distinct from `mark_globally_accepted`, which requires the network-wide 70% weight to have been observed [3](#0-2)  . Between these two states there is a real signature already broadcast by this signer over a specific block in a specific tenure, but the tenure has not yet reached global consensus.

The v2 duplicate-block guard for tenure-change proposals is deliberately written to treat `LocallyAccepted` and `GloballyAccepted` alike, because a `LocallyAccepted` block already carries this signer's own signature and re-signing a second, conflicting block in the same tenure would produce two conflicting signatures from the same signer [2](#0-1)  . The v1 guard performs the check with `get_last_globally_accepted_block` only, silently omitting the `LocallyAccepted` case [4](#0-3)  . This is the same bug shape as the reference report: a configuration/branch that is supposed to gate an action on a broader condition (`escrowPortion!=0` should always deposit when non-zero; here, "already signed a block in this tenure" should always include a self-signed-but-not-yet-globally-accepted block) instead only checks a narrower subset, silently allowing the guarded action (deposit / signing) to proceed when it should have been blocked.

### Impact Explanation
This breaks the "a signer signs only one canonical block per tenure/height" equality for the v1 protocol path: a signer can lock in `signed_self` for block A in tenure T (locally accepted, no global consensus yet), then receive a competing tenure-change proposal B in the same tenure T (e.g. after a stalled/absent miner situation, or a miner deliberately re-proposing a tenure-change once it sees A is not converging). `validate_tenure_change_payload` in v1 will not catch this as a duplicate because A is only `LocallyAccepted`, not `GloballyAccepted`, so the check passes, the signer proceeds to validate and (subject to the usual pre-commit/threshold flow) can put a second, conflicting signature on B. If B ever gathers enough weight from other signers, the network now has two conflicting signed blocks at the same tenure position, one of which carries this signer's signature despite it having already signed a different, conflicting block — a critical signer-safety violation (signing a conflicting block).

### Likelihood Explanation
No majority collusion or key compromise is required: this is a bug reachable by a single miner (or a legitimate absent-miner/tenure-timeout scenario) proposing a second tenure-change block for a tenure where at least one signer already reached `LocallyAccepted` but not yet `GloballyAccepted`. This is a state that is easily reached whenever pre-commit/signature accumulation is not yet complete for a slow-converging tenure — a normal operational occurrence, not a rare edge case.

### Recommendation
Change the v1 tenure-change duplicate-block guard in `stacks-signer/src/chainstate/v1.rs::validate_tenure_change_payload` to use the same signed-block definition as v2: replace the `get_last_globally_accepted_block` call with `get_last_signed_block` (or equivalent), so that any block for which this signer has already produced `signed_self` in the current tenure blocks acceptance of a conflicting tenure-change proposal, consistent with the v2 rationale.

### Proof of Concept
1. Signer running the v1 (`SortitionsView`) chainstate path receives tenure-change block proposal A for tenure T; it passes validation, and the signer reaches `LocallyAccepted` (produces `signed_self`) via `mark_locally_accepted` before global 70% weight is observed [5](#0-4)  .
2. Before A reaches `GloballyAccepted`, a competing tenure-change block proposal B for the same tenure T (same `consensus_hash`) arrives.
3. `validate_tenure_change_payload` calls `signer_db.get_last_globally_accepted_block(&block.header.consensus_hash)`, which returns `None` because A is only `LocallyAccepted`, so the duplicate-block rejection (`RejectReason::DuplicateBlockFound`) is never returned [4](#0-3)  .
4. B passes `check_proposal`, proceeds through node validation and pre-commit/threshold flow, and the signer ends up signing B - producing a second, conflicting signature in the same tenure from the same signer, whereas the equivalent v2 code path would have blocked B via `get_last_signed_block` catching A's `LocallyAccepted` state [6](#0-5)  .

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

**File:** stacks-signer/src/signerdb.rs (L279-295)
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

    /// Mark this block's signed group time if not already set and attempt to mark it as globally accepted.
    pub fn mark_globally_accepted(&mut self) -> Result<(), String> {
        self.signed_group.get_or_insert(get_epoch_time_secs());
        self.move_to(BlockState::GloballyAccepted)
    }
```
