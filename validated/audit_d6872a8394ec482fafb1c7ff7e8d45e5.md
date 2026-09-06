### Title
Single malformed/late `slot_id` in a signer's StackerDB message permanently kills the `StackerDBListener` thread, wedging the miner's signature-collection loop for the rest of the tenure - (File: `stacks-node/src/nakamoto_node/stackerdb_listener.rs`)

### Summary
`StackerDBListener::run()` is the miner-side analog of `_getCreatorPaymentInfo()`: it is meant to be a defensive dispatcher that tolerates malformed/unexpected input from an unbounded, gossip-fed message stream and keep running so the coordinator can keep collecting signatures. Instead, one unrecognized `slot_id` inside the per-message loop returns a hard `Err`, which is not "caught" anywhere — it unwinds out of the spawned listener thread and permanently kills it for the remainder of the tenure, wedging the block-signing pipeline exactly like the original report's un-caught revert wedges NFTs in escrow.

### Finding Description
`StackerDBListener::run()` iterates over an unbounded batch of `messages` decoded from a StackerDB event (built from `SignerEvent::SignerMessages` in `libsigner/src/events.rs`, which itself just filters chunks by payload type and successfully-recoverable signature — it does **not** cross-check that the recovered signer actually owns `chunk.slot_id`): [1](#0-0) 

Inside the miner's dispatch loop, each message's `slot_id` is looked up against `self.signer_entries`, a `HashMap` built once at `StackerDBListener::new()` time from the miner's locally-cached `reward_set`: [2](#0-1) 

If `signer_entries.get(&slot_id)` returns `None` — which happens whenever the miner's cached `signer_entries` map (built once at thread-start from a `reward_set` snapshot) disagrees with the `slot_id` a signer is currently, legitimately writing under (e.g. after a reward-set/`.signers`-contract slot reassignment such as `pox_5_compute_and_update_signers`/`update_signers`, or any other transient mismatch between the miner's snapshot and the live StackerDB slot assignment) — the function returns `Err(NakamotoNodeError::SignerSignatureError(...))` **from inside the loop over an unbounded, externally-supplied message list**, exactly the failure mode the report calls out: a `try`/defensive dispatcher whose per-item error is not actually caught, so the whole operation aborts instead of just skipping the bad item (contrast with the many other `continue`-on-error branches in the same function, e.g. lines 400-409, 411-426, which show the intended defensive pattern was to skip and continue).

That `Err` propagates straight out of `run()`, is only logged, and the thread exits: [3](#0-2) 

There is no restart/respawn logic for this thread anywhere in `SignerCoordinator`; `listener_thread` is only ever joined during `shutdown()`. Once it dies, `self.blocks` and `self.global_state_evaluator` (shared via `StackerDBListenerComms`) stop being updated for every subsequent block proposal in the tenure.

### Impact Explanation
This breaks liveness for the remainder of the tenure: the coordinator's block-status polling loop keeps spinning on stale weight counters that will never again increase, because the thread that updates them is dead: [4](#0-3) 

Every subsequent block proposed by this miner in this tenure will time out waiting for signatures/rejections it can never observe (`SignatureTimeout`), even though a healthy quorum of honest signers may in fact be responding correctly on StackerDB. This matches the report's High-impact class: "a signer/miner wedged into never signing/collecting valid responses" — here the miner-side collector is wedged, not a single signer's own state machine, but the practical effect (stalled tenure, no further blocks signed) is the same category of liveness failure the rules call for.

### Likelihood Explanation
The trigger requires only one signer's (or a gossiped/relayed) StackerDB chunk whose `slot_id` the miner's locally-cached `signer_entries` map doesn't recognize — no majority collusion, no compromised keys, and no access to the auth token or local node access is needed. Whether this specific mismatch (miner's `reward_set` snapshot vs. the live `.signers` StackerDB slot table) can be forced deterministically by a single one-slot participant (e.g. by timing a proposal around a reward-set recomputation/reorg boundary) was not something I could fully trace end-to-end in the available code/time — the slot assignment and reward-set-fetch code paths (`stackerdb-set-signer-slots`, `update_signers`, `pox_5_compute_and_update_signers`) are consistent by construction in the common case, so this is most reliably triggered by any transient staleness between the two snapshots rather than by pure malice on a fixed, static reward set. I flag this uncertainty explicitly rather than asserting a guaranteed reproduction.

### Recommendation
In the `messages.into_iter()` loop in `stackerdb_listener.rs`, treat an unrecognized `slot_id` the same defensive way every other malformed-input branch in this function is already handled: `continue` (optionally logging a `warn!`) instead of returning `Err`, so one bad/stale message cannot terminate the whole listener thread. If the underlying cause is snapshot staleness, additionally consider making `SignerCoordinator` detect a dead `listener_thread` and either respawn it with a fresh `reward_set`/slot mapping or fail the tenure attempt cleanly rather than silently spinning until `SignatureTimeout`.

### Proof of Concept
1. Miner starts a tenure; `StackerDBListener::new()` snapshots `reward_set` into `signer_entries` (indices 0..N-1).
2. Any signer (or a relayed/gossiped chunk) writes a `BlockResponse` message to a StackerDB `slot_id` that is not present in the miner's cached `signer_entries` (e.g., due to a reward-set/slot-table update the miner's snapshot predates, or any decode path that yields a slot index outside the cached map).
3. `StackerDBListener::run()`'s loop at `stackerdb_listener.rs:372-377` hits the `None` branch and returns `Err(NakamotoNodeError::SignerSignatureError(...))`.
4. The spawned thread in `signer_coordinator.rs:174-178` logs the error and exits; no restart occurs.
5. `self.blocks`/`self.global_state_evaluator` are frozen; `SignerCoordinator`'s waiting loop (`signer_coordinator.rs:482-561`) can no longer observe new approvals/rejections and every subsequent block proposal in the tenure times out via `SignatureTimeout`, wedging the miner despite a healthy signer quorum.

### Citations

**File:** libsigner/src/events.rs (L580-614)
```rust
            let messages: Vec<_> = event
                .modified_slots
                .iter()
                .filter_map(|chunk| {
                    // Accept only payloads whose type is valid for this contract's message id.
                    let &type_byte = chunk.data.first()?;
                    let payload_kind = SignerMessageTypePrefix::from_u8(type_byte)?;
                    if !signer_message_payload_matches_lane(payload_kind, message_id) {
                        warn!(
                            "Skipping signer chunk with unexpected payload type for contract";
                            "contract" => %event.contract_id,
                            "lane_message_id" => message_id,
                            "payload_type_prefix" => type_byte,
                        );
                        return None;
                    }
                    let Ok(pk) = chunk.recover_pk() else {
                        warn!(
                            "Skipping signer chunk: signature recovery failed";
                            "contract" => %event.contract_id,
                            "slot_id" => chunk.slot_id,
                        );
                        return None;
                    };
                    let Ok(message) = read_next::<T, _>(&mut &chunk.data[..]) else {
                        warn!(
                            "Skipping signer chunk: payload deserialization failed";
                            "contract" => %event.contract_id,
                            "slot_id" => chunk.slot_id,
                        );
                        return None;
                    };
                    Some((chunk.slot_id, pk, message))
                })
                .collect();
```

**File:** stacks-node/src/nakamoto_node/stackerdb_listener.rs (L372-383)
```rust
            for (slot_id, _pk, message) in messages.into_iter() {
                let Some(signer_entry) = &self.signer_entries.get(&slot_id) else {
                    return Err(NakamotoNodeError::SignerSignatureError(
                        "Signer entry not found".into(),
                    ));
                };
                let Ok(signer_pubkey) = StacksPublicKey::from_slice(&signer_entry.signing_key)
                else {
                    return Err(NakamotoNodeError::SignerSignatureError(
                        "Failed to parse signer public key".into(),
                    ));
                };
```

**File:** stacks-node/src/nakamoto_node/signer_coordinator.rs (L169-187)
```rust
        let listener_thread = std::thread::Builder::new()
            .name(format!(
                "stackerdb_listener_{}",
                election_block.block_height
            ))
            .spawn(move || {
                if let Err(e) = listener.run() {
                    error!("StackerDBListener: exited with error: {e:?}");
                }
            })
            .map_err(|e| {
                error!("Failed to spawn stackerdb_listener thread: {e:?}");
                ChainstateError::MinerAborted
            })?;

        sc.listener_thread = Some(listener_thread);

        Ok(sc)
    }
```

**File:** stacks-node/src/nakamoto_node/signer_coordinator.rs (L509-561)
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
            } else if rejections_timer.elapsed() > *rejections_timeout {
                warn!("Timed out while waiting for responses from signers";
                    "elapsed" => rejections_timer.elapsed().as_secs(),
                    "rejections_timeout" => rejections_timeout.as_secs(),
                    "rejections" => rejections,
                    "rejections_threshold" => self.total_weight.saturating_sub(self.weight_threshold)
                );

                // Reset the rejections in the stackerdb listener
                self.stackerdb_comms.reset_rejections(block_signer_sighash);

                return Err(NakamotoNodeError::SignatureTimeout);
            } else {
                continue;
            }
        }
```
