### Title
V1 tenure-duplicate guard checks only globally-accepted blocks, letting a v1-mode signer equivocate on a second tenure-change block for the same tenure - (File: `stacks-signer/src/chainstate/v1.rs`)

### Summary
`SortitionsView::validate_tenure_change_payload` in v1 rejects a second tenure-change block for the same tenure only if the signer's local `SignerDb` already holds a **globally accepted** first block for that tenure, via `signer_db.get_last_globally_accepted_block(&block.header.consensus_hash)`. The v2 equivalent instead calls `signer_db.get_last_signed_block(...)`, which also counts blocks the signer merely locally accepted (i.e., already signed itself but that have not yet reached the 70% global threshold). This lets a v1-mode signer that has already signed a first tenure-change block B1 (locally accepted only) sign a conflicting second tenure-change block B2 for the identical tenure, while a v2-mode signer in the same signer set correctly refuses via `DuplicateBlockFound`.

### Finding Description
The intended equality (anti-equivocation guard): "a signer must not add its signature weight to a second, conflicting block for a tenure it has already signed a first block for." V2 enforces this equality using `get_last_signed_block`, whose accompanying comment states explicitly that "Only blocks we have signed (locally or globally accepted) count here" [1](#0-0) . V1 instead checks only globally accepted state: [2](#0-1) 

Exploit flow:
1. Attacker (a single miner-slot winner) proposes tenure-change block B1 for tenure `CH`. A v1-mode signer S runs `check_proposal` → `validate_tenure_change_payload`, finds no prior block for `CH` (`get_last_globally_accepted_block` returns `None`), and signs B1. This records B1 in S's `SignerDb` as (at best) locally accepted — it has S's own signature but has not yet crossed the 70% global threshold (e.g., because the rest of the signer set is slow, partitioned, or a portion refuses for unrelated reasons).
2. Attacker crafts a second, different tenure-change block B2 for the same `CH` (same `prev_tenure_consensus_hash`, so `check_tenure_change_confirms_parent` and `check_parent_tenure_choice` — which validate the *parent* tenure, not duplication within the current tenure — both pass trivially) and gossips B2 as a new `BlockProposal`.
3. S (v1) re-enters `validate_tenure_change_payload`; `get_last_globally_accepted_block(&CH)` still returns `None` because B1 never became globally accepted, so the `DuplicateBlockFound` branch is skipped and `Ok(())` is returned — S signs B2 too.
4. A v2-mode signer in the same set, upon receiving B2, calls `get_last_signed_block(&CH)`, finds B1 (locally accepted counts), and correctly returns `RejectReason::DuplicateBlockFound`.

The existing guards (`check_tenure_change_confirms_parent`, `check_parent_tenure_choice`) validate the *parent* tenure relationship and reorg legitimacy, not same-tenure duplication, so they do not catch this case. No P2P/StackerDB mechanic is exploited — the attacker is simply using the normal `BlockProposal` gossip path with two distinct proposals for the same tenure, well within the described attacker capability (one miner slot, craft proposals, gossip messages).

### Impact Explanation
This breaks the intra-signer equivocation guard (UNIQUENESS): the same v1-mode signer weight can be counted toward two conflicting blocks at the same tenure position. If a sufficient fraction of the active weight runs v1 (e.g., during a rolling signer-software upgrade window, since each signer independently derives `SortitionStateVersion` from its own perceived protocol version), those signers' weight could accumulate on B2 in addition to whatever weight already exists on B1, risking two conflicting blocks each independently approaching/reaching the 70% threshold — a chain-safety violation (Critical: signer contributing to a conflicting/non-canonical block).

### Likelihood Explanation
Preconditions: (1) at least one signer must still be on v1 semantics (protocol version below `GLOBAL_SIGNER_STATE_ACTIVATION_VERSION`, plausible during a rolling upgrade / mixed-version fleet — see `SortitionStateVersion::from_protocol_version` [3](#0-2) ); (2) the first tenure-change block B1 must not yet have crossed the global-acceptance threshold when B2 is proposed (a normal, frequently-occurring timing window, not a rare race). Attacker cost is exactly one miner slot plus the ability to gossip a second `BlockProposal` — no majority-signer collusion, no key compromise, no local host access. It is repeatable across tenures whenever a v1-mode signer is in the set and a window exists between local and global acceptance.

### Recommendation
Change `stacks-signer/src/chainstate/v1.rs::validate_tenure_change_payload` to use `signer_db.get_last_signed_block(&block.header.consensus_hash)` (matching v2's semantics) instead of `get_last_globally_accepted_block`, so that a v1 signer's own locally-accepted (already-signed) block also blocks a conflicting second tenure-change block for the same tenure.

### Proof of Concept
Add to `stacks-signer/src/chainstate/tests/v1.rs`:
1. Build a `SortitionsView` (v1) with a `cur_sortition` for tenure `CH` and a `SignerDb`.
2. Insert into `SignerDb` a `BlockInfo` for a tenure-change block B1 at `CH` in state `LocallyAccepted` only (not `GloballyAccepted`) — e.g. via the same helper used for `get_last_signed_block`/`get_last_globally_accepted_block` tests, ensuring `get_last_globally_accepted_block(&CH)` returns `None` while `get_last_signed_block(&CH)` returns `Some(B1)`.
3. Construct a second `NakamotoBlock` B2 with a `TenureChangePayload` for the same `CH` and same `prev_tenure_consensus_hash`, differing (e.g.) in the transaction set / miner signature, so `B2.signer_signature_hash() != B1.signer_signature_hash()`.
4. Call `validate_tenure_change_payload` (v1) on B2 and assert `Ok(())` is returned (no `RejectReason::DuplicateBlockFound`), demonstrating the equivocation gap.
5. As a contrast, run the same DB state through v2's `GlobalStateView::validate_tenure_change_payload` and assert it returns `Err(RejectReason::DuplicateBlockFound)`, proving the divergence documented above.

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

**File:** stacks-signer/src/chainstate/mod.rs (L532-540)
```rust
impl SortitionStateVersion {
    /// Convert the protocol version to a sortition state version
    pub fn from_protocol_version(version: u64) -> Self {
        if version < GLOBAL_SIGNER_STATE_ACTIVATION_VERSION {
            Self::V1
        } else {
            Self::V2
        }
    }
```
