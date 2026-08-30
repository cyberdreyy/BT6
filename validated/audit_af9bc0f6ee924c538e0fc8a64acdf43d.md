No vulnerability found for this question.

The premise doesn't hold: `ExtCostsConfig` (and the wrapping `GasCounter`) is never resolved "at receipt-creation time" — it is resolved exactly once per chunk-apply, from the *block's* `epoch_id` → `protocol_version`, via `RuntimeConfigStore::get_config`, and that resolved `config` is what's threaded into `ApplyState` and ultimately into every `GasCounter` created while processing that chunk. [1](#0-0) [2](#0-1) 

`protocol_version` itself is derived deterministically from `epoch_id`, which is part of the block header and thus already agreed upon by consensus before any node applies the chunk — it is not something a node can compute differently depending on when a particular receipt happened to be created: [3](#0-2) 

`RuntimeConfigStore::get_config` is a pure, deterministic `BTreeMap` floor-lookup on `protocol_version`, so for a given, agreed-upon `protocol_version` every honest node produces byte-identical `RuntimeConfig`/`ExtCostsConfig` — there is no "resolution point" ambiguity or dependency on wall-clock/local state: [4](#0-3) 

Receipts do not carry their own `ExtCostsConfig` snapshot from creation time; `GasCounter::pay_per` always uses whatever `ext_costs_config` was passed into `GasCounter::new` for the *current* apply, which is uniform across all receipts processed within that chunk/epoch: [5](#0-4) 

So the described attack — one honest node using a pre-boundary config copy and another using a post-boundary copy for the *same* chunk/receipt — cannot occur under normal (non-Byzantine, non-misconfigured) node operation, since protocol version selection is a deterministic function of consensus-agreed `epoch_id`, not of when the underlying transaction/receipt was authored. This falls under the "misconfiguration-only" / speculative-without-reachable-path exclusion in the rules, and no concrete state-root-divergence path from an unprivileged attacker's transaction inputs is demonstrated.

### Citations

**File:** chain/chain/src/runtime/mod.rs (L280-286)
```rust
        let prev_block_epoch_id = self.epoch_manager.get_epoch_id(prev_block_hash)?;
        let current_protocol_version = self.epoch_manager.get_epoch_protocol_version(&epoch_id)?;
        let prev_block_protocol_version =
            self.epoch_manager.get_epoch_protocol_version(&prev_block_epoch_id)?;
        let is_first_block_of_version = current_protocol_version != prev_block_protocol_version;

        let config = self.runtime_config_store.get_config(current_protocol_version);
```

**File:** chain/chain/src/runtime/mod.rs (L325-337)
```rust
        let apply_state = ApplyState {
            apply_reason,
            block_height,
            prev_block_hash: *prev_block_hash,
            shard_id,
            epoch_id,
            epoch_height,
            gas_price,
            block_timestamp,
            gas_limit: Some(gas_limit),
            random_seed,
            current_protocol_version,
            config: config.clone(),
```

**File:** chain/chain/src/runtime/mod.rs (L1264-1266)
```rust
        let epoch_id = self.epoch_manager.get_epoch_id_from_prev_block(&block.prev_block_hash)?;
        let protocol_version = self.epoch_manager.get_epoch_protocol_version(&epoch_id)?;
        let config = self.runtime_config_store.get_config(protocol_version);
```

**File:** core/parameters/src/config_store.rs (L242-250)
```rust
    pub fn get_config(&self, protocol_version: ProtocolVersion) -> &Arc<RuntimeConfig> {
        self.store
            .range((Bound::Unbounded, Bound::Included(protocol_version)))
            .next_back()
            .unwrap_or_else(|| {
                panic!("Not found RuntimeConfig for protocol version {}", protocol_version)
            })
            .1
    }
```

**File:** runtime/near-vm-runner/src/logic/gas_counter.rs (L290-302)
```rust
    pub(crate) fn pay_per(&mut self, cost: ExtCosts, num: u64) -> Result<()> {
        let use_gas =
            cost.gas(&self.ext_costs_config).checked_mul(num).ok_or(HostError::IntegerOverflow)?;

        self.inc_ext_costs_counter(cost, num);
        let old_burnt_gas = self.fast_counter.burnt_gas;
        let burn_gas_result = self.burn_gas(use_gas);
        self.update_profile_host(
            cost,
            Gas::from_gas(self.fast_counter.burnt_gas.saturating_sub(old_burnt_gas)),
        );
        burn_gas_result
    }
```
