Based on the evidence gathered, there is a concrete analog in agave's `ProgramCache` (the SVM's global loaded-programs cache), which exhibits the same root-cause pattern as the CVE: failed load attempts are cached keyed by an attacker-supplied identifier, and the cache's eviction logic never reclaims those failure entries.

### Title
Unbounded accumulation of program-cache tombstones for failed program loads enables validator memory-exhaustion DoS - (File: program-runtime/src/loaded_programs.rs)

### Summary
The global `ProgramCache` caches a `FailedVerification`/`Closed` "tombstone" entry for every account a transaction references as a program that turns out not to be a valid, loadable program. These tombstone entries are inserted keyed by the referenced `Pubkey` and are never included as eviction candidates, so they persist for the lifetime of the validator process. An unprivileged transaction sender fully controls the key (any account it names as a program in an instruction), and can grow this cache without bound by repeatedly referencing distinct never-before-seen pubkeys.

### Finding Description
`load_program_with_pubkey` in `svm/src/program_loader.rs` produces a `ProgramCacheEntry::new_tombstone(... ProgramCacheEntryType::FailedVerification(env))` whenever the referenced account can't be parsed/verified as a valid program (invalid account data, wrong loader state, etc.): [1](#0-0) 

This result flows through `TransactionBatchProcessor::replenish_program_cache`, which calls `finish_cooperative_loading_task` → `assign_program` to insert the entry into the global, process-wide `ProgramCache`: [2](#0-1) 

`assign_program` unconditionally inserts the new tombstone into the per-key `entries` map with no bound and no distinction for failure vs. success: [3](#0-2) 

Crucially, the cache's only eviction mechanisms (`sort_and_unload` and `evict_using_random_selection`) both operate exclusively on candidates returned by `get_flattened_entries()`, which filters to `ProgramCacheEntryType::Loaded(_)` only — tombstones (`FailedVerification`, `Closed`, `DelayVisibility`) and `Unloaded` entries are excluded from eviction consideration entirely: [4](#0-3) [5](#0-4) 

The project's own unit test (`test_random_eviction` / `test_eviction`) explicitly documents and asserts this behavior — "the original tombstones are not evicted": [6](#0-5) 

The only way tombstones are ever removed is `remove_programs_with_no_entries`, which only drops a key when its `second_level` vector of entries is *empty* — a lone tombstone keeps the vector non-empty, so it is never pruned: [7](#0-6) 

This is structurally identical to the reported CVE: `smart_require` in Net::OAuth stores every failed-load result in a process-global, unbounded hash keyed by attacker-influenced input and never evicts it. Here, `ProgramCache` stores every failed-verification result in a process-global map keyed by an attacker-chosen `Pubkey`, and the eviction routine is scoped to skip exactly this class of entry.

### Impact Explanation
Each distinct account address an attacker references as a "program" that fails to load creates one permanent `FailedVerification`/`Closed` tombstone entry in the validator's global `ProgramCache`. Because eviction never targets these entries, the set of tombstones grows monotonically with the number of unique failing pubkeys ever observed by the validator, for the life of the process. Sustained abuse increases heap usage in the program cache indefinitely, degrading validator memory headroom and, if left unchecked over long uptimes, contributing to OOM conditions — a resource-exhaustion / availability impact consistent with the "transaction-triggered cluster halt" class of concern, though it develops gradually rather than instantly.

### Likelihood Explanation
Any unprivileged client can trivially trigger this by submitting transactions that reference newly generated (never-before-seen) account pubkeys in place of a program id, or as writable/readonly accounts subsequently invoked as a program, causing account-data-parse or verification failure. This requires no special privileges, no validator bugs to trigger, and no CPI tricks — a wrong/garbage program account is enough. The main friction is transaction fee cost per unique key (unlike the CVE's single-request amplification), so growth rate is bounded by the attacker's spend rate rather than by a single request; this materially lowers the amplification factor of this analog relative to the original CVE.

### Recommendation
Include tombstone entries (`FailedVerification`, `Closed`, `DelayVisibility`) and `Unloaded` entries in the population considered by `get_flattened_entries()`/eviction candidates, or implement a separate bounded, LRU/probabilistic eviction policy specifically for tombstone entries so that failed-load results cannot accumulate without bound. Alternatively, cap total tombstone count and evict oldest-by-last-access when the cap is exceeded, mirroring the `evict_using_random_selection` treatment already applied to `Loaded` entries.

### Proof of Concept
1. Submit a sequence of otherwise-valid, minimal transactions from an unprivileged wallet, each referencing a distinct freshly generated `Pubkey` (owned by the system program, with no data, or with garbage account data) in an instruction's program-id slot.
2. Each such transaction causes `load_program_with_pubkey` to return `ProgramCacheEntryType::FailedVerification`, which is inserted into the global `ProgramCache` via `assign_program`.
3. Repeat with new unique pubkeys across many transactions/slots.
4. Observe (e.g., via `ProgramCache::get_flattened_entries_for_tests` or heap profiling) that the number of tombstone entries in the cache grows monotonically and is unaffected by `sort_and_unload`/`evict_using_random_selection` calls that occur during normal cache maintenance, confirming unbounded, unevictable growth as key count increases.

### Citations

**File:** svm/src/program_loader.rs (L180-187)
```rust
    .unwrap_or_else(|(deployment_slot, owner)| {
        let env = ProgramRuntimeEnvironment::clone(program_runtime_environment);
        ProgramCacheEntry::new_tombstone(
            deployment_slot,
            owner,
            ProgramCacheEntryType::FailedVerification(env),
        )
    });
```

**File:** svm/src/transaction_processor.rs (L941-959)
```rust
            if let Some((key, program, last_modification_slot)) = program_to_store {
                program_cache_for_tx_batch.loaded_missing = true;
                let mut global_program_cache = self.global_program_cache.write().unwrap();
                // Submit our last completed loading task.
                if global_program_cache.finish_cooperative_loading_task(
                    program_runtime_environment_for_execution,
                    self.slot,
                    key,
                    last_modification_slot,
                    program,
                ) && limit_to_load_programs
                {
                    // This branch is taken when there is an error in assigning a program to a
                    // cache slot. It is not possible to mock this error for SVM unit
                    // tests purposes.
                    *program_cache_for_tx_batch = ProgramCacheForTxBatch::new(self.slot);
                    program_cache_for_tx_batch.hit_max_limit = true;
                    return;
                }
```

**File:** program-runtime/src/loaded_programs.rs (L419-480)
```rust
        match &mut self.index {
            IndexImplementation::V1 { entries, .. } => {
                let slot_versions = &mut entries.entry(key).or_default();
                let insertion_point = slot_versions.binary_search_by(|at| {
                    at.deployment_slot
                        .cmp(&entry.deployment_slot)
                        .then(at.account_owner.cmp(&entry.account_owner))
                        .then(
                            // This `.then()` has no effect during normal operation.
                            // Only during the cache preparation phase this does allow entries
                            // which only differ in their environment to be interleaved in `slot_versions`.
                            is_current_env(
                                program_runtime_environment,
                                at.program.get_environment(),
                            )
                            .cmp(&is_current_env(
                                program_runtime_environment,
                                entry.program.get_environment(),
                            )),
                        )
                });
                match insertion_point {
                    Ok(index) => {
                        let existing = slot_versions.get_mut(index).unwrap();
                        match (&existing.program, &entry.program) {
                            (
                                ProgramCacheEntryType::Builtin(_),
                                ProgramCacheEntryType::Builtin(_),
                            )
                            | (ProgramCacheEntryType::Closed, ProgramCacheEntryType::Loaded(_))
                            | (
                                ProgramCacheEntryType::Closed,
                                ProgramCacheEntryType::FailedVerification(_),
                            )
                            | (
                                ProgramCacheEntryType::Unloaded(_),
                                ProgramCacheEntryType::Loaded(_),
                            )
                            | (
                                ProgramCacheEntryType::Unloaded(_),
                                ProgramCacheEntryType::FailedVerification(_),
                            ) => {}
                            _ => {
                                // Something is wrong, I can feel it ...
                                error!(
                                    "ProgramCache::assign_program() failed key={key:?} \
                                     existing={slot_versions:?} entry={entry:?}"
                                );
                                debug_assert!(false, "Unexpected replacement of an entry");
                                self.stats.replacements.fetch_add(1, Ordering::Relaxed);
                                return true;
                            }
                        }
                        entry.stats.merge_from(&existing.stats);
                        *existing = Arc::clone(&entry);
                        self.stats.reloads.fetch_add(1, Ordering::Relaxed);
                    }
                    Err(index) => {
                        self.stats.insertions.fetch_add(1, Ordering::Relaxed);
                        slot_versions.insert(index, Arc::clone(&entry));
                    }
                }
```

**File:** program-runtime/src/loaded_programs.rs (L822-837)
```rust
    /// Returns the list of entries which are verified and compiled.
    pub fn get_flattened_entries(&self) -> Vec<(Pubkey, Slot, Arc<ProgramCacheEntry>)> {
        match &self.index {
            IndexImplementation::V1 { entries, .. } => entries
                .iter()
                .flat_map(|(id, second_level)| {
                    second_level
                        .iter()
                        .filter_map(move |program| match program.program {
                            ProgramCacheEntryType::Loaded(_) => Some((*id, 0, program.clone())),
                            _ => None,
                        })
                })
                .collect(),
        }
    }
```

**File:** program-runtime/src/loaded_programs.rs (L862-930)
```rust
    /// Unloads programs which were used infrequently
    pub fn sort_and_unload(&mut self, shrink_to_percent: Percent) {
        let mut sorted_candidates = self.get_flattened_entries();
        sorted_candidates.sort_by_cached_key(|(_id, _last_modification_slot, program)| {
            program.stats.uses.load(Ordering::Relaxed)
        });
        let num_to_unload = sorted_candidates
            .len()
            .saturating_sub(percent_of_max_entries(shrink_to_percent));
        for (program, last_modification_slot, entry) in sorted_candidates.iter().take(num_to_unload)
        {
            self.unload_program_entry(*program, *last_modification_slot, entry);
        }
    }

    /// Evicts programs using random selection, choosing the worst scoring program out of the
    /// entries sampled.
    ///
    /// The eviction is performed enough number of times to reduce the cache usage to the given
    /// percentage.
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

**File:** program-runtime/src/loaded_programs.rs (L977-990)
```rust
    fn remove_programs_with_no_entries(&mut self) {
        match &mut self.index {
            IndexImplementation::V1 { entries, .. } => {
                let num_programs_before_removal = entries.len();
                entries.retain(|_key, second_level| !second_level.is_empty());
                if entries.len() < num_programs_before_removal {
                    self.stats.empty_entries.fetch_add(
                        num_programs_before_removal.saturating_sub(entries.len()) as u64,
                        Ordering::Relaxed,
                    );
                }
            }
        }
    }
```

**File:** program-runtime/src/loaded_programs.rs (L1216-1244)
```rust
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
```
