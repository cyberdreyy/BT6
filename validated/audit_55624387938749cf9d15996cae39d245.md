Confirmed: `StackerDBChannel::is_active` (`stacks-node/src/event_dispatcher/stacker_db.rs:104-124`) routes **any** StackerDB chunk event whose contract name starts with `SIGNERS_NAME` and is a boot contract to whichever thread currently holds the miner-coordinator receiver — it does not filter by exact reward-cycle contract instance. Filtering to the "right" cycle is left entirely to `StackerDBListener::run`'s own `signer_set != self.signer_set` check, which only compares reward-cycle parity (0/1), not the actual reward-cycle ID.

### Title
Single stale/adjacent-cycle signer chunk crashes the miner's `StackerDBListener`, wedging block-signature aggregation - ([File: stacks-node/src/nakamoto_node/stackerdb_listener.rs])

### Summary
`StackerDBListener::run` terminates the entire listener thread with `Err(...)` the moment it encounters a `slot_id` that is not present in its `signer_entries` map, instead of skipping the single malformed/unexpected message and continuing — the same "any-exception-tears-down-the-channel" anti-pattern flagged in the SIPSorcery advisory.

### Finding Description
Any StackerDB chunk written to a `.signer-*` boot contract that shares the current tenure's `signer_set` parity (0 or 1) is forwarded to the coordinator's event loop, per `StackerDBChannel::is_active` [1](#0-0) . `StackerDBListener::run` only rejects events whose `signer_set` parity differs from `self.signer_set` [2](#0-1) ; it does not check the actual reward-cycle number embedded in the contract name/slot mapping. Because parity repeats every other cycle, a chunk written under a different-but-same-parity reward cycle (with a different/smaller `signer_entries` set) — or any chunk whose `slot_id` otherwise falls outside the currently loaded `signer_entries` map — is accepted into `messages` and then indexed:

```
for (slot_id, _pk, message) in messages.into_iter() {
    let Some(signer_entry) = &self.signer_entries.get(&slot_id) else {
        return Err(NakamotoNodeError::SignerSignatureError(
            "Signer entry not found".into(),
        ));
    };
``` [3](#0-2) 

This mirrors the SIPSorcery root cause exactly: a single, cheaply-producible message (one signer's/miner's chunk, no majority needed) triggers an unhandled-error path that the surrounding loop converts into total session teardown, rather than a per-message drop-and-continue. Here `run()` returning `Err` propagates out of the thread spawned for the `StackerDBListener` and kills it, exactly as `UdpReceiver.EndReceiveFrom`'s catch-all `Close()` killed the RTP channel on one bad packet.

### Impact Explanation
Once the `StackerDBListener` thread dies, the miner/coordinator stops tracking `BlockResponse` accept/reject weights (`self.blocks`), idle timestamps, read-count timestamps, and the `GlobalStateEvaluator` for the entire tenure — `total_weight_approved` can never again reach `weight_threshold` because no more signatures are ingested [4](#0-3) . This is a liveness wedge: the miner can no longer assemble enough signatures to produce a signed block for the remainder of the tenure, even though a legitimate quorum of signers is willing to sign. This matches the "High" impact bucket: a coordinator wedged such that valid signer responses are no longer acted upon.

### Likelihood Explanation
No majority or privileged access is required — the trigger is a normal StackerDB write from a single participant (any signer, or a leftover/gossiped chunk from an adjacent same-parity reward cycle) landing on a `slot_id` the currently-running listener's `signer_entries` doesn't recognize. Because `signer_entries` is fixed at listener-construction time from a specific reward set [5](#0-4) , any transient mismatch between the delivered event's originating cycle and the listener's expected cycle (allowed because only parity is checked) is enough.

### Recommendation
In the loop at `stacks-node/src/nakamoto_node/stackerdb_listener.rs:372-377`, replace the `return Err(...)` on an unrecognized `slot_id` with a `warn!` + `continue`, so a single out-of-range/unexpected chunk is dropped rather than terminating the listener thread. Additionally, tighten the cycle-membership check at line 356 to compare the full reward-cycle identifier (not just parity) before accepting a `SignerEvent` for processing.

### Proof of Concept
1. Node starts tenure for reward cycle `N`, instantiating `StackerDBListener` with `signer_set = N % 2` and `signer_entries` built from cycle `N`'s reward set (size `k`).
2. A StackerDB chunk is written (by any signer, or replicated/gossiped) to a `.signer-*` boot contract belonging to reward cycle `N+2` (same parity) whose reward set has more slots (`k' > k`), using `slot_id = k` (valid for `N+2`, invalid for `N`).
3. `StackerDBChannel::is_active` forwards this chunk because it matches `SIGNERS_NAME`/boot-contract regardless of exact cycle.
4. `SignerEvent::try_from` deserializes it into a `SignerMessage` and passes the `signer_set` parity check.
5. `self.signer_entries.get(&k)` returns `None`, hitting `return Err(NakamotoNodeError::SignerSignatureError(...))`, which terminates `StackerDBListener::run`, ending signature aggregation for the remainder of cycle `N`'s tenure.

### Citations

**File:** stacks-node/src/event_dispatcher/stacker_db.rs (L104-123)
```rust
    pub fn is_active(
        &self,
        stackerdb: &QualifiedContractIdentifier,
    ) -> Option<Sender<StackerDBChunksEvent>> {
        // if the receiver field is empty (i.e., None), then there is no listening thread, return None
        let guard = self
            .sender_info
            .lock()
            .expect("FATAL: poisoned StackerDBChannel lock");
        let sender_info = guard.as_ref()?;
        if sender_info.interested_in_signers
            && stackerdb.is_boot()
            && stackerdb.name.starts_with(SIGNERS_NAME)
        {
            return Some(sender_info.sender.clone());
        }
        if sender_info.other_interests.contains(stackerdb) {
            return Some(sender_info.sender.clone());
        }
        None
```

**File:** stacks-node/src/nakamoto_node/stackerdb_listener.rs (L227-239)
```rust
        let signer_entries = reward_set_signers
            .iter()
            .cloned()
            .enumerate()
            .map(|(idx, signer)| {
                let Ok(slot_id) = u32::try_from(idx) else {
                    return Err(ChainstateError::InvalidStacksBlock(
                        "Signer index exceeds u32".into(),
                    ));
                };
                Ok((slot_id, signer))
            })
            .collect::<Result<HashMap<_, _>, ChainstateError>>()?;
```

**File:** stacks-node/src/nakamoto_node/stackerdb_listener.rs (L356-361)
```rust
            if signer_set != self.signer_set {
                debug!(
                    "StackerDBListener: Received signer event for other reward cycle. Ignoring."
                );
                continue;
            };
```

**File:** stacks-node/src/nakamoto_node/stackerdb_listener.rs (L372-377)
```rust
            for (slot_id, _pk, message) in messages.into_iter() {
                let Some(signer_entry) = &self.signer_entries.get(&slot_id) else {
                    return Err(NakamotoNodeError::SignerSignatureError(
                        "Signer entry not found".into(),
                    ));
                };
```

**File:** stacks-node/src/nakamoto_node/stackerdb_listener.rs (L443-470)
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

                        if block.total_weight_approved >= self.weight_threshold {
                            // Signal to anyone waiting on this block that we have enough signatures
                            cvar.notify_all();
                        }
```
