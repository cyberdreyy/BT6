### Title
Signer caches PoX-5 reward-set weights at reward-cycle registration and never refreshes them for the lifetime of that cycle, while the node re-derives weights live from mutable PoX-5 stake — ([File: stacks-signer/src/v0/signer.rs])

### Summary
The external report's bug class is "internal accounting is snapshotted once and never reconciled with an underlying value that keeps changing," causing the tracked value to permanently diverge from ground truth. The `stacks-signer` analog is the per-reward-cycle `signer_weights` map: it is captured once from the reward set at `Signer::new` and used for the rest of that cycle to decide local pre-commit/accept/reject thresholds, while PoX-5's `.signers`/`.pox-5` staking bookkeeping is designed to let stacked amounts (and therefore signer weight) change mid-cycle via delegated stack-amount add/remove paths, and the node always re-derives the *true* weight fresh at verification time.

### Finding Description
Each `Signer` instance snapshots signing weight exactly once at construction: [1](#0-0) 

That `signer_weights: HashMap<StacksAddress, u32>` is never mutated afterward and feeds every local threshold decision for the cycle: pre-commit tallying, rejection tallying, and acceptance tallying all call `compute_signature_signing_weight` / `compute_signature_total_weight`, which read from this frozen snapshot: [2](#0-1) [3](#0-2) [4](#0-3) 

The runloop only replaces a `ConfiguredSigner` (and therefore its `signer_weights`) when the reward-cycle *number* it is configured for changes; it never re-derives it because the underlying stake for the *same* reward cycle moved: [5](#0-4) [6](#0-5) 

Meanwhile, PoX-5's stacking model (unlike the fixed-at-cycle-boundary PoX-1..4 model) maintains per-cycle signer membership and delegated amounts that can be incremented or decremented while the cycle is active — crossing the `SIGNER_SET_MIN_USTX` threshold triggers `add-signer-to-set-for-cycle`/removal from the set and adjusts `total-shares-staked-for-cycle`, all keyed by the *current* `reward-cycle`: [7](#0-6) [8](#0-7) 

The node-side authority (`verify_signer_signatures`, used to actually admit a block into the chain) always computes `total_weight` and per-signer weight from the `RewardSet` it loads fresh for that block/height, not from any per-signer cached snapshot: [9](#0-8) 

So there are two independently-maintained "weight" ledgers for the same reward cycle: the node's, which is live and PoX-5-mutation-aware, and each signer's, which is a point-in-time copy taken once at `Signer::new` and frozen for the rest of the cycle.

### Impact Explanation
This breaks the "aggregated-weight vs verified-accepts" equality required for safe/liveness threshold logic:
- If a signer's real PoX-5 weight decreases mid-cycle (stake withdrawn/redelegated) but the local snapshot still credits it with its original, higher weight, a signer's local tally can reach its (stale) 70% threshold and locally accept/broadcast a block using pre-commits/signatures that, when re-verified by the node against the current live reward set, do not actually sum to 70% real weight. That signer would broadcast acceptance and mark blocks `LocallyAccepted`/attempt-sign for something the network cannot actually finalize, stalling that signer's participation and misrepresenting the tenure state in its own `signerdb` (a wedge candidate: the signer can get stuck waiting on peers whose weight it double/under counts).
- Conversely, if a signer's real weight increases mid-cycle but the frozen snapshot still uses the old, lower weight, the signer's local `total_weight` denominator is wrong, permanently skewing its own threshold math (`min_weight`/`total_weight` percentages) for the rest of the cycle — this can push a signer into never reaching its local threshold on time (High: "a signer wedged into never signing valid blocks ... or acting on a stale reward set/threshold"), because the on-disk `Signer` object for that reward-cycle slot is never rebuilt until the *next* reward cycle rotates in (`refresh_signer_config` is keyed off `reward_cycle` equality only).

This does not directly let an invalid block get accepted on-chain, because `verify_signer_signatures` is the final gate and always uses the live weight; the concrete, provable consequence is a **liveness wedge inside the signer's own decision logic for the remainder of the affected reward cycle** — the signer is "acting on a stale reward set/threshold" as called out by the rules' High bucket.

### Likelihood Explanation
This requires no majority collusion and no other signer's key: it is triggered purely by ordinary PoX-5 stacking operations (any staker adjusting their delegated stake mid-cycle) that are within the reachable, in-scope protocol surface (`stacks-signer/src/v0/signer.rs`, `stacks-signer/src/runloop.rs`, and the PoX-5 signer-set contract in `stackslib/src/chainstate/stacks/boot/pox-5.clar` that this project deliberately made cycle-mutable, unlike prior PoX versions). Because I could not fully confirm from the indexed code whether PoX-5's mid-cycle add/remove functions actually apply to the *currently active* reward cycle used for signing (as opposed to only future cycles) — the `stack-increase`/`delegate-stack-increase` entry points that would call these helpers were not found in the indexed portion of `pox-5.clar` — this should be treated as a plausible but not fully-verified reachable path, and confirming the exact caller/trigger conditions for `add-signer-to-set-for-cycle`/`remove-staker-from-signer-for-cycle` against the *current* cycle is the key remaining validation step.

### Recommendation
Re-derive `signer_weights`/`total_weight` from a live read of the current reward cycle's `.signers` contract state (or invalidate/refresh the cached `SignerEntries` whenever the underlying PoX-5 stake for the active cycle changes) instead of freezing it for the lifetime of the `Signer` object, so that local threshold computations in `handle_block_pre_commit`, `store_and_process_block_signature`, and rejection handling stay consistent with the weight the node will use in `verify_signer_signatures`.

### Proof of Concept
1. A PoX-5 reward cycle `N` begins; `Signer::new` snapshots `signer_weights` from the reward set returned at that time (`stacks-signer/src/v0/signer.rs:299-307`).
2. Mid-cycle, a staker adjusts delegated stake such that PoX-5's contract crosses `SIGNER_SET_MIN_USTX` for some signer, changing that signer's true weight for cycle `N` via `add-signer-to-set-for-cycle`/`remove-staker-from-signer-for-cycle` (`pox-5.clar:1546-1560`, `1705-1737`).
3. `runloop.rs`'s `refresh_runloop`/`refresh_signer_config` never re-triggers for cycle `N` because `is_configured_for_cycle` only checks the reward-cycle number, not weight drift (`runloop.rs:450-469`).
4. All subsequent pre-commit/reject/accept weight tallies in `v0/signer.rs` continue to use the stale `signer_weights`, diverging from what `verify_signer_signatures` computes fresh from the live reward set (`nakamoto/mod.rs:1120-1190`) — producing a local threshold decision inconsistent with the network's real, mutable weight for the remainder of cycle `N`.

### Citations

**File:** stacks-signer/src/v0/signer.rs (L299-307)
```rust
        Self {
            private_key: signer_config.stacks_private_key,
            stacks_address,
            stackerdb,
            mainnet: signer_config.mainnet,
            mode,
            signer_addresses: signer_config.signer_entries.signer_addresses.clone(),
            signer_weights: signer_config.signer_entries.signer_addr_to_weight.clone(),
            signer_slot_ids: signer_config.signer_slot_ids.clone(),
```

**File:** stacks-signer/src/v0/signer.rs (L1290-1301)
```rust
        let committers = self
            .signer_db
            .get_block_pre_committers(&block_hash)
            .unwrap_or_else(|_| panic!("{self}: Failed to load block commits"));

        let commit_weight = self.compute_signature_signing_weight(committers.iter());
        let total_weight = self.compute_signature_total_weight();

        let min_weight = NakamotoBlockHeader::compute_voting_weight_threshold(total_weight)
            .unwrap_or_else(|_| {
                panic!("{self}: Failed to compute threshold weight for {total_weight}")
            });
```

**File:** stacks-signer/src/v0/signer.rs (L2304-2312)
```rust
        let signature_weight = self.signer_weights.get(signer_address).unwrap_or(&0);
        let total_reject_weight =
            self.compute_signature_signing_weight(rejection_addrs.iter().map(|(addr, _)| addr));
        let total_weight = self.compute_signature_total_weight();

        let min_weight = NakamotoBlockHeader::compute_voting_weight_threshold(total_weight)
            .unwrap_or_else(|_| {
                panic!("{self}: Failed to compute threshold weight for {total_weight}")
            });
```

**File:** stacks-signer/src/v0/signer.rs (L2494-2501)
```rust
        let signature_weight = self.signer_weights.get(signer_address).unwrap_or(&0);
        let total_signature_weight = self.compute_signature_signing_weight(addrs_to_sigs.keys());
        let total_weight = self.compute_signature_total_weight();

        let min_weight = NakamotoBlockHeader::compute_voting_weight_threshold(total_weight)
            .unwrap_or_else(|_| {
                panic!("{self}: Failed to compute threshold weight for {total_weight}")
            });
```

**File:** stacks-signer/src/runloop.rs (L429-447)
```rust
        // Check if we need to refresh the signers:
        //   need to refresh the current signer if we are not configured for the current reward cycle
        //   need to refresh the next signer if we're not configured for the next reward cycle, and we're in the prepare phase
        if !Self::is_configured_for_cycle(&self.stacks_signers, current_reward_cycle) {
            self.refresh_signer_config(current_reward_cycle);
        }
        if is_in_next_prepare_phase
            && !Self::is_configured_for_cycle(&self.stacks_signers, next_reward_cycle)
        {
            self.refresh_signer_config(next_reward_cycle);
        }

        self.cleanup_stale_signers(current_reward_cycle);
        if self.stacks_signers.is_empty() {
            self.state = State::NoRegisteredSigners;
        } else {
            self.state = State::RegisteredSigners;
        }
        Ok(())
```

**File:** stacks-signer/src/runloop.rs (L450-469)
```rust
    fn is_configured_for_cycle(
        stacks_signers: &HashMap<u64, ConfiguredSigner<Signer, T>>,
        reward_cycle: u64,
    ) -> bool {
        let Some(signer) = stacks_signers.get(&(reward_cycle % 2)) else {
            return false;
        };
        signer.reward_cycle() == reward_cycle
    }

    fn is_registered_for_cycle(
        stacks_signers: &HashMap<u64, ConfiguredSigner<Signer, T>>,
        reward_cycle: u64,
    ) -> bool {
        let Some(signer) = stacks_signers.get(&(reward_cycle % 2)) else {
            return false;
        };
        signer.reward_cycle() == reward_cycle
            && matches!(signer, ConfiguredSigner::RegisteredSigner(_))
    }
```

**File:** stackslib/src/chainstate/stacks/boot/pox-5.clar (L1546-1560)
```text
        (if is-in-signer-set
            (if (< new-delegated SIGNER_SET_MIN_USTX)
                ;; They've crossed back below the threshold - remove from the signer set
                ;; and remove from reward calculations.
                (begin
                    (try! (remove-staker-from-set-for-cycle signer reward-cycle))
                    (map-set signer-shares-staked-for-cycle {
                        reward-cycle: reward-cycle,
                        signer: signer,
                        bond-index: none,
                    }
                        u0
                    )
                    (map-set total-shares-staked-for-cycle {
                        reward-cycle: reward-cycle,
```

**File:** stackslib/src/chainstate/stacks/boot/pox-5.clar (L1705-1737)
```text
        (if (>= new-delegated SIGNER_SET_MIN_USTX)
            (begin
                (map-set signer-shares-staked-for-cycle {
                    reward-cycle: cycle,
                    bond-index: none,
                    signer: signer,
                }
                    (+ prev-staked stake-amount)
                )
                (if (< cur-delegated-for-signer SIGNER_SET_MIN_USTX)
                    ;; They just crossed the threshold - add to signer set and add to reward calculations
                    (begin
                        (add-signer-to-set-for-cycle signer cycle)
                        (map-set total-shares-staked-for-cycle {
                            reward-cycle: cycle,
                            bond-index: none,
                        }
                            (+ prev-total-shares-staked prev-staked stake-amount)
                        )
                    )
                    ;; They're already over the threshold - update the total by just `stake-amount`
                    (map-set total-shares-staked-for-cycle {
                        reward-cycle: cycle,
                        bond-index: none,
                    }
                        (+ prev-total-shares-staked stake-amount)
                    )
                )
            )

            ;; not over the min yet
            true
        )
```

**File:** stackslib/src/chainstate/nakamoto/mod.rs (L1120-1190)
```rust
        let strict_order = epoch_id.enforces_strict_signature_order();

        let total_weight = reward_set
            .total_signing_weight()
            .map_err(|_| ChainstateError::NoRegisteredSigners(0))?;

        // HashMap of <PublicKey, (Signer, Index)>
        let mut signers_by_pk: HashMap<_, _> = signers
            .iter()
            .enumerate()
            .map(|(i, signer)| (&signer.signing_key, (signer, i)))
            .collect();

        for signature in self.signer_signature.iter() {
            let public_key = Secp256k1PublicKey::recover_to_pubkey_without_validating_low_s(
                message.bits(),
                signature,
            )
            .map_err(|_| {
                ChainstateError::InvalidStacksBlock(format!(
                    "Unable to recover public key from signature {}",
                    signature.to_hex()
                ))
            })?;

            let mut public_key_bytes = [0u8; 33];
            public_key_bytes.copy_from_slice(&public_key.to_bytes_compressed()[..]);

            let (signer, signer_index) = signers_by_pk.remove(&public_key_bytes).ok_or_else(|| {
                warn!(
                    "Found an invalid public key. Reward set has {} signers. Chain length {}. Signatures length {}",
                    signers.len(),
                    self.chain_length,
                    self.signer_signature.len(),
                );
                ChainstateError::InvalidStacksBlock(format!(
                    "Public key {} not found in the reward set",
                    public_key.to_hex()
                ))
            })?;

            // Enforce order of signatures
            if let Some(index) = last_index.as_ref() {
                if *index >= signer_index {
                    return Err(ChainstateError::InvalidStacksBlock(
                        "Signatures are out of order".to_string(),
                    ));
                }
                if strict_order {
                    last_index = Some(signer_index);
                }
            } else {
                last_index = Some(signer_index);
            }

            total_weight_signed = total_weight_signed
                .checked_add(signer.weight)
                .expect("FATAL: overflow while computing signer set threshold");
        }

        let threshold = Self::compute_voting_weight_threshold(total_weight)?;

        if total_weight_signed < threshold {
            return Err(ChainstateError::InvalidStacksBlock(format!(
                "Not enough signatures. Needed at least {} but got {} (out of {})",
                threshold, total_weight_signed, total_weight,
            )));
        }

        return Ok(total_weight_signed);
    }
```
