### Title
Unbounded RocksDB iteration in `get_confirmed_signatures_for_address2` when an address has many non-rooted/non-confirmed `AddressSignatures` entries - ([File: ledger/src/blockstore.rs])

### Summary
`Blockstore::get_confirmed_signatures_for_address2` iterates the `AddressSignatures` column in reverse and only stops the loop when `address_signatures.len() == limit`, but entries belonging to slots that are neither rooted nor in `confirmed_unrooted_slots` are skipped via `continue` without incrementing `address_signatures.len()`. This makes the iteration cost proportional to the total number of `AddressSignatures` rows written for that address across all forks still present in the ledger, not to the caller-supplied `limit`.

### Finding Description
The RPC entrypoint `JsonRpcRequestProcessor::get_signatures_for_address` (`rpc/src/rpc.rs`) passes the caller's `limit` straight into `Blockstore::get_confirmed_signatures_for_address2`: [1](#0-0) 

Inside the blockstore function, the terminating loop is: [2](#0-1) 

`confirmed_unrooted_slots` is only populated from the ancestor chain of `highest_slot` down to `max_root`: [3](#0-2) 

so any slot that produced an `AddressSignatures` entry for the address but is not an ancestor of `highest_slot` (e.g., a pruned/losing fork still retained in the blockstore column families) and is not itself rooted, will hit the `if key_address == address { ...; continue; }` branch without ever pushing to `address_signatures` and without ever breaking the loop, because the loop only exits when `key_address != address` (a different key entirely) or `slot < lowest_slot`. As long as the reverse iterator keeps encountering rows keyed to the same `address`, it keeps calling `.next()` on the RocksDB iterator regardless of `limit`. This means the number of RocksDB iterator steps for a single call is bounded by the count of `AddressSignatures` rows for that address in non-qualifying slots, not by `limit`.

The existing guard `is_root(slot) || confirmed_unrooted_slots.contains(&slot)` correctly filters *which entries are returned*, but does nothing to bound *how many entries must be scanned* to satisfy `limit`. There is no fallback size cap, iteration-step limit, or per-call timeout in this function.

### Impact Explanation
This matches the "unbounded cost for a single low-rate call" category: a single `getSignaturesForAddress`/`getConfirmedSignaturesForAddress2` JSON-RPC call can force the validator's RPC thread to perform a RocksDB reverse scan whose length is proportional to on-chain-authored data volume (number of non-qualifying `AddressSignatures` rows for the address) rather than the requested `limit`, consuming CPU/I/O time disproportionate to the request and degrading RPC responsiveness for that node.

### Likelihood Explanation
Feasibility requires only that many `AddressSignatures` entries exist for a single address in slots that are neither rooted nor within `confirmed_unrooted_slots` of the queried `highest_slot`/`highest_super_majority_root`, and that those rows have not yet been purged from the ledger (purge is driven by the ledger-cleanup retention window, not immediately after a slot becomes non-canonical). Any address that participates in transactions across forks that end up losing (common on validators tracking multiple forks before finalization, or via replay of many non-rooted blocks still within the cleanup window) accumulates such rows. A single call with a modest `limit` (e.g. 1000) is sufficient to trigger the unbounded scan, satisfying the "one call per `CLUSTER_SLOT_TIME_TARGET/2`" constraint.

### Recommendation
Bound the reverse-iteration step count independently of `limit` — e.g., track a maximum number of `iterator.next()` calls (or a wall-clock budget) inside the `while` loop in `get_confirmed_signatures_for_address2`, and return a partial/paged result (or an error indicating the search was truncated) once that budget is exhausted, instead of allowing an unbounded scan driven purely by non-qualifying entries for the same address.

### Proof of Concept
Extend `test_get_confirmed_signatures_for_address2` (in `ledger/src/blockstore/tests.rs`) as follows:
1. Insert transaction statuses for `address0` across a large number `N` (e.g. 50,000) of distinct slots that are all set as *non-rooted* and are *not* ancestors of the `highest_slot` passed to the query (so none satisfy `is_root(slot) || confirmed_unrooted_slots.contains(&slot)`).
2. Insert a small number of qualifying (rooted) entries for `address0` before those N slots in reverse-iteration order (i.e., at lower slot numbers, since iteration is reverse from `slot` downward), so the loop must first scan through all N non-qualifying rows.
3. Call `blockstore.get_confirmed_signatures_for_address2(address0, highest_slot, None, None, 5)` with a small `limit`.
4. Instrument or wrap the RocksDB iterator (or measure wall-clock time) and assert that the number of `iterator.next()` calls / elapsed time scales linearly with `N`, despite `limit == 5`, demonstrating that cost is governed by attacker-controlled on-chain history volume rather than the requested `limit`.

### Citations

**File:** rpc/src/rpc.rs (L1879-1886)
```rust
        let SignatureInfosForAddress {
            infos: mut results,
            found_before,
            found_until,
        } = self
            .blockstore
            .get_confirmed_signatures_for_address2(address, highest_slot, before, until, limit)
            .map_err(|err| Error::invalid_params(format!("{err}")))?;
```

**File:** ledger/src/blockstore.rs (L4582-4586)
```rust
        let max_root = self.max_root();
        let confirmed_unrooted_slots: HashSet<_> =
            AncestorIterator::new_inclusive(highest_slot, self)
                .take_while(|&slot| slot > max_root)
                .collect();
```

**File:** ledger/src/blockstore.rs (L4672-4686)
```rust
        // Iterate until limit is reached
        while address_signatures.len() < limit {
            if let Some(((key_address, slot, transaction_index, signature), _)) = iterator.next() {
                if slot < lowest_slot {
                    break;
                }
                if key_address == address {
                    if self.is_root(slot) || confirmed_unrooted_slots.contains(&slot) {
                        address_signatures.push((slot, signature, transaction_index));
                    }
                    continue;
                }
            }
            break;
        }
```
