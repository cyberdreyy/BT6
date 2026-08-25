### No vulnerability found for this question.

The `Deduper` design specifically defends against this class of attack. `DeduperGeneration::new` seeds each `ahash::RandomState` via `rng.random()` for all four seed parameters at construction and at every `reset`, and this seed is never exposed to network input [1](#0-0) . Because `ahash::RandomState::hash_one` is a *keyed* hash, the mapping from byte patterns to output digest bits is permuted by an attacker-unknown secret; this is the standard defense against hash-flooding/algorithmic-complexity attacks, and structural padding or fixed byte patterns cannot be crafted to concentrate into specific buckets without knowledge of the seed, precisely because that's what keyed hashing is designed to prevent.

Additionally, the worst case an attacker can already achieve without any special crafting is simply sending many syntactically-unique transactions (which is required anyway since duplicate transactions are, by definition, filtered), each incrementing `popcount` by up to `K=2` bits per `dedup` call [2](#0-1) . This baseline saturation rate from ordinary unique traffic is exactly what `maybe_reset`'s `false_positive_rate` and `reset_cycle` thresholds are designed to bound [3](#0-2) ; the premise that "structured padding, independent of the secret seed" can reliably force concentrated bucket collisions contradicts the keyed-hash security property that this code correctly relies on, and no code defect bypasses that seeding. This does not exceed the already-anticipated behavior of maybe_reset-triggered resets under high unique-transaction volume, which is an accepted design tradeoff, not a distinct exploitable bug.

### Citations

**File:** perf/src/deduper.rs (L82-95)
```rust
    pub fn maybe_reset<R: Rng>(
        &self,
        rng: &mut R,
        false_positive_rate: f64,
        reset_cycle: Duration,
    ) -> bool {
        assert!(0.0 < false_positive_rate && false_positive_rate < 1.0);
        let _reset_guard = self.reset_guard.lock().unwrap();
        let saturated = self.false_positive_rate() >= false_positive_rate;
        if saturated || self.state.load().started_at.elapsed() >= reset_cycle {
            self.reset(rng);
        }
        saturated
    }
```

**File:** perf/src/deduper.rs (L100-114)
```rust
    pub fn dedup(&self, data: &T) -> bool {
        let mut out = true;
        let state = self.state.load();
        for random_state in state.random_states.iter() {
            let hash: u64 = random_state.hash_one(data) % self.num_bits;
            let index = (hash >> 6) as usize;
            let mask: u64 = 1u64 << (hash & 63);
            let old = self.bits[index].fetch_or(mask, Ordering::Relaxed);
            if old & mask == 0u64 {
                self.popcount.fetch_add(1, Ordering::Relaxed);
                out = false;
            }
        }
        out
    }
```

**File:** perf/src/deduper.rs (L121-123)
```rust
fn new_random_state<R: Rng>(rng: &mut R) -> RandomState {
    RandomState::with_seeds(rng.random(), rng.random(), rng.random(), rng.random())
}
```
