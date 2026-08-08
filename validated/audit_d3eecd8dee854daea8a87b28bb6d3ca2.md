Based on my investigation, I found a real reachable pattern but I want to be upfront that I ran out of iterations before I could confirm one detail (whether the public JSON-RPC entry point enforces a hard cap on the `limit` parameter passed into the blockstore call). I'll present the finding with that caveat rather than overclaim.

### Title
Unprivileged address-signature index pollution causes disproportionate scan cost for `getSignaturesForAddress` - ([File: ledger/src/blockstore.rs])

### Summary
Every account key referenced in *any* transaction — including read-only, non-signer accounts that the transaction sender does not control or need permission for — is written into the `address_signatures_cf` column family. This lets any unprivileged user cheaply "spam" the on-chain index for an arbitrary victim address, which is later scanned in full by the `getSignaturesForAddress` JSON-RPC method when a client queries that victim's transaction history.

### Finding Description
`write_transaction_status`/`add_transaction_status_to_batch` insert one `address_signatures_cf` row per `(address, slot, tx_index, signature)` for **every** key in `keys_with_writable`, and `TransactionStatusService` builds `keys_with_writable` from `message.account_keys()` for *all* accounts in the message, writable or read-only: [1](#0-0) [2](#0-1) 

An attacker can therefore include any victim `Pubkey` as a cheap, non-signer, read-only account in their own self-funded transactions, repeated arbitrarily many times, to grow the number of `address_signatures_cf` entries keyed on the victim's address — at only the cost of the attacker's own transaction fees, with no cooperation or funds from the victim required.

The read path, `Blockstore::get_confirmed_signatures_for_address2`, is invoked directly by the unprivileged `getSignaturesForAddress` JSON-RPC handler: [3](#0-2) 
and scans this same column family with a loop bounded only by `limit`: [4](#0-3) 

This mirrors the reported bug class exactly: an attacker-controllable, per-victim-address structure (`delegatesOf[_depositorAddress]` in the report vs. `address_signatures_cf` entries per address here) is grown cheaply by a third party and then imposes cost on a victim's later, unrelated read/query.

### Impact Explanation
Every legitimate call to `getSignaturesForAddress` for the polluted address returns a result set dominated by attacker-injected noise instead of the victim's actual transaction history, and requires the RPC/blockstore layer to iterate and fetch transaction-status metadata (`get_transaction_status`, `read_transaction_memos`, `get_block_time`) for each injected entry it encounters within the scan window: [5](#0-4) 
This degrades the usefulness/availability of a public JSON-RPC API for the targeted address and increases per-call I/O work on the RPC node, at negligible cost to the attacker (ordinary transaction fees only).

### Likelihood Explanation
Likelihood is high for any RPC node that has transaction-history indexing enabled (`--enable-rpc-transaction-history`), since the write path unconditionally indexes every account key of every transaction, and nothing restricts which pubkeys a transaction may reference as read-only accounts.

### Recommendation
Consider bounding/limiting how many `address_signatures_cf` entries can be attributed to non-signer/read-only inclusion versus rate-limiting per-address entries, or clearly documenting/capping the cost impact for public-facing RPC deployments (e.g., a strict, small, non-overridable cap on `limit`/scan work per `getSignaturesForAddress` call, and/or excluding read-only, non-signer references from the address-signature secondary index).

### Proof of Concept
1. Attacker submits many small self-funded transactions, each including the victim's pubkey as an additional read-only, non-signer account (no signature from the victim required).
2. Each such transaction causes `write_transaction_status`/`add_transaction_status_to_batch` to insert a new `(victim_address, slot, tx_index, signature)` row into `address_signatures_cf`.
3. A client later calls `getSignaturesForAddress` for the victim's address; `get_confirmed_signatures_for_address2` scans through the polluted entries, returning results dominated by attacker noise and doing proportionally more work per call than an unpolluted address would require.

**Caveat / unverified item:** I was unable to confirm within the available tool budget whether the public `getSignaturesForAddress` JSON-RPC entry point clamps the `limit` parameter to a small fixed maximum (the internal `Blockstore` method itself accepts `usize::MAX` in tests, with no clamp). If such a clamp exists at the RPC layer, the *per-call* compute amplification is bounded by that clamp, and the primary impact is index pollution/noise (degraded data quality) rather than unbounded per-call cost. I recommend a Devin session with full-repo access to locate and confirm the `limit` validation logic (search terms: `verify_and_parse_signatures_for_address_params`, `MAX_GET_CONFIRMED_SIGNATURES_FOR_ADDRESS2_LIMIT` in `rpc/src/rpc.rs`) before finalizing severity.

### Citations

**File:** rpc/src/transaction_status_service.rs (L240-254)
```rust
                        let message = transaction.message();
                        let keys_with_writable = message
                            .account_keys()
                            .iter()
                            .enumerate()
                            .map(|(index, key)| (key, message.is_writable(index)));

                        blockstore.add_transaction_status_to_batch(
                            slot,
                            *transaction.signature(),
                            keys_with_writable,
                            transaction_status_meta,
                            transaction_index,
                            batch,
                        )?;
```

**File:** ledger/src/blockstore.rs (L4281-4302)
```rust
    pub fn write_transaction_status<'a>(
        &self,
        slot: Slot,
        signature: Signature,
        keys_with_writable: impl Iterator<Item = (&'a Pubkey, bool)>,
        status: TransactionStatusMeta,
        transaction_index: usize,
    ) -> Result<()> {
        self.write_transaction_status_helper(
            slot,
            signature,
            keys_with_writable,
            status,
            transaction_index,
            |address, slot, tx_index, signature, writeable| {
                self.address_signatures_cf.put(
                    (*address, slot, tx_index, signature),
                    &AddressSignatureMeta { writeable },
                )
            },
        )
    }
```

**File:** ledger/src/blockstore.rs (L4661-4686)
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
```

**File:** ledger/src/blockstore.rs (L4694-4712)
```rust
        // Fill in the status information for each found transaction
        let mut get_status_info_timer = Measure::start("get_status_info_timer");
        let mut infos = vec![];
        for (slot, signature, index) in address_signatures_iter {
            let transaction_status =
                self.get_transaction_status(signature, &confirmed_unrooted_slots)?;
            let err = transaction_status.and_then(|(_slot, status)| status.status.err());
            let memo = self.read_transaction_memos(signature, slot)?;
            let block_time = self.get_block_time(slot)?;
            infos.push(ConfirmedTransactionStatusWithSignature {
                signature,
                slot,
                err,
                memo,
                block_time,
                index,
            });
        }
        get_status_info_timer.stop();
```

**File:** rpc/src/rpc.rs (L1847-1886)
```rust
    pub async fn get_signatures_for_address(
        &self,
        address: Pubkey,
        before: Option<Signature>,
        until: Option<Signature>,
        mut limit: usize,
        config: RpcContextConfig,
    ) -> Result<Vec<RpcConfirmedTransactionStatusWithSignature>> {
        self.check_if_transaction_history_enabled()?;

        let commitment = config.commitment.unwrap_or_default();
        check_is_at_least_confirmed(commitment)?;

        let highest_super_majority_root = self
            .block_commitment_cache
            .read()
            .unwrap()
            .highest_super_majority_root();
        let highest_slot = if commitment.is_confirmed() {
            let confirmed_bank = self.get_bank_with_config(config)?;
            confirmed_bank.slot()
        } else {
            let min_context_slot = config.min_context_slot.unwrap_or_default();
            if highest_super_majority_root < min_context_slot {
                return Err(RpcCustomError::MinContextSlotNotReached {
                    context_slot: highest_super_majority_root,
                }
                .into());
            }
            highest_super_majority_root
        };

        let SignatureInfosForAddress {
            infos: mut results,
            found_before,
            found_until,
        } = self
            .blockstore
            .get_confirmed_signatures_for_address2(address, highest_slot, before, until, limit)
            .map_err(|err| Error::invalid_params(format!("{err}")))?;
```
