## Title
`confirmed` commitment level in `getSignatureStatuses`/`signatureSubscribe` can report a transaction as `Confirmed` on a fork that is later abandoned, causing clients to act on state that gets rolled back - (File: `rpc/src/rpc.rs`)

### Summary
The Superposition finding is fundamentally about a client observing a "successful" state for one action, taking an irreversible follow-up action based on that observation, and then having the underlying chain state reorganize so the first action's effect disappears while the second (irreversible) action still executes — producing an unrecoverable loss. Agave's own commitment-level model has a structurally identical failure mode: `confirmed` commitment (optimistic confirmation) is explicitly *not* final and can be rolled back if the optimistically-confirmed fork is later abandoned, yet RPC/pubsub consumers use `confirmed` as if it were a durable success signal.

### Finding Description
`RpcSolPubSubImpl` and `JsonRpcRequestProcessor` expose transaction status at the `confirmed` commitment level through `getSignatureStatuses`/`get_transaction_status` and the `signatureSubscribe` pubsub method. `get_transaction_status` derives `TransactionConfirmationStatus::Confirmed` purely from whether the signature is present in `optimistically_confirmed_bank`: [1](#0-0) 

The "optimistically confirmed" bank tracked here is populated from gossip vote aggregation before the corresponding fork is rooted, as seen in `OptimisticallyConfirmedBankTracker`'s notification handler, which updates `optimistically_confirmed_bank` as soon as 2/3+ stake votes are observed for a bank — well before that bank is guaranteed to become part of the canonical, rooted chain: [2](#0-1) 

Agave itself maintains a dedicated `OptimisticConfirmationVerifier` specifically because this optimistic confirmation *can* later prove to be wrong (i.e., the optimistically-confirmed slot is not an ancestor of the eventual root — analogous to a "reorg" in this context): [3](#0-2) 

The local-cluster test suite exercises exactly this scenario end-to-end: a validator optimistically confirms a slot, that slot is subsequently marked dead/abandoned, and the validator explicitly logs an "optimistically confirmed slot was not rooted" violation: [4](#0-3) [5](#0-4) 

Meanwhile, `signatureSubscribe` allows a client to request `commitment: confirmed` notifications and treat the single "ProcessedSignature" push as a definitive success signal, after which the subscription is auto-unsubscribed and no further update is delivered if the fork later gets abandoned: [6](#0-5) [7](#0-6) 

### Impact Explanation
An unprivileged RPC/pubsub client (e.g., a wallet or program driving a multi-step, irrevocable on-chain flow analogous to "remove liquidity, then burn/close position") that treats a `confirmed`-level status/notification for transaction A as sufficient proof of durability, and then submits an irreversible transaction B conditioned on A's apparent success, can lose funds if the fork carrying A is later abandoned before rooting: A's effects vanish, but B — having been signed and broadcast based on the stale `Confirmed` status — may still land and execute against a bank where A never happened, exactly mirroring the "remove liquidity succeeds visibly → burn position executes → removal is later invalidated → funds locked/lost" pattern from the source report. This is "wrong-fork data returned" from a JSON-RPC/pubsub query, an accepted impact class here.

### Likelihood Explanation
Optimistic-confirmation rollbacks are rare (they require a supermajority-stake voting pattern to be later invalidated, typically via duplicate-block detection or a switch-threshold event) but they are a real, previously-observed condition in the Solana ecosystem, not a purely theoretical one — which is why Agave ships a dedicated `OptimisticConfirmationVerifier` and alerting path (`format_optimistic_confirmed_slot_violation_log`) to detect and log the exact condition. Unlike the Arbitrum discussion in the source report (where reviewers debated whether re-orgs occur at all), Agave's own code and tests assume and actively test for this event occurring.

### Recommendation
- Document (and, where feasible, enforce in higher-level client SDKs) that `confirmed` is not a safe commitment level to gate irreversible, chained user actions; only `finalized` provides that guarantee.
- Consider having `signatureSubscribe`/`getSignatureStatuses` emit a follow-up notification/status transition (e.g., an explicit "rolled back" indication) when a previously `Confirmed` signature is detected via `OptimisticConfirmationVerifier` to no longer be an ancestor of the root, rather than silently going quiet after the one-shot `confirmed` push.

### Proof of Concept
1. Client submits transaction A (e.g., "remove liquidity") and subscribes via `signatureSubscribe` with `commitment: confirmed`.
2. `OptimisticallyConfirmedBankTracker` observes 2/3+ stake votes for the bank containing A and calls `notify_or_defer_confirmed_banks`, causing `get_transaction_status`/the pubsub notifier to report A as `Confirmed` [1](#0-0) [2](#0-1) .
3. Client, seeing `Confirmed`, submits transaction B ("burn position") which is irrevocable.
4. The fork containing A is later found to be non-ancestor of the eventual root (duplicate-block/switch-threshold event), exactly the scenario the `OptimisticConfirmationVerifier`/`test_optimistic_confirmation_violation_detection` test constructs [3](#0-2) [4](#0-3) .
5. A's effects are gone from the canonical chain, but B still executes, leaving the client's funds/position lost — the same end state as the source report's re-org scenario.

### Citations

**File:** rpc/src/rpc.rs (L1736-1765)
```rust
        let (slot, status) = bank.get_signature_status_slot(&signature)?;

        let optimistically_confirmed_bank = self.bank(Some(CommitmentConfig::confirmed()));
        let optimistically_confirmed =
            optimistically_confirmed_bank.get_signature_status_slot(&signature);

        let r_block_commitment_cache = self.block_commitment_cache.read().unwrap();
        let confirmations = if r_block_commitment_cache.root() >= slot
            && is_finalized(&r_block_commitment_cache, bank, &self.blockstore, slot)
        {
            None
        } else {
            r_block_commitment_cache
                .get_confirmation_count(slot)
                .or(Some(0))
        };
        let err = status.clone().err();
        Some(TransactionStatus {
            slot,
            status,
            confirmations,
            err,
            confirmation_status: if confirmations.is_none() {
                Some(TransactionConfirmationStatus::Finalized)
            } else if optimistically_confirmed.is_some() {
                Some(TransactionConfirmationStatus::Confirmed)
            } else {
                Some(TransactionConfirmationStatus::Processed)
            },
        })
```

**File:** rpc/src/optimistically_confirmed_bank_tracker.rs (L306-345)
```rust
        match notification {
            BankNotification::OptimisticallyConfirmed(slot, hash) => {
                let bank = bank_forks.read().unwrap().get(slot);
                if let Some(bank) = bank {
                    if bank.is_frozen() {
                        if bank.hash() != hash {
                            if slot > bank_forks.read().unwrap().root() {
                                pending_optimistically_confirmed_banks.insert((slot, hash));
                                debug!(
                                    "defer notifying optimistic confirmation for slot {slot}: \
                                     local bank hash {} does not match optimistic confirmation \
                                     hash {hash}",
                                    bank.hash()
                                );
                            }
                        } else {
                            let mut w_optimistically_confirmed_bank =
                                optimistically_confirmed_bank.write().unwrap();

                            if bank.slot() > w_optimistically_confirmed_bank.bank.slot() {
                                w_optimistically_confirmed_bank.bank = bank.clone();
                            }

                            if slot > *highest_confirmed_slot {
                                Self::notify_or_defer_confirmed_banks(
                                    subscriptions,
                                    bank_forks,
                                    bank,
                                    *highest_confirmed_slot,
                                    None,
                                    last_notified_confirmed_slot,
                                    pending_optimistically_confirmed_banks,
                                    slot_notification_subscribers,
                                    prioritization_fee_cache,
                                );

                                *highest_confirmed_slot = slot;
                            }
                            drop(w_optimistically_confirmed_bank);
                        }
```

**File:** core/src/optimistic_confirmation_verifier.rs (L26-55)
```rust
    // Returns any optimistic slots that were not rooted
    pub fn verify_for_unrooted_optimistic_slots(
        &mut self,
        root_bank: &Bank,
        blockstore: &Blockstore,
    ) -> Vec<(Slot, Hash)> {
        let root = root_bank.slot();
        let root_ancestors = &root_bank.ancestors;
        let slots_after_root = self
            .unchecked_slots
            .split_off(&((root + 1), Hash::default()));
        // `slots_before_root` now contains all slots <= root
        let slots_before_root = std::mem::replace(&mut self.unchecked_slots, slots_after_root);
        slots_before_root
            .into_iter()
            .filter(|(optimistic_slot, optimistic_hash)| {
                (*optimistic_slot == root && *optimistic_hash != root_bank.hash())
                    || (!root_ancestors.contains_key(optimistic_slot) &&
                    // In this second part of the `and`, we account for the possibility that
                    // there was some other root `rootX` set in BankForks where:
                    //
                    // `root` > `rootX` > `optimistic_slot`
                    //
                    // in which case `root` may  not contain the ancestor information for
                    // slots < `rootX`, so we also have to check if `optimistic_slot` was rooted
                    // through blockstore.
                    !blockstore.is_root(*optimistic_slot))
            })
            .collect()
    }
```

**File:** local-cluster/tests/local_cluster.rs (L1604-1646)
```rust
    // Mark fork as dead on the heavier validator, this should make the fork effectively
    // dead, even though it was optimistically confirmed. The smaller validator should
    // create and jump over to a new fork
    // Also, remove saved tower to intentionally make the restarted validator to violate the
    // optimistic confirmation
    let optimistically_confirmed_slot_parent = {
        let tower = restore_tower(
            &exited_validator_info.info.ledger_path,
            &exited_validator_info.info.keypair.pubkey(),
        )
        .unwrap();

        // Vote must exist since we waited for OC and so this node must have voted
        let last_voted_slot = tower.last_voted_slot().expect("vote must exist");
        let blockstore = open_blockstore(&exited_validator_info.info.ledger_path);

        // The last vote must be descended from the OC slot
        assert!(
            AncestorIterator::new_inclusive(last_voted_slot, &blockstore)
                .contains(&optimistically_confirmed_slot)
        );

        info!(
            "Setting slot: {optimistically_confirmed_slot} on main fork as dead, should cause fork"
        );
        // Necessary otherwise tower will inform this validator that it's latest
        // vote is on slot `optimistically_confirmed_slot`. This will then prevent this validator
        // from resetting to the parent of `optimistically_confirmed_slot` to create an alternative fork because
        // 1) Validator can't vote on earlier ancestor of last vote due to switch threshold (can't vote
        // on ancestors of last vote)
        // 2) Won't reset to this earlier ancestor because reset can only happen on same voted fork if
        // it's for the last vote slot or later
        remove_tower(&exited_validator_info.info.ledger_path, &node_to_restart);
        blockstore
            .set_dead_slot(optimistically_confirmed_slot)
            .unwrap();
        blockstore
            .meta(optimistically_confirmed_slot)
            .unwrap()
            .unwrap()
            .parent_slot
            .unwrap()
    };
```

**File:** local-cluster/tests/local_cluster.rs (L1732-1756)
```rust
        // Check to see that validator detected optimistic confirmation for
        // `last_voted_slot` failed
        let expected_log =
            OptimisticConfirmationVerifier::format_optimistic_confirmed_slot_violation_log(
                optimistically_confirmed_slot,
            );
        // Violation detection thread can be behind so poll logs up to 10 seconds
        if let Some(mut buf) = buf {
            let start = Instant::now();
            let mut success = false;
            let mut output = String::new();
            while start.elapsed().as_secs() < 10 {
                buf.read_to_string(&mut output).unwrap();
                if output.contains(&expected_log) {
                    success = true;
                    break;
                }
                sleep(Duration::from_millis(10));
            }
            print!("{output}");
            assert!(success);
        } else {
            panic!("dumped log and disabled testing");
        }
    }
```

**File:** rpc/src/rpc_pubsub.rs (L511-523)
```rust
    fn signature_subscribe(
        &self,
        signature_str: String,
        config: Option<RpcSignatureSubscribeConfig>,
    ) -> Result<SubscriptionId> {
        let config = config.unwrap_or_default();
        let params = SignatureSubscriptionParams {
            signature: param::<Signature>(&signature_str, "signature")?,
            commitment: config.commitment.unwrap_or_default(),
            enable_received_notification: config.enable_received_notification.unwrap_or_default(),
        };
        self.subscribe(SubscriptionParams::Signature(params))
    }
```

**File:** rpc/src/rpc_subscriptions.rs (L1093-1113)
```rust
                SubscriptionParams::Signature(params) => {
                    num_signatures_found.fetch_add(1, Ordering::Relaxed);
                    if let Some(slot) = slot {
                        let notified = check_commitment_and_notify(
                            params,
                            subscription,
                            bank_forks,
                            slot,
                            |bank, params| {
                                bank.get_signature_status_processed_since_parent(&params.signature)
                            },
                            filter_signature_result,
                            notifier,
                            true, // Unsubscribe.
                        );

                        if notified {
                            num_signatures_notified.fetch_add(1, Ordering::Relaxed);
                        }
                    }
                }
```
