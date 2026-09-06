# No vulnerability found for this question.

Investigating the described attack chain against the actual mechanism in `stacks-signer/src/chainstate/mod.rs`, `stacks-signer/src/signerdb.rs`, and `stacks-signer/src/v0/signer.rs` shows both premises the question relies on are false:

**1. `proposed_time`/timing input is not attacker-controlled.** `check_parent_tenure_choice`'s reorg-timing branch compares `sortition_state_received_time` (the local signer's own recorded receipt time for the *new* burn block) against `local_block_info.approved_time` (the local signer's own recorded approval time for the reorged tenure's first block) [1](#0-0) . Both timestamps are locally derived by the victim signer's own database records, not fields the attacker supplies in a `BlockProposal`. The attacker cannot directly "engineer" this value into the timing window; the only way a tenure is "poorly timed" is if its own first block genuinely was approved close to the real next sortition — which is the exact case the rule is intentionally designed to permit a reorg for, as documented in `check_parent_tenure_choice`'s own doc comment [2](#0-1)  and in `docs/signer-flows.md` [3](#0-2) .

**2. The "permitting sortition not yet confirmed canonical" window does not exist as claimed, and even if a fork occurred, it is not exploitable.** `check_parent_tenure_choice` runs on `self` (the sortition currently being evaluated as the new tip), which the signer only has data for because the node already reported it as the current sortition view — it is not a separate, unconfirmed candidate. More importantly, the permit is never trusted as a one-time decision: every time a competing block reaches the pre-commit signing threshold, `reorg_permit_stands` re-queries the node fresh via `get_sortition_by_burn_hash` to check the permitting sortition's *current* canonical status before excluding a conflict [4](#0-3) . If a burnchain fork orphans the permitting sortition in between, the very next evaluation sees `reorg_permit_stands` return `false`, the conflict is restored, and the second signature is blocked [5](#0-4) .

This exact scenario — permit granted, then permitting sortition orphaned before the second block is signed — is already covered by existing tests: `standing_reorg_permit_clears_conflict` (permit stands → signs) and `orphaned_reorg_permit_restores_conflict` (permit voided by fork → signature blocked) [6](#0-5) . There is no gap between "permit granted" and "canonicity re-checked" that an attacker can race, because the canonicity check happens live at each signing evaluation rather than being cached from grant time.

Since neither the timing input is attacker-forgeable nor is there an actual unguarded window between permit grant and canonicity confirmation, the claimed equivocation path does not exist.

### Citations

**File:** stacks-signer/src/chainstate/mod.rs (L159-169)
```rust
impl SortitionData {
    /// Check if the tenure defined by `sortition_state` is building off of an
    ///  appropriate tenure.
    ///
    /// A permitted reorg is recorded once the whole reorg is permitted: each tenure whose
    /// blocks this one is allowed to replace is marked superseded (see
    /// [`SignerDb::mark_tenure_superseded`]), so a signature we already placed on one of those
    /// blocks does not later block the replacement. The record carries this tenure's sortition
    /// as the permitting one, so the permit stops applying if a burnchain fork later orphans
    /// it. Nothing is recorded for a refused reorg, even for the tenures in it that
    /// individually qualified.
```

**File:** stacks-signer/src/chainstate/mod.rs (L247-259)
```rust
            let checked_proposal_timing = if let Some(sortition_state_received_time) =
                sortition_state_received_time
            {
                // how long was there between when the proposal was received and the next sortition started?
                let proposal_to_sortition = if let Some(approved_at) =
                    local_block_info.approved_time
                {
                    sortition_state_received_time.saturating_sub(approved_at)
                } else {
                    info!("We did not sign over the reorged tenure's first block, considering it as a late-arriving proposal");
                    0
                };
                if Duration::from_secs(proposal_to_sortition) < *first_proposal_burn_block_timing {
```

**File:** docs/signer-flows.md (L496-511)
```markdown
One decision does have to be recorded, because it is ours rather than the
node's. When a miner builds off something other than the prior sortition,
`check_parent_tenure_choice` decides whether the reorg is allowed: it is, if
every tenure being reorged has at most one globally accepted block and produced
its first block too close to the next sortition to count
(`first_proposal_burn_block_timing`). Having sanctioned that replacement, the
signer records those tenures as **superseded** (`mark_tenure_superseded`), so its
own signature over what they built does not then block the replacement it just
permitted — the node cannot answer this one at signing time, since it still
serves the reorged tenure as fully live until the replacement lands. What _is_
still derived from the node is the permit's own validity: the record carries the
permitting tenure's sortition, and it only excludes conflicts while that
sortition remains canonical (section 5, `reorg_permit_stands`), so a burnchain
fork that orphans the permitting tenure automatically voids the permit. A record
more than `MAX_FORK_DEPTH` (100) burn blocks below the tip is dropped; a fork
that deep would cause far bigger problems than a stale conflict.
```

**File:** stacks-signer/src/v0/signer.rs (L1222-1247)
```rust
    fn reorg_permit_stands(
        &self,
        stacks_client: &StacksClient,
        conflict: &SignedConflictInfo,
    ) -> bool {
        let Some(superseded_by) = &conflict.superseded_by else {
            return false;
        };
        match stacks_client.get_sortition_by_burn_hash(&superseded_by.burn_block_hash) {
            Ok(_) => true,
            Err(ClientError::RequestFailure(reqwest::StatusCode::NOT_FOUND)) => {
                info!("{self}: The tenure we permitted to reorg a conflicting block's tenure was itself orphaned by a burnchain fork. The permit no longer excludes the conflict.";
                    "conflicting_consensus_hash" => %conflict.consensus_hash,
                    "superseded_by_consensus_hash" => %superseded_by.consensus_hash,
                    "superseded_by_burn_block_hash" => %superseded_by.burn_block_hash,
                );
                false
            }
            Err(e) => {
                warn!("{self}: Failed to check whether the sortition that permitted a reorg is still canonical: {e:?}. Treating the permit as void.";
                    "conflicting_consensus_hash" => %conflict.consensus_hash,
                    "superseded_by_consensus_hash" => %superseded_by.consensus_hash,
                );
                false
            }
        }
```

**File:** stacks-signer/src/v0/signer.rs (L1432-1457)
```rust
        if conflicts.iter().any(|conflict| {
            conflict.consensus_hash == block_info.block.header.consensus_hash
                && !self.reorg_permit_stands(stacks_client, conflict)
        }) {
            match stacks_client.get_tenure_tip(&block_info.block.header.consensus_hash) {
                Ok(tip) => {
                    let tip_height = tip.anchored_header.height();
                    if tip_height >= block_info.block.header.chain_length {
                        warn!(
                            "{self}: Reached the pre-commit threshold for a block that conflicts with previously signed or accepted blocks, and the canonical tip of its tenure is already at or above the proposed height. Refusing to sign.";
                            "signer_signature_hash" => %block_hash,
                            "block_height" => block_info.block.header.chain_length,
                            "canonical_tip_height" => tip_height,
                        );
                        return;
                    }
                }
                Err(e) => {
                    warn!(
                        "{self}: Failed to fetch the canonical tip of the proposed block's tenure: {e:?}. Treating the tenure as unconfirmed.";
                        "signer_signature_hash" => %block_hash,
                        "consensus_hash" => %block_info.block.header.consensus_hash,
                    );
                }
            }
        }
```

**File:** stacks-signer/src/v0/tests.rs (L1193-1222)
```rust
    #[test]
    fn standing_reorg_permit_clears_conflict() {
        // Having sanctioned tenure 2 reorging tenure 1, our fresh signature over A must not
        // stand in the way of the replacement we permitted: B is signed immediately.
        let (info_a, info_b) = run_cross_tenure_scenario(TenureAFate::SupersededPermitCanonical);
        assert_a_signed(&info_a);
        assert_eq!(
            info_b.state,
            BlockState::LocallyAccepted,
            "block B should be signed: we permitted the reorg that replaces the conflicting sibling, got: {}",
            info_b.state
        );
        assert!(
            info_b.signed_self.is_some(),
            "block B should carry our signature while the reorg permit stands"
        );
    }

    #[test]
    fn orphaned_reorg_permit_restores_conflict() {
        // The permit is only as alive as the sortition it was granted to. Once a burnchain
        // fork orphans the permitting sortition, the reorg we sanctioned can no longer
        // happen, so the record must stop suppressing the conflict and B is refused again.
        let (info_a, info_b) = run_cross_tenure_scenario(TenureAFate::SupersededPermitOrphaned);
        assert_a_signed(&info_a);
        assert_b_refused(
            &info_b,
            "the sortition that permitted the reorg was itself orphaned",
        );
    }
```
