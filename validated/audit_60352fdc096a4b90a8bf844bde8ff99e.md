### Title
Unbounded Growth of `FailedVerification`/`Closed` Tombstones in `ProgramCache` via Repeated Invocation of Unloadable Program Accounts - (File: `program-runtime/src/loaded_programs.rs`)

### Summary
`ProgramCache::evict_using_random_selection` (and its sibling `unload_program_entry`) only unloads entries whose `ProgramCacheEntryType` is `Loaded`; entries of type `FailedVerification`, `Closed`, or `DelayVisibility` ("tombstones") are explicitly exempted from capacity-based eviction. Every time a transaction invokes a program account whose data cannot be parsed/verified (e.g. a non-executable/invalid-bytecode account, or an account owned by a loader but with garbage `programdata`), `svm/src/program_loader.rs::load_program_with_pubkey` creates a permanent `FailedVerification`/`Closed` tombstone entry keyed by that account's pubkey and inserts it into the global, validator-wide `ProgramCache`. Because an unprivileged sender can cheaply create an unlimited number of distinct pubkeys (system-program `CreateAccount`) that are then referenced as "programs" in a CPI/instruction call, the cache's tombstone population can grow without any capacity-based bound, mirroring the OpenSSL CMP `extraCerts` issue where rejected content was cached indefinitely instead of being expunged.

### Finding Description
`ProgramCache` is a validator-global, per-fork-graph cache of program entries [1](#0-0) . When a transaction batch references a program pubkey that is not yet cached, the SVM loads and evaluates that account and produces a `ProgramCacheEntry`; if the account data is invalid or fails verification, a tombstone (`FailedVerification` or `Closed`) is produced instead of an executable entry [2](#0-1) .

The eviction routine that keeps the cache bounded, `evict_using_random_selection`, samples the flattened entry set and calls `unload_program_entry` on the losers [3](#0-2) . However, `unload_program_entry` only converts `Loaded` entries into `Unloaded`; tombstone variants are left untouched: [4](#0-3) 

`ProgramCacheEntry::is_tombstone` explicitly enumerates `FailedVerification`, `Closed`, and `DelayVisibility` as the tombstone set [5](#0-4) , and the code comment/design doc for the type states tombstones are only removed "Through pruning ... when on orphan fork or overshadowed on the rooted fork" [6](#0-5) . The unit test `test_random_eviction` explicitly documents and asserts this behavior — "Tombstones and unloaded entries are expected to not be evicted" — and confirms tombstone counts are unaffected by `evict_using_random_selection` [7](#0-6) .

Because tombstones are keyed per-pubkey (`IndexImplementation::V1 { entries: HashMap<Pubkey, ...> }`), and a new unique pubkey can be created cheaply by any unprivileged sender via a `CreateAccount` system instruction, an attacker can drive unbounded growth of the cache's tombstone population: for each new never-before-seen invalid "program" pubkey referenced in a transaction (e.g., an account created with garbage bytes, or one owned by a BPF loader but too short/invalid to parse), the loader inserts a brand-new tombstone entry that is exempt from the water-level/percentage-based eviction mechanism intended to bound overall cache memory.

This is structurally analogous to CVE-2026-63074: content associated with a *rejected* operation (a failed/invalid program) is cached and never expunged by the capacity-management path, only by an unrelated mechanism (fork pruning) that does not apply to rooted, still-referenced pubkeys.

### Impact Explanation
The `ProgramCache` is shared across the validator process and is not persisted or eagerly capacity-limited for tombstone entries. Sustained, cheap creation of unique invalid "program" pubkeys followed by transactions that attempt to invoke them causes unbounded heap growth in a long-lived validator process, which can lead to memory exhaustion (OOM) of the validator — a transaction-triggered resource-exhaustion condition matching the CWE-770 class described in the report. This does not directly cause fund loss or consensus divergence but can degrade or crash validator nodes over time if left unmitigated, satisfying the "transaction-triggered cluster-wide degradation" impact class.

### Likelihood Explanation
Likelihood is moderate-to-high in terms of mechanics (any unprivileged sender can create new pubkeys and reference them in CPI instructions cheaply — the dominant cost is rent for tiny/zero-data accounts and transaction fees), but the practical impact is gated by how large the tombstone set would need to grow to matter and whether tombstones carry non-trivial per-entry memory (they are small: enum variant + stats + deployment slot). This makes the “instructions rejected/never used again” growth pattern real but the per-entry footprint is small, so achieving OOM requires sustained, large-scale flooding rather than a single transaction — I was not able to fully verify the exact per-entry byte size or whether any other implicit cap (e.g., overall cache size limit checked elsewhere, index capacity) exists to bound `IndexImplementation::V1.entries` beyond `evict_using_random_selection`/pruning, due to not locating an explicit size cap enforcement point besides `percent_of_max_entries`/`shrink_to_percent`, which as shown does not apply to tombstones.

### Recommendation
Extend `ProgramCache`'s eviction accounting to include tombstone entries in the capacity/water-level calculation, or add an explicit, bounded eviction path for `FailedVerification`/`Closed` tombstones that are not on a rooted/referenced-and-recently-used pubkey (analogous to the OpenSSL fix, which removes rejected extraCerts using the same removal path as normal cache management). At minimum, tombstones should count toward `shrink_to_percent`-based sizing so that `evict_using_random_selection`/`sort_and_unload` can actually shrink the cache back down when it grows due to repeated invalid-program references, rather than only being removable via fork pruning.

### Proof of Concept
1. Submit repeated transactions from an unprivileged keypair, each: (a) creates a new system-owned account with a unique pubkey via `CreateAccount`, writing enough (or invalid) data so it appears loader-owned but fails ELF/verification when interpreted as a program, and (b) includes an instruction in the same or a subsequent transaction targeting that pubkey as the program id.
2. Each such invocation flows through `load_program_with_pubkey`, producing a new `ProgramCacheEntry::new_tombstone(..., FailedVerification(env))` inserted under a unique key [8](#0-7) .
3. Repeat with N unique pubkeys; observe that `ProgramCache::evict_using_random_selection`/`sort_and_unload` reduces `Loaded`/`Unloaded` counts but leaves the tombstone count (`num_tombstones`) unchanged, as demonstrated by `test_random_eviction`/`test_eviction` [9](#0-8) , confirming the tombstone set only grows and is never shrunk by the normal capacity-management mechanism.

### Citations

**File:** program-runtime/src/loaded_programs.rs (L233-259)
```rust
/// This structure is the global cache of loaded, verified and compiled programs.
///
/// It ...
/// - is validator global and fork graph aware, so it can optimize the commonalities across banks.
/// - handles the visibility rules of un/re/deployments.
/// - stores the usage statistics and verification status of each program.
/// - is elastic and uses a probabilistic eviction strategy based on the usage statistics.
/// - also keeps the compiled executables around, but only for the most used programs.
/// - supports various kinds of tombstones to avoid loading programs which can not be loaded.
/// - cleans up entries on orphan branches when the block store is rerooted.
/// - supports the cache preparation phase before feature activations which can change cached programs.
/// - manages the environments of the programs and upcoming environments for the next epoch.
/// - allows for cooperative loading of TX batches which hit the same missing programs simultaneously.
/// - enforces that all programs used in a batch are eagerly loaded ahead of execution.
/// - is not persisted to disk or a snapshot, so it needs to cold start and warm up first.
pub struct ProgramCache<FG: ForkGraph> {
    /// Index of the cached entries and cooperative loading tasks
    pub(crate) index: IndexImplementation,
    /// The slot of the last rerooting
    pub latest_root_slot: Slot,
    /// Statistics counters
    pub stats: ProgramCacheStats,
    /// Reference to the block store
    pub fork_graph: Option<Weak<RwLock<FG>>>,
    /// Coordinates TX batches waiting for others to complete their task during cooperative loading
    pub loading_task_waiter: Arc<LoadingTaskWaiter>,
}
```

**File:** program-runtime/src/loaded_programs.rs (L882-930)
```rust
    pub fn evict_using_random_selection(&mut self, shrink_to_percent: Percent, now: Slot) {
        let mut candidates = self.get_flattened_entries();
        let mut rng = rng();
        self.stats
            .water_level
            .store(candidates.len() as u64, Ordering::Relaxed);
        let num_to_unload = candidates
            .len()
            .saturating_sub(percent_of_max_entries(shrink_to_percent));
        let mut sample_entry = |candidates: &Vec<(Pubkey, u64, Arc<ProgramCacheEntry>)>| {
            // gen_range is deprecated in favor of random_range in rand>=0.9, but we also get
            // rnd() from shuttle, which doesn't yet support rand 0.9 APIs
            #[cfg(feature = "shuttle-test")]
            let index = rng.gen_range(0..candidates.len());
            #[cfg(not(feature = "shuttle-test"))]
            let index = rng.random_range(0..candidates.len());
            let usage_counter = candidates
                .get(index)
                .expect("Failed to get cached entry")
                .2
                .retention_score();
            (index, usage_counter)
        };

        // Random sampling with just 2 choices can frequently lead to a situation where both
        // entries chosen have relatively high retention scores, having us to pick one out of two
        // poor options. We can tell what a relatively high retention score is, so we can make a
        // few additional samples until we hit some other entry that isn't as highly scoring.
        //
        // Note that the "high enough" compilation time and use count numbers used here are
        // relatively arbitrary.
        const MAX_ADDITIONAL_SAMPLES: usize = 3;
        let avoid_evicting_above_score = retention_score(now, 500 * EMA_SCALE, 500);
        for _ in 0..num_to_unload {
            let (mut index, mut score) = sample_entry(&candidates);
            for _ in 0..MAX_ADDITIONAL_SAMPLES {
                let (sample_index, sample_score) = sample_entry(&candidates);
                if score > sample_score {
                    index = sample_index;
                    score = sample_score;
                }
                if score < avoid_evicting_above_score {
                    break;
                }
            }
            let (id, last_modification_slot, entry) = candidates.swap_remove(index);
            self.unload_program_entry(id, last_modification_slot, &entry);
        }
    }
```

**File:** program-runtime/src/loaded_programs.rs (L945-975)
```rust
    fn unload_program_entry(
        &mut self,
        id: Pubkey,
        _last_modification_slot: Slot,
        remove_entry: &Arc<ProgramCacheEntry>,
    ) {
        match &mut self.index {
            IndexImplementation::V1 { entries, .. } => {
                let second_level = entries.get_mut(&id).expect("Cache lookup failed");
                let candidate = second_level
                    .iter_mut()
                    .find(|entry| Arc::ptr_eq(entry, remove_entry))
                    .expect("Program entry not found");

                // Only loaded entries shall be unloaded by eviction.
                if let ProgramCacheEntryType::Loaded(_) = candidate.program
                    && let Some(unloaded) = candidate.to_unloaded()
                {
                    if candidate.stats.uses.load(Ordering::Relaxed) == 1 {
                        self.stats.one_hit_wonders.fetch_add(1, Ordering::Relaxed);
                    }
                    self.stats
                        .evictions
                        .entry(id)
                        .and_modify(|c| *c = c.saturating_add(1))
                        .or_insert(1);
                    *candidate = Arc::new(unloaded);
                }
            }
        }
    }
```

**File:** program-runtime/src/loaded_programs.rs (L1156-1245)
```rust
    #[test]
    fn test_random_eviction() {
        let mut programs = vec![];
        let mut cache = ProgramCache::<TestForkGraph>::new(0);

        // This test adds different kind of entries to the cache.
        // Tombstones and unloaded entries are expected to not be evicted.
        // It also adds multiple entries for three programs as it tries to create a typical cache instance.

        // Program 1
        program_deploy_test_helper(
            &mut cache,
            Pubkey::new_unique(),
            vec![0, 10, 20],
            vec![4, 5, 25],
            &mut programs,
        );

        // Program 2
        program_deploy_test_helper(
            &mut cache,
            Pubkey::new_unique(),
            vec![5, 11],
            vec![0, 2],
            &mut programs,
        );

        // Program 3
        program_deploy_test_helper(
            &mut cache,
            Pubkey::new_unique(),
            vec![0, 5, 15],
            vec![100, 3, 20],
            &mut programs,
        );

        // 1 for each deployment slot
        let num_loaded_expected = 8;
        // 10 for each program
        let num_unloaded_expected = 30;
        // 10 for each program
        let num_tombstones_expected = 30;

        // Count the number of loaded, unloaded and tombstone entries.
        programs.sort_by_key(|(_id, _slot, usage_count)| *usage_count);
        let num_loaded = num_matching_entries(&cache, |program_type| {
            matches!(program_type, ProgramCacheEntryType::Loaded(_))
        });
        let num_unloaded = num_matching_entries(&cache, |program_type| {
            matches!(program_type, ProgramCacheEntryType::Unloaded(_))
        });
        let num_tombstones = num_matching_entries(&cache, |program_type| {
            matches!(
                program_type,
                ProgramCacheEntryType::DelayVisibility
                    | ProgramCacheEntryType::FailedVerification(_)
                    | ProgramCacheEntryType::Closed
            )
        });

        // Test that the cache is constructed with the expected number of entries.
        assert_eq!(num_loaded, num_loaded_expected);
        assert_eq!(num_unloaded, num_unloaded_expected);
        assert_eq!(num_tombstones, num_tombstones_expected);

        // Evict entries from the cache
        let eviction_pct: Percent = 1;

        let num_loaded_expected = crate::loaded_programs::percent_of_max_entries(eviction_pct);
        let num_unloaded_expected = num_unloaded_expected + num_loaded - num_loaded_expected;
        cache.evict_using_random_selection(eviction_pct, 21);

        // Count the number of loaded, unloaded and tombstone entries.
        let num_loaded = num_matching_entries(&cache, |program_type| {
            matches!(program_type, ProgramCacheEntryType::Loaded(_))
        });
        let num_unloaded = num_matching_entries(&cache, |program_type| {
            matches!(program_type, ProgramCacheEntryType::Unloaded(_))
        });
        let num_tombstones = num_matching_entries(&cache, |program_type| {
            matches!(program_type, ProgramCacheEntryType::FailedVerification(_))
        });

        // However many entries are left after the shrink
        assert_eq!(num_loaded, num_loaded_expected);
        // The original unloaded entries + the evicted loaded entries
        assert_eq!(num_unloaded, num_unloaded_expected);
        // The original tombstones are not evicted
        assert_eq!(num_tombstones, num_tombstones_expected);
    }
```

**File:** svm/src/program_loader.rs (L114-192)
```rust
    let (load_result, last_modification_slot) = load_program_accounts(callbacks, pubkey)?;
    let loaded_program = match load_result {
        ProgramAccountLoadResult::InvalidAccountData(owner) => Ok(
            ProgramCacheEntry::new_tombstone(current_slot, owner, ProgramCacheEntryType::Closed),
        ),

        ProgramAccountLoadResult::ProgramOfLoaderV1(program_account) => ProgramCacheEntry::new(
            program_account.owner(),
            ProgramRuntimeEnvironment::clone(program_runtime_environment),
            0,
            program_account.data(),
            #[cfg(feature = "metrics")]
            &mut load_program_metrics,
        )
        .map_err(|_| (0, ProgramCacheEntryOwner::LoaderV1)),

        ProgramAccountLoadResult::ProgramOfLoaderV2(program_account) => ProgramCacheEntry::new(
            program_account.owner(),
            ProgramRuntimeEnvironment::clone(program_runtime_environment),
            0,
            program_account.data(),
            #[cfg(feature = "metrics")]
            &mut load_program_metrics,
        )
        .map_err(|_| (0, ProgramCacheEntryOwner::LoaderV2)),

        ProgramAccountLoadResult::ProgramOfLoaderV3(
            program_account,
            programdata_account,
            deployment_slot,
        ) => programdata_account
            .data()
            .get(UpgradeableLoaderState::size_of_programdata_metadata()..)
            .ok_or(())
            .and_then(|programdata| {
                ProgramCacheEntry::new(
                    program_account.owner(),
                    ProgramRuntimeEnvironment::clone(program_runtime_environment),
                    deployment_slot,
                    programdata,
                    #[cfg(feature = "metrics")]
                    &mut load_program_metrics,
                )
                .map_err(|_| ())
            })
            .map_err(|_| (deployment_slot, ProgramCacheEntryOwner::LoaderV3)),

        ProgramAccountLoadResult::ProgramOfLoaderV4(program_account, deployment_slot) => {
            program_account
                .data()
                .get(LoaderV4State::program_data_offset()..)
                .ok_or(())
                .and_then(|elf_bytes| {
                    ProgramCacheEntry::new(
                        &loader_v4::id(),
                        ProgramRuntimeEnvironment::clone(program_runtime_environment),
                        deployment_slot,
                        elf_bytes,
                        #[cfg(feature = "metrics")]
                        &mut load_program_metrics,
                    )
                    .map_err(|_| ())
                })
                .map_err(|_| (deployment_slot, ProgramCacheEntryOwner::LoaderV4))
        }
    }
    .unwrap_or_else(|(deployment_slot, owner)| {
        let env = ProgramRuntimeEnvironment::clone(program_runtime_environment);
        ProgramCacheEntry::new_tombstone(
            deployment_slot,
            owner,
            ProgramCacheEntryType::FailedVerification(env),
        )
    });

    #[cfg(feature = "metrics")]
    load_program_metrics.submit_datapoint(&mut execute_timings.details);
    loaded_program.update_access_slot(current_slot);
    Some((Arc::new(loaded_program), last_modification_slot))
```

**File:** program-runtime/src/program_cache_entry.rs (L94-97)
```rust
    Through pruning:
    - Closed / Unloaded / Loaded / Builtin => Empty in ProgramCache::prune (when on orphan fork or overshadowed on the rooted fork)
    - FailedVerification / Unloaded / Loaded => Unloaded in ProgramCache::prune (when on outdated program runtime environment)
*/
```

**File:** program-runtime/src/program_cache_entry.rs (L354-361)
```rust
    pub fn is_tombstone(&self) -> bool {
        matches!(
            self.program,
            ProgramCacheEntryType::FailedVerification(_)
                | ProgramCacheEntryType::Closed
                | ProgramCacheEntryType::DelayVisibility
        )
    }
```
