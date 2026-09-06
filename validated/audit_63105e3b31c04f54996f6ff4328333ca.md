### Title
Miner's per-block signer-weight tally double-counts a signer who switches from Rejected to Accepted, corrupting the aggregated-weight vs. verified-accepts equality - (File: stacks-node/src/nakamoto_node/stackerdb_listener.rs)

### Summary
`StackerDBListener` (the node/miner-side coordinator that tallies signer responses for a block proposal) tracks `total_weight_approved` and `total_weight_rejected` in a shared `BlockStatus` guarded by a single `responded_signers: HashSet<slot_id>` used to prevent double-adding weight *within* the same response type. However, `responded_signers` is shared across both the `Accepted` and `Rejected` match arms, and neither arm removes the signer's weight from the *other* tally when a signer later changes its vote. A single signer legitimately re-evaluating a block from `LocallyRejected` to `LocallyAccepted` (a transition explicitly supported by the signer's own state machine, see `stacks-signer/src/signerdb.rs` `check_state`/`move_to` and `docs/signer-flows.md`'s `LocallyRejected --> LocallyAccepted : re-evaluated`) causes the miner to retain that signer's weight in `total_weight_rejected` forever for that block while *also* adding it to `total_weight_approved`.

### Finding Description
In `handle_block_pre_commit`/vote flow, a signer can rescind a prior rejection and sign after re-evaluating a proposal (this is a normal, sanctioned code path: `stacks-signer/src/signerdb.rs` allows `LocallyRejected -> LocallyAccepted` via `check_state`, and `add_block_signature` on the signer side even deletes the prior rejection row from `block_rejection_signer_addrs`, showing the protocol design intends reject→accept transitions to fully supersede the earlier vote).

On the miner/coordinator side (`stacks-node/src/nakamoto_node/stackerdb_listener.rs`), the two message arms are:

- `BlockResponse::Rejected`: 
```
if block.responded_signers.insert(slot_id) {
    block.total_weight_rejected = block.total_weight_rejected.saturating_add(signer_entry.weight);
    ...
}
``` [1](#0-0) 

- `BlockResponse::Accepted`:
```
if !block.gathered_signatures.contains_key(&slot_id) {
    block.total_weight_approved = block.total_weight_approved.saturating_add(signer_entry.weight);
    ...
}
block.gathered_signatures.insert(slot_id, signature);
block.responded_signers.insert(slot_id);
``` [2](#0-1) 

Both arms guard weight addition using different maps: the reject arm guards on `responded_signers`, the accept arm guards on `gathered_signatures`. Since these are two separate collections that are never reconciled against each other, if signer `S` first sends `Rejected` for block `B` (adding weight to `total_weight_rejected` and inserting `S`'s slot into `responded_signers`), and later sends `Accepted` for the same `B` (a legitimate re-evaluation), the accept arm sees `S`'s slot is *not* in `gathered_signatures` yet, so it adds `S`'s weight to `total_weight_approved` as well — without ever subtracting it back out of `total_weight_rejected`. `BlockStatus::insert_block` only zeroes these counters when a *new* block proposal is inserted, and `reset_rejections` (used on proposal timeout) explicitly states rejections are cleared but approvals are preserved, with no code path clearing `total_weight_rejected` when an individual signer's vote flips to accept.

The result: `total_weight_approved + total_weight_rejected` can exceed `self.total_weight` for the same proposal, breaking the invariant that the miner's in-memory aggregated weight ledger must equal the sum of genuinely distinct, live verified-accept/verified-reject decisions. This is the direct analog of the reported bug class: a counter (`generationMintCounts` in the original report; `total_weight_rejected` here) that is not corrected/reset when the underlying state it represents changes, producing a mismatch between what actually happened (this signer now accepts) and what the aggregate state reflects (still counted as a rejector).

### Impact Explanation
`get_block_status` in `stacks-node/src/nakamoto_node/signer_coordinator.rs` uses `total_weight_rejected` to decide whether to abandon the current proposal as rejected (`total_weight_rejected.saturating_add(weight_threshold) > total_weight`) versus using `total_weight_approved` to decide acceptance (`total_weight_approved >= weight_threshold`). Because a single signer's weight can be simultaneously counted in both ledgers, the miner can reach `SignersRejected` (treating the proposal as rejected, discarding it and excluding transactions) purely from stale/superseded rejections that the signer itself has already withdrawn by accepting — i.e., a rejection is retained and can help trip the rejection threshold even after being effectively "recounted as an accept" by the same signer. This directly matches the report's "rejection recounted as accept" impact class, but manifests here as the opposite failure of accounting integrity (double counting one weight into both buckets) rather than corruption of block-level consensus records; it is confined to the miner's ephemeral, per-proposal `BlockStatus` in `stackerdb_listener.rs`/`signer_coordinator.rs`, which is discarded and rebuilt via `insert_block` on the next proposal attempt. It does not corrupt any persisted signer state or SignerDB record, and it cannot cause a signer to sign an invalid/non-canonical block, nor grant a cross-context-valid signature, nor by itself wedge the state machine across tenures.

### Likelihood Explanation
Reachable by any single signer (weight ≥1) that changes its vote from reject to accept for the same proposal — a normal, sanctioned re-evaluation flow that the signer-side state machine explicitly supports. No majority collusion, private-key access, or malicious behavior is required; it can happen during ordinary operation whenever a signer's local chain-state view changes between an initial rejection and a later validated acceptance of the identical proposal.

### Recommendation
When processing `BlockResponse::Accepted`, if the signer's slot is already present in `responded_signers` from a prior rejection, subtract that signer's weight from `total_weight_rejected` (and analogously, when processing `BlockResponse::Rejected`, subtract from `total_weight_approved` if the slot is already in `gathered_signatures`) before adding to the new bucket, so a signer's weight is attributed to exactly one bucket at a time, restoring the invariant `total_weight_approved + total_weight_rejected + (unresponded weight) == total_weight`.

### Proof of Concept
1. Miner proposes block `B`; `StackerDBListenerComms::insert_block` initializes `total_weight_approved = 0`, `total_weight_rejected = 0`, empty `responded_signers`/`gathered_signatures` [3](#0-2) .
2. Signer `S` (weight `w`) sends `BlockResponse::Rejected` for `B` (e.g., stale chainstate view). Listener executes the `Rejected` arm: `responded_signers.insert(slot_id)` succeeds, `total_weight_rejected += w` [1](#0-0) .
3. `S` subsequently re-evaluates and legitimately signs/accepts `B` (protocol-sanctioned `LocallyRejected -> LocallyAccepted` transition, per signer-side `check_state`) and gossips `BlockResponse::Accepted`.
4. Listener executes the `Accepted` arm: since `S`'s slot is not in `gathered_signatures`, `total_weight_approved += w`, then `gathered_signatures.insert(slot_id, signature)` and `responded_signers.insert(slot_id)` (no-op, already present) [2](#0-1) .
5. Now `total_weight_rejected` still includes `w` from step 2 (never decremented) and `total_weight_approved` also includes `w` from step 4 — `S`'s weight is counted in both ledgers simultaneously, so `total_weight_approved + total_weight_rejected` can exceed `total_weight`, corrupting the equality the coordinator relies on in `get_block_status` (`stacks-node/src/nakamoto_node/signer_coordinator.rs`) to decide accept vs. reject for the proposal [4](#0-3) .

### Citations

**File:** stacks-node/src/nakamoto_node/stackerdb_listener.rs (L443-465)
```rust
                        if !block.gathered_signatures.contains_key(&slot_id) {
                            block.total_weight_approved = block
                                .total_weight_approved
                                .saturating_add(signer_entry.weight);

                            info!("StackerDBListener: Signature Added to block";
                                "signer_signature_hash" => %block_sighash,
                                "signer_pubkey" => signer_pubkey.to_hex(),
                                "signer_slot_id" => slot_id,
                                "signature" => %signature,
                                "signer_weight" => signer_entry.weight,
                                "total_weight_approved" => block.total_weight_approved,
                                "percent_approved" => block.total_weight_approved as f64 / self.total_weight as f64 * 100.0,
                                "total_weight_rejected" => block.total_weight_rejected,
                                "percent_rejected" => block.total_weight_rejected as f64 / self.total_weight as f64 * 100.0,
                                "weight_threshold" => self.weight_threshold,
                                "tenure_extend_timestamp" => tenure_extend_timestamp,
                                "read_count_extend_timestamp" => read_count_extend_timestamp,
                                "server_version" => metadata.server_version,
                            );
                        }
                        block.gathered_signatures.insert(slot_id, signature);
                        block.responded_signers.insert(slot_id);
```

**File:** stacks-node/src/nakamoto_node/stackerdb_listener.rs (L515-518)
```rust
                        if block.responded_signers.insert(slot_id) {
                            block.total_weight_rejected = block
                                .total_weight_rejected
                                .saturating_add(signer_entry.weight);
```

**File:** stacks-node/src/nakamoto_node/stackerdb_listener.rs (L691-704)
```rust
impl StackerDBListenerComms {
    /// Insert a block into the block status map with initial values.
    pub fn insert_block(&self, block: &NakamotoBlockHeader) {
        let (lock, _cvar) = &*self.blocks;
        let mut blocks = lock.lock().expect("FATAL: failed to lock block status");
        let block_status = BlockStatus {
            responded_signers: HashSet::new(),
            gathered_signatures: BTreeMap::new(),
            total_weight_approved: 0,
            total_weight_rejected: 0,
            failed_txids: HashMap::new(),
        };
        blocks.insert(block.signer_signature_hash(), block_status);
    }
```

**File:** stacks-node/src/nakamoto_node/signer_coordinator.rs (L509-545)
```rust
            if block_status
                .total_weight_rejected
                .saturating_add(self.weight_threshold)
                > self.total_weight
            {
                info!(
                    "{}/{} signer weight votes to reject block",
                    block_status.total_weight_rejected, self.total_weight;
                    "signer_signature_hash" => %block_signer_sighash,
                );
                counters.bump_naka_rejected_blocks();

                // Only act on failed txids that a blocking minority (>30% weight) agrees on
                let blocking_minority = self.total_weight.saturating_sub(self.weight_threshold);
                let mut temporarily_excluded_txids = HashSet::new();
                let mut permanently_excluded_txids = HashSet::new();
                for (txid, info) in &block_status.failed_txids {
                    if info.total_weight > blocking_minority {
                        // Do not perma ban txids that only a small minority of signers reported as problematic
                        // But make sure its removed from the next block proposal
                        if info.problematic_weight > blocking_minority {
                            permanently_excluded_txids.insert(txid.clone());
                        } else {
                            temporarily_excluded_txids.insert(txid.clone());
                        }
                    }
                }

                return Err(NakamotoNodeError::SignersRejected {
                    temporarily_excluded_txids,
                    permanently_excluded_txids,
                });
            } else if block_status.total_weight_approved >= self.weight_threshold {
                info!("Received enough signatures, block accepted";
                    "signer_signature_hash" => %block_signer_sighash,
                );
                return Ok(block_status.gathered_signatures.values().cloned().collect());
```
