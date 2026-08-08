Confirmed: the `AddressSignatures` column key is `(Pubkey, Slot, u32, Signature)` with `Pubkey` as the leading sort component, so a reverse RocksDB iterator started at `(address, slot, 0, Signature::default())` will, once it exhausts entries for `address`, continue walking backward into the entries of lexicographically-preceding addresses, since the loop in `get_confirmed_signatures_for_address2` has no `key_address == address` short-circuit break — it simply loops again when the key doesn't match. [1](#0-0) [2](#0-1) 

This is invoked directly from the unprivileged `getSignaturesForAddress` JSON-RPC method with an attacker-supplied `address` and no `before`/`until`. [3](#0-2) [4](#0-3) 

### Title
Single-address `getSignaturesForAddress` query with a non-existent/rarely-used address forces an unbounded full ledger scan across all addresses - (File: ledger/src/blockstore.rs)

### Summary
The `getSignaturesForAddress` RPC handler (guarded only by `--enable-rpc-transaction-history`, otherwise open to any unprivileged JSON-RPC client) calls `Blockstore::get_confirmed_signatures_for_address2`, which uses a single reverse RocksDB iterator over the `AddressSignatures` column family to find `limit` (up to 1000) matching entries for the requested `address`.

### Finding Description
The `AddressSignatures` column is keyed by `(Pubkey, Slot, u32, Signature)`, with `Pubkey` as the primary/leading sort key [1](#0-0) . The scanning loop is:

```rust
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
``` [5](#0-4) 

When `key_address != address`, there is no `break`; the loop simply calls `iterator.next()` again. Because the iterator direction is `Reverse` and the key is primarily sorted by `Pubkey`, once the entries belonging to the requested `address` are exhausted (or if that address has zero entries at all), the iterator keeps walking backward through the entries of every lexicographically-preceding address in the entire column family. The loop only terminates early via `slot < lowest_slot`, where `lowest_slot` defaults to `first_available_block` when `until` is not supplied [6](#0-5) , which on a long-lived validator can be very low (deep ledger history), or when the whole column family is exhausted. Since `limit` defaults to 1000 [7](#0-6)  and can never be satisfied for an address with few/no transactions, this call degenerates into an essentially full reverse scan of the entire `AddressSignatures` column family.

### Impact Explanation
A single unprivileged, low-rate `getSignaturesForAddress` request supplying an unused (or randomly generated) `Pubkey` forces the validator's blocking RPC worker thread to perform a RocksDB scan proportional to the total historical size of the `AddressSignatures` column (potentially the entire retained ledger history), touching every address ever seen. This produces disproportionate CPU/I/O cost for a trivially cheap request, degrading RPC responsiveness and consuming validator resources, matching the "unbounded cost for a single low-rate call" impact class.

### Likelihood Explanation
This requires only that `--enable-rpc-transaction-history` be enabled (a common configuration for RPC-serving validators) and a single JSON-RPC call with an attacker-chosen address that has no recorded signatures (trivial to construct, e.g., a freshly generated keypair). No authentication or special privilege is needed, and the call is fully within the documented public RPC surface.

### Recommendation
Add an explicit termination condition to the reverse-iteration loop when `key_address != address` — i.e., break (or seek directly to the next boundary of the requested address) rather than continuing to scan unrelated addresses' entries. This can be efficiently implemented by detecting the address change and either terminating the scan (since keys sorted by `Pubkey` mean once a different, "smaller" pubkey is seen, no further entries for the original address will follow) or reseeking directly rather than linear-scanning.

### Proof of Concept
1. Start a validator/RPC node with `--enable-rpc-transaction-history` on a ledger with a large amount of historical transaction data (many distinct addresses in `AddressSignatures`).
2. Generate a fresh keypair `attacker_addr` that has never transacted.
3. Issue a single JSON-RPC call:
```json
{"jsonrpc":"2.0","id":1,"method":"getSignaturesForAddress","params":["<attacker_addr>"]}
```
4. Observe (e.g., via the existing `address_signatures_iter_timer` instrumentation [8](#0-7) ) that the RPC thread performs a reverse scan touching a number of RocksDB entries proportional to the size of the entire `AddressSignatures` column family (all addresses, not just the queried one), rather than terminating quickly for an address with zero matches.

### Citations

**File:** ledger/src/blockstore/column.rs (L404-419)
```rust
impl Column for columns::AddressSignatures {
    type Index = (Pubkey, Slot, /*transaction index:*/ u32, Signature);
    type Key = [u8; PUBKEY_BYTES
        + std::mem::size_of::<Slot>()
        + std::mem::size_of::<u32>()
        + SIGNATURE_BYTES];

    #[inline]
    fn key((pubkey, slot, transaction_index, signature): &Self::Index) -> Self::Key {
        convert_column_index_to_key_bytes!(Key,
              ..32 => pubkey.as_ref(),
            32..40 => &slot.to_be_bytes(),
            40..44 => &transaction_index.to_be_bytes(),
            44..   => signature.as_ref(),
        )
    }
```

**File:** ledger/src/blockstore.rs (L4615-4620)
```rust
        let first_available_block = self.get_first_available_block()?;
        // Generate a HashSet of signatures that should be excluded from the results based on
        // `until` signature
        let mut get_until_slot_timer = Measure::start("get_until_slot_timer");
        let (lowest_slot, until_excluded_signatures, found_until) = match until {
            None => (first_available_block, HashSet::new(), false),
```

**File:** ledger/src/blockstore.rs (L4661-4687)
```rust
        let mut address_signatures_iter_timer = Measure::start("iter_timer");
        let mut iterator = self.address_signatures_cf.iter(IteratorMode::From(
            // Regardless of whether a `before` signature is provided, the latest relevant
            // `slot` is queried directly with the `find_address_signatures_for_slot()`
            // call above. Thus, this iterator starts at the lowest entry of `address,
            // slot` and iterates backwards to continue reporting the next earliest
            // signatures.
            (address, slot, 0, Signature::default()),
            IteratorDirection::Reverse,
        ))?;

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
        address_signatures_iter_timer.stop();
```

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

**File:** rpc/src/rpc.rs (L4233-4265)
```rust
        fn get_signatures_for_address(
            &self,
            meta: Self::Metadata,
            address: String,
            config: Option<RpcSignaturesForAddressConfig>,
        ) -> BoxFuture<Result<Vec<RpcConfirmedTransactionStatusWithSignature>>> {
            let RpcSignaturesForAddressConfig {
                before,
                until,
                limit,
                commitment,
                min_context_slot,
            } = config.unwrap_or_default();
            let verification =
                verify_and_parse_signatures_for_address_params(address, before, until, limit);

            match verification {
                Err(err) => Box::pin(future::err(err)),
                Ok((address, before, until, limit)) => Box::pin(async move {
                    meta.get_signatures_for_address(
                        address,
                        before,
                        until,
                        limit,
                        RpcContextConfig {
                            commitment,
                            min_context_slot,
                        },
                    )
                    .await
                }),
            }
        }
```

**File:** rpc-client-types/src/request.rs (L152-156)
```rust
pub const MAX_GET_SIGNATURE_STATUSES_QUERY_ITEMS: usize = 256;
pub const MAX_GET_CONFIRMED_SIGNATURES_FOR_ADDRESS_SLOT_RANGE: u64 = 10_000;
pub const MAX_GET_CONFIRMED_BLOCKS_RANGE: u64 = 500_000;
pub const MAX_GET_CONFIRMED_SIGNATURES_FOR_ADDRESS2_LIMIT: usize = 1_000;
pub const MAX_MULTIPLE_ACCOUNTS: usize = 100;
```
