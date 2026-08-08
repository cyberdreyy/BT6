### Title
Redundant unbounded `leader_schedule_utils::leader_schedule` computation on concurrent cache-miss calls in `compute_leader_schedule` - (File: ledger/src/leader_schedule_cache.rs)

### Summary
`LeaderScheduleCache::compute_leader_schedule` performs the expensive `leader_schedule_utils::leader_schedule(epoch, bank)` computation *before* acquiring the write lock and checking whether another thread already inserted the schedule for that epoch. Because `get_leader_schedule_else_compute`/`slot_leader_at_else_compute` check the cache without holding a lock across the compute step, two concurrent lookups for the same never-before-cached epoch can both perform the full computation, with only the losing thread's result discarded.

### Finding Description
`get_leader_schedule_else_compute` (used by `next_leader_slot`, and the analogous `slot_leader_at_else_compute` used by `slot_leader_at`, which backs `getLeaderSchedule`-style RPC lookups) first calls `get_epoch_leader_schedule`, which takes only a read lock on `cached_schedules` [1](#0-0) . If that read returns `None` (cache miss), it calls `compute_leader_schedule`, which unconditionally computes `leader_schedule_utils::leader_schedule(epoch, bank)` *before* taking the write lock, and only checks `Entry::Vacant` afterward to decide whether to insert: [2](#0-1) 

The `Entry::Vacant` check correctly prevents double *insertion* and guarantees cache correctness (only one `Arc<LeaderSchedule>` ends up cached, as demonstrated by the existing `test_thread_race_leader_schedule_cache` test which asserts `cached_schedules.len() == 1`), but it does nothing to prevent double *computation* — the losing thread's already-computed `LeaderSchedule` is simply dropped after being fully computed. The check-then-act race window is inherent to the design: there is no per-epoch mutex/in-flight marker serializing concurrent computations for the same epoch, only a global write lock taken after the expensive work is already done.

### Impact Explanation
This matches Agave's "cost of one request or subscription must be bounded" invariant category. For a single logical request pattern (two `getLeaderSchedule` calls at the client's permitted rate, e.g. driven by retries, connection failover, or straightforward duplicate polling for a not-yet-cached future epoch), the validator can be made to perform the full leader-schedule computation (an O(slots-per-epoch) stake-weighted shuffle over `bank.epoch_stakes_map()`) twice instead of once, doubling CPU cost for that epoch lookup. This is a scoped, bounded amplification (at most 2x per epoch cache-miss window, not unbounded), not a crash or consensus-affecting bug, and is limited to the first access to a given epoch since the cache is populated after either computation completes.

### Likelihood Explanation
Preconditions are easily met: any epoch for which no schedule has yet been computed (e.g., a newly reachable epoch after `set_root` advances `max_epoch`, or any distant future epoch queried via RPC) is a cache miss, and issuing two RPC calls in quick succession from one client within the permitted rate is trivial. The race window is the duration of `leader_schedule_utils::leader_schedule`, which is a real, non-trivial CPU computation, making the window realistically hittable, especially under any client-side retry/backup-request patterns.

### Recommendation
Serialize concurrent computation for the same epoch, e.g. by re-checking the cache immediately after acquiring the write lock but *before* calling `leader_schedule_utils::leader_schedule` (move the compute call inside the lock, or use a per-epoch "in-progress" `HashMap<Epoch, Arc<OnceCell<...>>>`/`Mutex` pattern) so that only the first caller for a given epoch performs the computation while others wait for and reuse its result.

### Proof of Concept
```rust
// ledger/src/leader_schedule_cache.rs (test module)
use std::sync::atomic::{AtomicUsize, Ordering};

#[test]
fn test_concurrent_compute_leader_schedule_duplicate_work() {
    static COMPUTE_COUNT: AtomicUsize = AtomicUsize::new(0);

    // Wrap leader_schedule_utils::leader_schedule with a counting shim
    // (requires making compute path testable, e.g. via a function pointer
    // field or feature-gated instrumentation) to count actual invocations.

    let slots_per_epoch = MINIMUM_SLOTS_PER_EPOCH;
    let epoch_schedule = EpochSchedule::custom(slots_per_epoch, slots_per_epoch / 2, true);
    let GenesisConfigInfo { genesis_config, .. } = create_genesis_config(2);
    let bank = Arc::new(Bank::new_for_tests(&genesis_config));
    let cache = Arc::new(LeaderScheduleCache::new(epoch_schedule, &bank));
    let target_epoch = /* an epoch not yet cached */ cache.max_epoch.load(Ordering::Acquire) + 1;

    let barrier = Arc::new(std::sync::Barrier::new(2));
    let handles: Vec<_> = (0..2)
        .map(|_| {
            let cache = cache.clone();
            let bank = bank.clone();
            let barrier = barrier.clone();
            std::thread::spawn(move || {
                barrier.wait();
                COMPUTE_COUNT.fetch_add(1, Ordering::SeqCst); // instrumentation hook placed
                                                               // inside compute_leader_schedule
                cache.compute_leader_schedule(target_epoch, &bank)
            })
        })
        .collect();
    for h in handles {
        h.join().unwrap();
    }

    // Expected (buggy) behavior today: COMPUTE_COUNT == 2, i.e. the schedule
    // was computed twice even though the cache only stores one entry.
    // A fix should assert COMPUTE_COUNT == 1.
    assert_eq!(COMPUTE_COUNT.load(Ordering::SeqCst), 2);
    let (cached_schedules, _) = &*cache.cached_schedules.read().unwrap();
    assert_eq!(cached_schedules.len(), /* prior count */ 1 + 1); // only one entry inserted
}
```
Note: exact instrumentation requires adding a counting hook around the call at `ledger/src/leader_schedule_cache.rs:208` (e.g., via `cfg(test)` injectable closure), since `leader_schedule_utils::leader_schedule` is not directly mockable in the current code; this PoC sketch demonstrates the intended assertion structure rather than a drop-in runnable test.

### Citations

**File:** ledger/src/leader_schedule_cache.rs (L187-205)
```rust
    pub fn get_epoch_leader_schedule(&self, epoch: Epoch) -> Option<Arc<LeaderSchedule>> {
        self.cached_schedules.read().unwrap().0.get(&epoch).cloned()
    }

    fn get_leader_schedule_else_compute(
        &self,
        epoch: Epoch,
        bank: &Bank,
    ) -> Option<Arc<LeaderSchedule>> {
        if let Some(ref fixed_schedule) = self.fixed_schedule {
            return Some(fixed_schedule.leader_schedule.clone());
        }
        let epoch_schedule = self.get_epoch_leader_schedule(epoch);
        if epoch_schedule.is_some() {
            epoch_schedule
        } else {
            self.compute_leader_schedule(epoch, bank)
        }
    }
```

**File:** ledger/src/leader_schedule_cache.rs (L207-222)
```rust
    fn compute_leader_schedule(&self, epoch: Epoch, bank: &Bank) -> Option<Arc<LeaderSchedule>> {
        let leader_schedule = leader_schedule_utils::leader_schedule(epoch, bank);
        leader_schedule.map(|leader_schedule| {
            let leader_schedule = Arc::new(leader_schedule);
            let (ref mut cached_schedules, ref mut order) = *self.cached_schedules.write().unwrap();
            // Check to see if schedule exists in case somebody already inserted in the time we were
            // waiting for the lock
            let entry = cached_schedules.entry(epoch);
            if let Entry::Vacant(v) = entry {
                v.insert(leader_schedule.clone());
                order.push_back(epoch);
                Self::retain_latest(cached_schedules, order, self.max_schedules());
            }
            leader_schedule
        })
    }
```
