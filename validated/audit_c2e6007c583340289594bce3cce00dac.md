### Title
V1 chainstate uses a weaker duplicate-block guard than V2, letting a signer equivocate on a tenure-start block - ([File: stacks-signer/src/chainstate/v1.rs])

### Summary
The CVE-2026-12210 report describes a security check (`is_secure_url`) that was correctly hardened in one code path but never propagated to sibling code paths that perform the *same* logical check on the *same* trust-equivalent input. `stacks-signer` has an analogous divergence: the "have I already signed a block in this tenure" duplicate-block guard inside `validate_tenure_change_payload` is implemented twice — once for the V1 (local state machine) chainstate and once for the V2 (global state machine) chainstate — and the two implementations query different predicates from `SignerDb`, so the V1 path is strictly weaker.

### Finding Description
Both `SortitionsView::validate_tenure_change_payload` (V1) and `GlobalStateView::validate_tenure_change_payload` (V2) exist to block a signer from approving a second, competing tenure-start block for a tenure it has already committed a signature to. In V2 the guard is: [1](#0-0) 

which explicitly calls `signer_db.get_last_signed_block(...)`, and the surrounding comment states the intent: "Only blocks we have signed (locally or globally accepted) count here: a block we have merely pre-committed to carries no signature from us" — i.e. the check is meant to catch *any* block this signer has already put a signature on, whether or not the network as a whole has reached the global-acceptance threshold.

The V1 implementation of the identical guard instead calls `get_last_globally_accepted_block`: [2](#0-1) 

`get_last_globally_accepted_block` only returns a block once the tenure's block has reached *global* acceptance (i.e., the aggregated signer weight has crossed the network threshold), which is a narrower predicate than "signed by me." Both `check_proposal` implementations are otherwise structurally identical and route into `validate_tenure_change_payload` from the same call site: [3](#0-2)  vs. [4](#0-3) .

`SortitionStateVersion::from_protocol_version` selects V1 for any protocol version below `GLOBAL_SIGNER_STATE_ACTIVATION_VERSION`, so any signer set (or any signer that has not yet activated V2) still runs exclusively through this weaker V1 path: [5](#0-4) .

### Impact Explanation
Because V1's guard is scoped to "globally accepted" rather than "signed by me," a V1 signer that has already locally signed one tenure-start block (state `Accepted`/`LocallyAccepted`, not yet `GloballyAccepted`) will pass `get_last_globally_accepted_block(&tenure_consensus_hash)` returning `None` a second time, and will therefore sign a second, conflicting tenure-change block for the same tenure/height if a miner (or reorg-driven proposal flow) presents one before the first block reaches global acceptance. This is a direct equivocation: the same signer places valid signatures on two conflicting blocks for the same tenure position, breaking the "one accepted block per height/tenure-start" invariant the guard is meant to enforce (matches the "Critical" impact class: a signer signing a conflicting block).

### Likelihood Explanation
This requires only a single miner/proposer (in scope: "a one-slot miner plus gossip") re-proposing a second tenure-change block for the same sortition/tenure before the first proposal's signature set reaches the global-acceptance threshold — a normal, low-latency race that can occur during ordinary block-production cadence (not a majority-signer or key-compromise scenario), since it exploits the gap between "I signed it" and "network reached threshold on it," a gap that always exists during normal operation.

### Recommendation
Change V1's `validate_tenure_change_payload` to use the same predicate as V2 — `SignerDb::get_last_signed_block` (or the tenure-scoped "have I signed anything in this tenure" query) instead of `get_last_globally_accepted_block` — so both chainstate versions enforce the guard against the same threat: any block this signer has already signed, not merely one the whole network has already ratified. Alternatively, centralize the check in `mod.rs`'s shared `SortitionData`/`SignerDb` layer so V1 and V2 cannot diverge again, mirroring the "promote to a shared module" remediation used for the analogous UTCP report.

### Proof of Concept
1. Run a signer under a protocol version `< GLOBAL_SIGNER_STATE_ACTIVATION_VERSION` so `SortitionStateVersion::V1` is active (`stacks-signer/src/chainstate/mod.rs:532-548`).
2. Miner proposes Block A: a tenure-change block for tenure `X` (parent tenure `Y`). `check_proposal` → `validate_tenure_change_payload` calls `get_last_globally_accepted_block(X)` → `None` (first block in tenure) → passes; signer signs Block A. Block A's state is now `Accepted`/`LocallyAccepted` in `SignerDb`, not yet `GloballyAccepted` (network threshold not yet reached).
3. Before Block A reaches global acceptance, the same or a colluding miner proposes Block B: a different, conflicting tenure-change block also targeting tenure `X` (same `consensus_hash`, same sortition winner, different transaction set/parent commit).
4. `check_proposal` runs again for Block B; `validate_tenure_change_payload` calls `get_last_globally_accepted_block(X)` again — still `None`, because Block A has not (yet) crossed the global-acceptance threshold — so the `DuplicateBlockFound` rejection at [6](#0-5)  never fires, and the signer proceeds to sign Block B.
5. The signer has now produced two valid signatures over conflicting blocks for the same tenure-start position — an equivocation that V2's `get_last_signed_block`-based check (`stacks-signer/src/chainstate/v2.rs:344-357`) would have prevented.

### Citations

**File:** stacks-signer/src/chainstate/v2.rs (L176-184)
```rust
        if let Some(tenure_change) = block.get_tenure_change_tx_payload() {
            Self::validate_tenure_change_payload(
                tenure_change,
                block,
                parent_tenure_id,
                signer_db,
                client,
                &self.config,
            )?;
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

**File:** stacks-signer/src/chainstate/v1.rs (L319-327)
```rust
        if let Some(tenure_change) = block.get_tenure_change_tx_payload() {
            self.validate_tenure_change_payload(
                &proposed_by,
                tenure_change,
                block,
                signer_db,
                client,
            )?;
        } else {
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

**File:** stacks-signer/src/chainstate/mod.rs (L532-548)
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
    /// Uses global state version
    pub fn uses_global_state(&self) -> bool {
        match self {
            Self::V1 => false,
            Self::V2 => true,
        }
    }
}
```
