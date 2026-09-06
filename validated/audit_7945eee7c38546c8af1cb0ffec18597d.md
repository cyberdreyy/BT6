### Title
Per-signer equivocation guard in v1 (`get_last_globally_accepted_block`) lets a signer double-sign two conflicting tenure-start blocks - ([File: stacks-signer/src/chainstate/v1.rs])

### Summary
`v1::SortitionsView::validate_tenure_change_payload` rejects a duplicate tenure-change proposal for the current tenure only if this signer's prior block in that tenure has already reached *global* acceptance [1](#0-0) , whereas `v2::GlobalStateView::validate_tenure_change_payload` rejects it as soon as this signer has *locally* accepted (i.e. already signed) any block in that tenure [2](#0-1) . Because global acceptance is only inferred asynchronously from gossiped peer responses, a v1 signer can sign a second, conflicting tenure-start block for a tenure it already signed, before its own view of that first block flips from "signed" to "globally accepted."

### Finding Description
Both functions implement the same intended safety property - "never let this signer sign two different blocks for the same tenure" - but they measure "already signed" differently:

- v1: `signer_db.get_last_globally_accepted_block(&block.header.consensus_hash)` [3](#0-2)  - only counts a prior block as "already signed" once *global* threshold has been observed and recorded.
- v2: `signer_db.get_last_signed_block(&block.header.consensus_hash)` [4](#0-3)  - counts a prior block as "already signed" as soon as this signer locally accepted it (i.e., produced its own signature), explicitly excluding only pre-commits. The comment directly preceding this call documents the rationale: "Only blocks we have signed (locally or globally accepted) count here" [5](#0-4) .

Global-acceptance status on a given signer's local DB is populated by tallying gossiped `BlockResponse` votes from peers over StackerDB - it lags the signer's own act of signing by however long it takes to observe enough matching peer votes. Consequently there is a real window, on every tenure-change proposal, in which a v1 signer has already signed block B1 for tenure T (and broadcast that signature) but its own `get_last_globally_accepted_block(T)` still returns `None` because it hasn't yet tallied threshold-worth of peer accepts. An attacker who wins the tenure's miner slot can exploit this window directly:

1. Propose block B1 (a valid tenure-change block for tenure T) and get it signed by some signers (this happens naturally through ordinary propagation delay - no privileged access needed).
2. Before that acceptance count round-trips back into every signer's own "globally accepted" record, gossip a second, conflicting tenure-change block B2 for the same tenure T (same `consensus_hash`, different `signer_signature_hash`/content, e.g., a different parent-tenure choice or different transactions).
3. Every v1 signer that already signed B1 re-evaluates B2: its `get_last_globally_accepted_block(T)` still returns `None` (B1 not yet globally accepted from that signer's perspective), so the duplicate check at [6](#0-5)  passes and the signer proceeds to sign B2 as well.

This produces two independently-signed, mutually exclusive blocks (B1 and B2) for the same tenure, each carrying valid signer signatures. Node-side or aggregation code does not detect or reconcile this: the miner/StackerDB simply tallies whichever signature set first reaches the reward-cycle threshold weight, with no cross-check that the contributing signers hadn't already signed a conflicting block for the same tenure via a different code path. This is the exact scenario the question posits (a v1 subset signs what a v2 subset - correctly using `get_last_signed_block` - would reject as `DuplicateBlockFound`), and it is reachable in a network still running any weight of v1 signers, mixed or not, since v1's guard is independently defective; v2's stricter check is not a mitigant for signers still on v1.

### Impact Explanation
This breaks the core "one signature per signer per tenure slot" (non-equivocation) invariant, which is a chain-safety property. If enough v1-weighted signers double-sign, two conflicting tenure-start blocks can each independently accumulate a valid-looking aggregate signature at the same tenure height - a signer signing a conflicting block, matching the stated Critical impact category. The scenario is fully repeatable on every tenure-change proposal wherever v1 signers hold weight, since the window is inherent to gossip-based global-acceptance tallying, not a one-off race.

### Likelihood Explanation
Preconditions: (1) at least one v1-processing signer (v1 is still the fallback/default absent 70%-weight consensus on v2, per `determine_active_signer_protocol_version`), and (2) the attacker only needs to win a single miner slot and gossip two competing tenure-change block proposals in quick succession, both well within the capability of the stated unprivileged attacker. No majority-signer collusion, no compromised keys, and no auth_token are required. The exploit window naturally exists on every tenure change (gossip-based global-acceptance updates are never instantaneous), so this is highly likely to be triggerable and repeatable across tenures.

### Recommendation
Change `v1::SortitionsView::validate_tenure_change_payload`'s duplicate-tenure check to use `get_last_signed_block` (locally-or-globally accepted) instead of `get_last_globally_accepted_block`, matching v2's semantics, so that a signer's own local signing act is immediately and unconditionally sufficient to block it from signing a second conflicting block in the same tenure, independent of asynchronous global-acceptance tallying.

### Proof of Concept
Rust test plan (in `stacks-signer` chainstate test module):
1. Construct a `SignerDb` and mark a `BlockInfo` for block `B1` (tenure `T`) as `LocallyAccepted` (signed) but not `GloballyAccepted`, mirroring the state right after this signer signs but before it tallies peer votes.
2. Call `signer_db.get_last_globally_accepted_block(&T)` and assert it returns `None` (v1 view: not a duplicate).
3. Call `signer_db.get_last_signed_block(&T)` and assert it returns `Some(B1)` (v2 view: is a duplicate).
4. Build a `SortitionsView` (v1) and a `GlobalStateView` (v2) against the identical `SignerDb`/state, and call `validate_tenure_change_payload` on both with a second block `B2` in tenure `T` (`consensus_hash == T`, different `signer_signature_hash`).
5. Assert `v1::SortitionsView::validate_tenure_change_payload(...)` returns `Ok(())` (accepts, would sign) while `v2::GlobalStateView::validate_tenure_change_payload(...)` returns `Err(RejectReason::DuplicateBlockFound)`.
6. Assert that no code in `stacks-signer/src/v0/signer.rs`'s `check_block_against_state`/aggregation path reconciles or blocks this divergence - i.e., the v1 signer would proceed to sign `B2` even though it already signed `B1`.

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
