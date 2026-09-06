## Finding [1](#0-0) 

### Title
Stale `StateMachineUpdate` entries in `GlobalStateEvaluator` are never freshness-checked before being counted toward supermajority thresholds - (File: `libsigner/src/v0/signer_state.rs`)

### Summary
`GlobalStateEvaluator` aggregates each signer's latest `StateMachineUpdate` (burn view, miner view, protocol version, replay set) keyed only by `StacksAddress`, with no timestamp on the stored value and no freshness check applied when the weights are tallied. [2](#0-1)  This mirrors the reported oracle-freshness class of bug: a piece of externally supplied, timestamped data is consumed for a threshold decision without checking how old it is.

### Finding Description
`insert_update` (invoked from `handle_state_machine_update`) simply overwrites the map entry for a signer's address every time a new gossip message arrives, and the entry persists indefinitely once a signer stops sending updates. [3](#0-2)  `SignerDb::insert_state_machine_update` does record a `received_time` for each update (and a per-burn-block-view arrival timestamp), so the freshness data exists. [4](#0-3)  However, none of `determine_latest_supported_signer_protocol_version`, `determine_global_burn_view`, or `determine_global_state` consult `received_time` (or any age bound) before summing `address_weights` for the corresponding entry — they iterate `self.address_updates` unconditionally. [5](#0-4)  The same unfiltered map is used by `capitulate_miner_view`, which decides whether the local signer should switch its view of the current miner/parent tenure based on these weight tallies. [6](#0-5) 

A single signer (one StackerDB slot) can send one `StateMachineUpdate` and then go silent (crash, network partition, or deliberate abandonment after gossiping a message). That signer's stale opinion of the burn view, active miner, and parent-tenure-last-block remains permanently counted at full weight in every subsequent `determine_global_burn_view` / `determine_global_state` / `capitulate_miner_view` computation used by every other signer's runloop, because there is no mechanism analogous to the `NodeOutput.Data` timestamp check recommended in the external report.

### Impact Explanation
This falls into the "High" impact category of acting on a stale threshold: the reward/weight aggregation that other signers rely on to determine the canonical burn view and to decide whether to capitulate their miner view is computed over data that can be arbitrarily stale for any subset of signers, without ever being excluded from the tally. In a live network, this dilutes/blocks the ability of `determine_global_burn_view`/`determine_global_state` to reach a threshold reflecting the actually-online signer set, and it feeds `capitulate_viewpoint`'s decision about whether the local signer should change its own tracked miner state — the same state that section 8 of the docs marks as consensus-visible. [7](#0-6) 

### Likelihood Explanation
Likelihood is Medium: it requires only one signer (of any weight) to stop refreshing its gossiped `StateMachineUpdate` after an initial send — a very ordinary occurrence (crash, restart delay, network partition) rather than a majority collusion. No special privileges beyond a normal signer's StackerDB slot are needed.

### Recommendation
Track a per-entry `received_time`/age alongside each `StateMachineUpdate` inside `GlobalStateEvaluator.address_updates` (the value is already recorded in `SignerDb::insert_state_machine_update`), and exclude entries older than a configurable freshness threshold from the weight tallies in `determine_latest_supported_signer_protocol_version`, `determine_global_burn_view`, `determine_global_state`, and `capitulate_miner_view`, consistent with how `get_tenure_last_block_info` already discards stale signed-block info via `tenure_last_block_proposal_timeout`. [8](#0-7) 

### Proof of Concept
1. Signer S (any weight < 70%) sends one `StateMachineUpdate` claiming burn view B1/miner M1, then stops sending updates (crash/partition).
2. `GlobalStateEvaluator.address_updates[S]` retains that stale entry indefinitely, since `insert_update`/`handle_state_machine_update` never expires it.
3. Every subsequent call to `determine_global_burn_view`/`determine_global_state`/`capitulate_miner_view` by every other signer folds S's stale B1/M1 opinion into the weight sums at full weight, even though S has not observed the actual current burn view/miner for an arbitrary length of time.
4. No code path checks `received_time` (stored in `SignerDb::insert_state_machine_update`) against any freshness cutoff before using the entry, unlike the analogous and already-guarded `get_tenure_last_block_info` timeout check.

### Citations

**File:** libsigner/src/v0/signer_state.rs (L29-54)
```rust
/// A struct used to determine the current global state
#[derive(Debug)]
pub struct GlobalStateEvaluator {
    /// A mapping of signer addresses to their corresponding vote weight
    pub address_weights: HashMap<StacksAddress, u32>,
    /// A mapping of signer addresses to their corresponding updates
    pub address_updates: HashMap<StacksAddress, StateMachineUpdate>,
    /// The total weight of all signers
    pub total_weight: u32,
}

impl GlobalStateEvaluator {
    /// Create a new state evaluator
    pub fn new(
        address_updates: HashMap<StacksAddress, StateMachineUpdate>,
        address_weights: HashMap<StacksAddress, u32>,
    ) -> Self {
        let total_weight = address_weights
            .values()
            .fold(0u32, |acc, val| acc.saturating_add(*val));
        Self {
            address_weights,
            address_updates,
            total_weight,
        }
    }
```

**File:** libsigner/src/v0/signer_state.rs (L56-99)
```rust
    /// Determine what the maximum signer protocol version that a majority of signers can support
    pub fn determine_latest_supported_signer_protocol_version(&self) -> Option<u64> {
        let mut protocol_versions = HashMap::new();
        for (address, update) in &self.address_updates {
            let Some(weight) = self.address_weights.get(address) else {
                continue;
            };
            let entry = protocol_versions
                .entry(update.local_supported_signer_protocol_version)
                .or_insert_with(|| 0);
            *entry += weight;
        }
        // find the highest version number supported by a threshold number of signers
        let mut protocol_versions: Vec<_> = protocol_versions.into_iter().collect();
        protocol_versions.sort_by_key(|(version, _)| *version);
        let mut total_weight_support: u32 = 0;
        for (version, weight_support) in protocol_versions.into_iter().rev() {
            total_weight_support += weight_support;
            if self.reached_agreement(total_weight_support) {
                return Some(version);
            }
        }
        None
    }

    /// Determine what the global burn view is if there is one
    pub fn determine_global_burn_view(&self) -> Option<(&ConsensusHash, u64)> {
        let mut burn_blocks = HashMap::new();
        for (address, update) in &self.address_updates {
            let Some(weight) = self.address_weights.get(address) else {
                continue;
            };
            let (burn_block, burn_block_height) = update.content.burn_block_view();

            let entry = burn_blocks
                .entry((burn_block, burn_block_height))
                .or_insert_with(|| 0);
            *entry += weight;
            if self.reached_agreement(*entry) {
                return Some((burn_block, burn_block_height));
            }
        }
        None
    }
```

**File:** stacks-signer/src/v0/signer.rs (L1082-1106)
```rust
    fn handle_state_machine_update(
        &mut self,
        signer_public_key: &Secp256k1PublicKey,
        update: &StateMachineUpdate,
        received_time: &SystemTime,
    ) {
        let replay_txids = update.content.replay_txids();
        let pubkey = signer_public_key.to_hex();
        info!(
            "{self}: Received state machine update from signer {pubkey}: {update}";
            "replay_txids" => ?replay_txids
        );
        let address = StacksAddress::p2pkh(self.mainnet, signer_public_key);
        // Store the state machine update so we can reload it if we crash
        if let Err(e) = self.signer_db.insert_state_machine_update(
            self.reward_cycle,
            &address,
            update,
            received_time,
        ) {
            warn!("{self}: Failed to update global state in signerdb: {e}");
        }
        self.global_state_evaluator
            .insert_update(address, update.clone());
    }
```

**File:** stacks-signer/src/signerdb.rs (L2273-2312)
```rust
    /// Insert the signer state machine update
    pub fn insert_state_machine_update(
        &mut self,
        reward_cycle: u64,
        address: &StacksAddress,
        update: &StateMachineUpdate,
        received_time: &SystemTime,
    ) -> Result<(), DBError> {
        let received_ts = received_time
            .duration_since(std::time::UNIX_EPOCH)
            .map_err(|e| DBError::Other(format!("Bad system time: {e}")))?
            .as_secs();
        let update_str =
            serde_json::to_string(&update).expect("Unable to serialize state machine update");
        debug!("Inserting update.";
            "reward_cycle" => reward_cycle,
            "address" => %address,
            "active_signer_protocol_version" => update.active_signer_protocol_version,
            "local_supported_signer_protocol_version" => update.local_supported_signer_protocol_version
        );
        self.db.execute("INSERT OR REPLACE INTO signer_state_machine_updates (signer_addr, reward_cycle, state_update, received_time) VALUES (?1, ?2, ?3, ?4)", params![
            address.to_string(),
            u64_to_sql(reward_cycle)?,
            update_str,
            u64_to_sql(received_ts)?,
        ])?;

        // Conditionally insert into burn_block_updates_received_times only if missing for (signer_addr, burn_block_consensus_hash)
        let burn_block_consensus_hash = update.content.burn_block_view().0;
        self.db.execute(
            "INSERT OR IGNORE INTO burn_block_updates_received_times
            (signer_addr, burn_block_consensus_hash, received_time)
            VALUES (?1, ?2, ?3)",
            params![
                address.to_string(),
                burn_block_consensus_hash,
                u64_to_sql(received_ts)?,
            ],
        )?;
        Ok(())
```

**File:** stacks-signer/src/v0/signer_state.rs (L981-1044)
```rust
    /// Determines whether a signer with the `local_address` and `local_update` should capitulate
    /// its current miner view to a new state. This is not necessarily the same as the current global
    /// view of the miner as it is up to signers to capitulate before this becomes the finalized view.
    pub fn capitulate_miner_view(
        &mut self,
        stacks_client: &StacksClient,
        eval: &mut GlobalStateEvaluator,
        signerdb: &mut SignerDb,
        local_update: &StateMachineUpdateMessage,
        tenure_last_block_proposal_timeout: Duration,
    ) -> Option<StateMachineUpdateMinerState> {
        // First always make sure we consider our own viewpoint
        eval.insert_update(
            stacks_client.get_signer_address().clone(),
            local_update.clone(),
        );

        // Determine the current burn block from the local update
        let (current_burn_block, current_burn_block_height) =
            local_update.content.burn_block_view();

        // Determine the global burn view
        let (global_burn_block, global_burn_block_height) = eval.determine_global_burn_view()?;
        if current_burn_block != global_burn_block {
            debug!(
                "Signer State: Burn block mismatch. Cannot capitulate.";
                "current_burn_block" => %current_burn_block,
                "current_burn_block_height" => current_burn_block_height,
                "global_burn_block" => %current_burn_block,
                "global_burn_block_height" => global_burn_block_height,
            );
            // We don't have the majority's burn block yet...will have to wait
            crate::monitoring::actions::increment_signer_agreement_state_conflict(
                crate::monitoring::SignerAgreementStateConflict::BurnBlockDelay,
            );
            return None;
        }

        let mut miners = HashMap::new();
        let mut potential_matches = HashSet::new();

        for (address, update) in &eval.address_updates {
            let Some(weight) = eval.address_weights.get(address) else {
                continue;
            };
            let burn_block = update.content.burn_block_view().0;
            if burn_block != global_burn_block {
                continue;
            }
            let miner_state = update.content.current_miner();
            let StateMachineUpdateMinerState::ActiveMiner {
                tenure_id,
                parent_tenure_last_block_height,
                parent_tenure_id,
                ..
            } = miner_state
            else {
                // Only consider potential active miners
                continue;
            };

            let entry = miners.entry(miner_state).or_insert(0);
            *entry += weight;
            if !eval.reached_disagreement(*entry) {
```

**File:** docs/signer-flows.md (L450-456)
```markdown
## 8. Burn blocks & the miner-view state machine

Independent of any single block, the signer maintains a view of _who the current
miner is and what they should build on_, and broadcasts it as a
`StateMachineUpdate`. The whole miner state, including
`parent_tenure_last_block`, is the equality key for global agreement, so what
this flow computes is consensus-visible.
```

**File:** stacks-signer/src/chainstate/mod.rs (L330-363)
```rust
    pub fn get_tenure_last_block_info(
        consensus_hash: &ConsensusHash,
        signer_db: &SignerDb,
        tenure_last_block_proposal_timeout: Duration,
    ) -> Result<Option<BlockInfo>, ClientError> {
        // Get the last signed block in the tenure
        let last_signed_block = signer_db
            .get_last_signed_block(consensus_hash)
            .map_err(|e| ClientError::InvalidResponse(e.to_string()))?;

        let Some(block_info) = last_signed_block else {
            return Ok(None);
        };

        // `approved_time` may hold the pre-commit time; use the actual signature time.
        let Some(signed_over_time) = block_info.signed_self.max(block_info.signed_group) else {
            return Ok(None);
        };

        if signed_over_time.saturating_add(tenure_last_block_proposal_timeout.as_secs())
            > get_epoch_time_secs()
        {
            // The last accepted block is not timed out, return it
            Ok(Some(block_info))
        } else {
            // The last accepted block is timed out
            info!(
                "Last accepted block has timed out";
                "signer_signature_hash" => %block_info.block.header.signer_signature_hash(),
                "signed_over_time" => signed_over_time,
                "state" => %block_info.state,
            );
            Ok(None)
        }
```
