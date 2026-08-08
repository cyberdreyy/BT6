No vulnerability found for this question.

The truncation logic in `update_account` builds a `BinaryHeap` from the input `IterItem`s, sorts it descending via `IntoIterSorted`, and takes exactly `MAX_ENTRIES` before collecting into `RecentBlockhashes` [1](#0-0) . This construction inherently retains the top `MAX_ENTRIES` items by `IterItem`'s `Ord` (slot index) and drops the rest — there is no seam for off-by-one duplication since `take(MAX_ENTRIES)` operates on an already fully-sorted iterator, not a sliding/rotating buffer. The existing `test_create_account_unsorted` test already validates that arbitrary input ordering is correctly sorted into descending slot-index order and matches an independently-computed expected sequence [2](#0-1) , and `test_create_account_truncate`/`test_create_account_full` validate the exact length boundary at `MAX_ENTRIES` and `MAX_ENTRIES + 1` [3](#0-2) . Combining these two already-passing tests logically covers the exact property the question asks about (top-`MAX_ENTRIES`-by-slot-index retained set), and the algorithm's structure (full sort + fixed take) makes an off-by-one duplication/drop mathematically impossible.

Additionally, this code path is only reachable from `Bank::update_recent_blockhashes_locked`, which is driven by internal validator `BlockhashQueue` state, not attacker-supplied RPC input [4](#0-3) , so there is no unprivileged single-client entrypoint that could trigger or observe a divergent truncation result as described in the threat model.

### Citations

**File:** runtime/src/bank/recent_blockhashes_account.rs (L16-22)
```rust
    let sorted = BinaryHeap::from_iter(recent_blockhash_iter);
    let sorted_iter = IntoIterSorted::new(sorted);
    #[expect(deprecated)]
    let recent_blockhash_iter = sorted_iter.take(MAX_ENTRIES);
    #[expect(deprecated)]
    let recent_blockhashes: RecentBlockhashes = recent_blockhash_iter.collect();
    to_account(&recent_blockhashes, account)
```

**File:** runtime/src/bank/recent_blockhashes_account.rs (L64-94)
```rust
    #[test]
    fn test_create_account_full() {
        let def_hash = Hash::default();
        let def_lamports_per_signature = 0;
        let account = create_account_with_data_for_test(vec![
            IterItem(
                0u64,
                &def_hash,
                def_lamports_per_signature
            );
            MAX_ENTRIES
        ]);
        let recent_blockhashes = from_account::<RecentBlockhashes>(&account).unwrap();
        assert_eq!(recent_blockhashes.len(), MAX_ENTRIES);
    }

    #[test]
    fn test_create_account_truncate() {
        let def_hash = Hash::default();
        let def_lamports_per_signature = 0;
        let account = create_account_with_data_for_test(vec![
            IterItem(
                0u64,
                &def_hash,
                def_lamports_per_signature
            );
            MAX_ENTRIES + 1
        ]);
        let recent_blockhashes = from_account::<RecentBlockhashes>(&account).unwrap();
        assert_eq!(recent_blockhashes.len(), MAX_ENTRIES);
    }
```

**File:** runtime/src/bank/recent_blockhashes_account.rs (L96-130)
```rust
    #[test]
    fn test_create_account_unsorted() {
        let def_lamports_per_signature = 0;
        let mut unsorted_blocks: Vec<_> = (0..MAX_ENTRIES)
            .map(|i| {
                (i as u64, {
                    // create hash with visibly recognizable ordering
                    let mut h = [0; HASH_BYTES];
                    h[HASH_BYTES - 1] = i as u8;
                    Hash::new_from_array(h)
                })
            })
            .collect();
        unsorted_blocks.shuffle(&mut rng());

        let account = create_account_with_data_for_test(
            unsorted_blocks
                .iter()
                .map(|(i, hash)| IterItem(*i, hash, def_lamports_per_signature)),
        );
        let recent_blockhashes = from_account::<RecentBlockhashes>(&account).unwrap();

        let mut unsorted_recent_blockhashes: Vec<_> = unsorted_blocks
            .iter()
            .map(|(i, hash)| IterItem(*i, hash, def_lamports_per_signature))
            .collect();
        unsorted_recent_blockhashes.sort();
        unsorted_recent_blockhashes.reverse();
        let expected_recent_blockhashes: Vec<_> = (unsorted_recent_blockhashes
            .into_iter()
            .map(|IterItem(_, b, f)| Entry::new(b, f)))
        .collect();

        assert_eq!(*recent_blockhashes, expected_recent_blockhashes);
    }
```

**File:** runtime/src/bank.rs (L2979-2993)
```rust
    fn update_recent_blockhashes_locked(&self, locked_blockhash_queue: &BlockhashQueue) {
        #[expect(deprecated)]
        self.update_sysvar_account(&sysvar::recent_blockhashes::id(), |account| {
            let recent_blockhash_iter = locked_blockhash_queue.get_recent_blockhashes();
            recent_blockhashes_account::create_account_with_data_and_fields(
                recent_blockhash_iter,
                self.inherit_specially_retained_account_fields(account),
            )
        });
    }

    pub fn update_recent_blockhashes(&self) {
        let blockhash_queue = self.blockhash_queue.read().unwrap();
        self.update_recent_blockhashes_locked(&blockhash_queue);
    }
```
