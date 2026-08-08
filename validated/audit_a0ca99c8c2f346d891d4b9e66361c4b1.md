### No vulnerability found for this question.

The question concerns a testing-methodology gap in gossip's `EpochSpecs` trait implementations (`gossip/src/epoch_specs.rs` `TestEpochSpecs` [1](#0-0)  vs `core/src/epoch_specs.rs` `EpochSpecs`'s cache-refresh logic [2](#0-1) ), not an attacker-reachable vulnerability. This falls outside the defined threat model: it requires staked-node/gossip data control and test-harness/differential-testing analysis rather than a single unprivileged JSON-RPC/pubsub call or on-chain write later returned through those APIs, both of which are explicitly excluded ("Reject anything requiring... staked-node control... mocked paths"). There is no reachable attacker entrypoint through RPC, pubsub, decoding, or blockstore-read logic described here, so it does not meet the validation requirements.

### Citations

**File:** gossip/src/epoch_specs.rs (L13-35)
```rust
#[cfg(feature = "dev-context-only-utils")]
#[derive(Clone)]
pub struct TestEpochSpecs {
    pub staked_nodes: Arc<HashMap<Pubkey, u64>>,
    pub slots_in_epoch: u64,
    pub epoch_duration: Duration,
}

#[cfg(feature = "dev-context-only-utils")]
impl EpochSpecs for TestEpochSpecs {
    fn current_epoch_staked_nodes(&mut self) -> Arc<HashMap<Pubkey, u64>> {
        Arc::clone(&self.staked_nodes)
    }
    fn epoch_duration(&mut self) -> Duration {
        self.epoch_duration
    }
    fn epoch_slots(&mut self) -> u64 {
        self.slots_in_epoch
    }
    fn clone_box(&self) -> Box<dyn EpochSpecs> {
        Box::new(self.clone())
    }
}
```

**File:** core/src/epoch_specs.rs (L56-70)
```rust
    fn maybe_refresh_cache(cache: &mut EpochSpecsCache, shareable_banks: &SharableBanks) {
        let root_bank = shareable_banks.root();
        if root_bank.epoch() == cache.epoch {
            return; // still the same epoch. nothing to update.
        }
        debug_assert_eq!(
            cache.epoch_schedule.get_epoch(root_bank.slot()),
            root_bank.epoch()
        );
        cache.epoch = root_bank.epoch();
        cache.epoch_schedule = root_bank.epoch_schedule().clone();
        cache.current_epoch_staked_nodes = root_bank.current_epoch_staked_nodes();
        cache.epoch_duration = get_epoch_duration(&root_bank);
        cache.slots_in_epoch = root_bank.get_slots_in_epoch(root_bank.epoch());
    }
```
