Confirmed. The double-dedup guard mismatch is real: the `Accepted` branch dedups on `block.gathered_signatures.contains_key(&slot_id)` [1](#0-0)  while the `Rejected` branch dedups on the separate `block.responded_signers.insert(slot_id)` set [2](#0-1) , and the coordinator consumes both counters as if they partition total signer weight [3](#0-2) [4](#0-3) .

### Title
Coordinator double-counts a flip-flopping signer's weight across `total_weight_approved` and `total_weight_rejected`, wedging valid-block acceptance - (File: `stacks-node/src/nakamoto_node/stackerdb_listener.rs`)

### Summary
`StackerDBListener` maintains two independent weight tallies for a proposed block, `total_weight_approved` and `total_weight_rejected`, which `SignCoordinator` treats as mutually exclusive partitions of `self.total_weight` when deciding whether to accept or reject a block [5](#0-4) . The dedup guard for each tally is keyed off a *different* set: the acceptance path checks `gathered_signatures` [6](#0-5) , while the rejection path checks the separate `responded_signers` set [2](#0-1) . A single signer that first rejects and later accepts the same block (a legitimate, allowed opinion change, since `reject_then_accept`/`accept_then_reject` semantics are only enforced signer-side in `SignerDb`, not at this listener) has its weight counted into `total_weight_rejected` on the first message and — because `gathered_signatures` was empty for that slot — counted again into `total_weight_approved` on the second message. Its stale rejection weight is never retracted.

### Finding Description
The intended invariant is that `total_weight_approved + total_weight_rejected <= self.total_weight`, i.e. each signer's weight contributes to at most one side. This is exactly analogous to the `VaultTracker.transferNotionalFrom` bug: two logically-linked ledgers (`from`/`to` balances there; `approved`/`rejected` weight tallies here) are updated using two separate, non-synchronized "already counted" checks, so the same identity (the signer's `slot_id`) can be credited on both sides without ever being debited from either, inflating the sum beyond what should be possible.

Sequence:
1. Signer S (slot `k`, weight `w`) sends `BlockResponse::Rejected` for block `B`. `responded_signers.insert(k)` succeeds → `total_weight_rejected += w` [2](#0-1) .
2. S changes its mind (a normal, allowed re-evaluation per the signer's own state machine) and sends `BlockResponse::Accepted` for the same `B`. The acceptance path checks `gathered_signatures.contains_key(&k)`, which is still empty for `k` → `total_weight_approved += w` [1](#0-0) .
3. S's weight `w` is now present in both `total_weight_rejected` and `total_weight_approved`. There is no path in this file that removes `w` from `total_weight_rejected` when S later accepts.

### Impact Explanation
Because the coordinator's rejection check runs first and is order-sensitive [7](#0-6) , a phantom/stale rejection weight that a signer has already retracted can push `total_weight_rejected + weight_threshold > total_weight` even while genuinely-collected signatures in `gathered_signatures` have already reached the real 70% threshold. The miner then aborts a validly, sufficiently-signed block as `SignersRejected` instead of using the signatures it already legitimately collected — a liveness wedge on an otherwise-valid, canonical block. This matches the "High" bucket: a signer's/coordinator's bookkeeping causes the block-production path to wedge and never conclude on a valid block due to miscounted (stale) rejection weight that should have been superseded by the same signer's later acceptance.

### Likelihood Explanation
This requires only one signer (well within the "one-slot miner/signer" scope) sending two ordinary StackerDB messages — a rejection followed later by an acceptance for the same block — which is a normal occurrence (e.g., a signer that initially rejects due to a stale view, then reconsiders and accepts after conditions change, similar to the `signers_reprocess_bitcoin_block_not_found_proposals` scenario referenced in the test suite [8](#0-7) ). No majority collusion, no key compromise, and no auth token access are required; a lone honest-but-reconsidering, or a lone Byzantine, signer can trigger it via ordinary gossip.

### Recommendation
Use a single, unified per-slot "final vote" map (or clear the prior tally when a signer's vote changes) so that a signer's weight is moved from `total_weight_rejected` to `total_weight_approved` (or vice versa) rather than added to both. Concretely, in the `Accepted` branch, check/clear membership in `responded_signers`/rejection weight before adding to `total_weight_approved` (and symmetrically in `Rejected`, subtract any weight already present in `gathered_signatures`), guaranteeing `total_weight_approved + total_weight_rejected <= self.total_weight` at all times.

### Proof of Concept
1. Start a miner tenure with a proposed block `B` and `insert_block(&B.header)` initializing zero tallies [9](#0-8) .
2. Have signer S (weight `w`, chosen so that `total_weight_rejected + w + weight_threshold > total_weight` after step 3) broadcast `SignerMessageV0::BlockResponse(BlockResponse::Rejected(...))` for `B`'s sighash — `total_weight_rejected` increases by `w`.
3. Have the remaining signers broadcast enough `Accepted` messages so that `total_weight_approved` (excluding S) reaches `weight_threshold` (70%) on its own.
4. Have S now broadcast `SignerMessageV0::BlockResponse(BlockResponse::Accepted(...))` for the same `B`. `gathered_signatures` did not contain S's slot, so `total_weight_approved += w` too — `total_weight_rejected` is unchanged and still holds S's stale `w`.
5. In `SignCoordinator`'s wait loop, `total_weight_rejected.saturating_add(weight_threshold) > total_weight` may now be true (since it still includes S's stale rejection) and is checked before the approval branch, causing `Err(NakamotoNodeError::SignersRejected { .. })` to be returned even though genuinely-collected `gathered_signatures` already meets the real signing threshold [5](#0-4) .

### Citations

**File:** stacks-node/src/nakamoto_node/stackerdb_listener.rs (L443-464)
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
```

**File:** stacks-node/src/nakamoto_node/stackerdb_listener.rs (L515-518)
```rust
                        if block.responded_signers.insert(slot_id) {
                            block.total_weight_rejected = block
                                .total_weight_rejected
                                .saturating_add(signer_entry.weight);
```

**File:** stacks-node/src/nakamoto_node/stackerdb_listener.rs (L693-704)
```rust
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

**File:** stacks-node/src/tests/signer/v0/reprocess_block_proposals.rs (L38-45)
```rust
/// Test Execution:
/// 1. Propose a block to all signers
/// 2. Pause bitcoin block processing on the node connect to the two signers (miner 2) to simulate the condition where the block proposal is received before the Bitcoin block is fully processed
/// 3. 3 signers on miner 1 issue pre-commits
/// 4. 2 signers on miner 2 issue a rejection due to the missing Bitcoin block
/// 5. Resume Bitcoin block processing
/// 6. Confirm the two miners on miner 2 reconsider the block proposal and issue pre-commits
/// 7. Confirm the block is accepted the node advances its tip.
```
