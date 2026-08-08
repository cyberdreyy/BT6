Based on my investigation, I found a concrete analog: for V1-format transactions, `compute_unit_limit` is clamped independently of `priority_fee_lamports`, and the resulting inconsistent pair feeds directly into `getRecentPrioritizationFees`, an unprivileged RPC method.

### Title
`compute_unit_price_in_microlamports` miscalculates effective price for V1 transactions because `compute_unit_limit` is clamped but `priority_fee_lamports` is not - ([File: runtime-transaction/src/transaction_meta.rs])

### Summary
`VersionedTransactionConfiguration::try_into_config` for `V1` transactions clamps `compute_unit_limit` to `MAX_COMPUTE_UNIT_LIMIT` but leaves `priority_fee_lamports` untouched, since for V1 messages the priority fee is submitted directly as a lamport amount rather than derived from `compute_unit_price * compute_unit_limit`. [1](#0-0)  This mirrors the reported bug class: one derived/capped value (`collateralClaim`/here `compute_unit_limit`) is clamped in isolation while a related accounting value (`debtRepaying`/here `priority_fee_lamports`) is used unmodified downstream, producing an inconsistent pair that is then used together in further math.

### Finding Description
`compute_unit_price_in_microlamports()` derives an "effective price per compute unit" as `priority_fee_lamports * 1_000_000 / compute_unit_limit`. [2](#0-1)  For legacy/V0 transactions this is safe because the prioritization fee is computed *from* the already-clamped `compute_unit_limit`, so numerator and denominator are always consistent. [3](#0-2)  For V1 transactions, however, `priority_fee_lamports` and `compute_unit_limit` are independently supplied fields on `TransactionConfig`, and only `compute_unit_limit` is clamped to `MAX_COMPUTE_UNIT_LIMIT` in `try_into_config`; `priority_fee_lamports` passes through unchanged. [4](#0-3) [5](#0-4)  A test in the same file, `test_try_into_config_v1_clamps_compute_unit_limit`, explicitly demonstrates that `compute_unit_limit` becomes `MAX_COMPUTE_UNIT_LIMIT` while `priority_fee_lamports` stays at its original, uncorrelated value. [6](#0-5)  This `TransactionConfiguration` (and thus the mismatched pair) flows straight into `PrioritizationFeeCache::update`, which calls `transaction_configuration.compute_unit_price_in_microlamports()` and stores the result as the transaction's cached "compute unit price." [7](#0-6)  That cached value is directly served by the unprivileged `getRecentPrioritizationFees` JSON-RPC method via `get_prioritization_fees`. [8](#0-7) [9](#0-8) 

### Impact Explanation
Any user can craft a V1 transaction requesting a `compute_unit_limit` far above `MAX_COMPUTE_UNIT_LIMIT` alongside an arbitrary `priority_fee_lamports`. Because the divisor is silently clamped down to `MAX_COMPUTE_UNIT_LIMIT` while the numerator is untouched, the reported "compute unit price" returned by `getRecentPrioritizationFees` can be inflated far beyond what the transaction actually paid per compute unit, or conversely understated depending on the combination chosen. This is wrong data returned from an unprivileged query (misreporting of prioritization-fee market data), which other users and fee-estimation tooling rely on to set competitive priority fees — a form of economic/consensus-adjacent state misreporting reachable with a single RPC-visible transaction, matching the "wrong ... data returned" and "decoder/misreporting" impact classes.

### Likelihood Explanation
Likelihood is high: constructing a V1 transaction with a `compute_unit_limit` above `MAX_COMPUTE_UNIT_LIMIT` and an arbitrary `priority_fee_lamports` requires no special privileges, and the mismatched values propagate unconditionally through `PrioritizationFeeCache::update` into the RPC-exposed cache — no race condition, no multiple calls, and no additional preconditions are needed beyond having the transaction processed by a bank.

### Recommendation
When clamping `compute_unit_limit` in the `V1` branch of `try_into_config`, either reject transactions whose `compute_unit_limit` exceeds `MAX_COMPUTE_UNIT_LIMIT` (consistent with how out-of-range values are already rejected for `updated_heap_bytes`), or recompute/scale `priority_fee_lamports` so that the reported effective price stays consistent with the clamped limit actually used for execution and fee accounting.

### Proof of Concept
1. Build a V1 `TransactionConfig` with `compute_unit_limit = MAX_COMPUTE_UNIT_LIMIT + N` (any large `N`) and `priority_fee_lamports = P` (fixed, small value).
2. Submit/process the transaction; `try_into_config` clamps `compute_unit_limit` to `MAX_COMPUTE_UNIT_LIMIT` but leaves `priority_fee_lamports = P` unchanged, as shown by `test_try_into_config_v1_clamps_compute_unit_limit`. [6](#0-5) 
3. `PrioritizationFeeCache::update` computes `compute_unit_price_in_microlamports = P * 1_000_000 / MAX_COMPUTE_UNIT_LIMIT`, which no longer reflects the price the transaction was actually built around (i.e., `P / N` where `N` was the originally intended, much larger, limit). [2](#0-1) [10](#0-9) 
4. Any client calling `getRecentPrioritizationFees` for the accounts written by this transaction observes this skewed, misreported price. [9](#0-8) 

**Note on limitations:** I was not able to fully trace whether V1-format transactions are currently reachable end-to-end in production (gated behind the `enable_tx_v1` feature per `runtime/src/bank/check_transactions.rs`), which affects current exploitability until that feature is active. [11](#0-10)  If this feature is not yet enabled on mainnet, the finding is currently latent rather than immediately exploitable.

### Citations

**File:** runtime-transaction/src/transaction_meta.rs (L77-83)
```rust
    pub fn compute_unit_price_in_microlamports(&self) -> u64 {
        (self.priority_fee_lamports as u128)
            .saturating_mul(1_000_000u128)
            .checked_div(self.compute_unit_limit as u128)
            .and_then(|x| u64::try_from(x).ok())
            .unwrap_or(0)
    }
```

**File:** runtime-transaction/src/transaction_meta.rs (L122-129)
```rust
    fn from_v1_config(config: &TransactionConfig) -> Self {
        Self::V1(TransactionConfiguration {
            priority_fee_lamports: config.priority_fee.unwrap_or(0),
            compute_unit_limit: config.compute_unit_limit.unwrap_or(0),
            loaded_accounts_data_size_limit: config.loaded_accounts_data_size_limit.unwrap_or(0),
            updated_heap_bytes: config.heap_size.unwrap_or(HEAP_LENGTH as u32),
        })
    }
```

**File:** runtime-transaction/src/transaction_meta.rs (L144-155)
```rust
            Self::LegacyAndV0(compute_budget_instruction_details) => {
                let compute_budget_limits = compute_budget_instruction_details
                    .sanitize_and_convert_to_compute_budget_limits(feature_set)?;
                Ok(TransactionConfiguration {
                    updated_heap_bytes: compute_budget_limits.updated_heap_bytes,
                    compute_unit_limit: compute_budget_limits.compute_unit_limit,
                    priority_fee_lamports: compute_budget_limits.get_prioritization_fee(),
                    loaded_accounts_data_size_limit: compute_budget_limits
                        .loaded_accounts_bytes
                        .get(),
                })
            }
```

**File:** runtime-transaction/src/transaction_meta.rs (L156-176)
```rust
            Self::V1(transaction_configuration) => {
                if !(MIN_HEAP_FRAME_BYTES..=MAX_HEAP_FRAME_BYTES)
                    .contains(&transaction_configuration.updated_heap_bytes)
                    || !transaction_configuration
                        .updated_heap_bytes
                        .is_multiple_of(1024)
                {
                    return Err(TransactionError::SanitizeFailure);
                }

                Ok(TransactionConfiguration {
                    updated_heap_bytes: transaction_configuration.updated_heap_bytes,
                    compute_unit_limit: transaction_configuration
                        .compute_unit_limit
                        .min(MAX_COMPUTE_UNIT_LIMIT),
                    priority_fee_lamports: transaction_configuration.priority_fee_lamports,
                    loaded_accounts_data_size_limit: transaction_configuration
                        .loaded_accounts_data_size_limit
                        .min(MAX_LOADED_ACCOUNTS_DATA_SIZE_BYTES.get()),
                })
            }
```

**File:** runtime-transaction/src/transaction_meta.rs (L251-270)
```rust
    #[test]
    fn test_try_into_config_v1_clamps_compute_unit_limit() {
        let feature_set = FeatureSet::all_enabled();

        let input = TransactionConfiguration {
            updated_heap_bytes: 65_536,
            compute_unit_limit: MAX_COMPUTE_UNIT_LIMIT.saturating_add(1),
            priority_fee_lamports: 42,
            loaded_accounts_data_size_limit: 1,
        };

        let config = VersionedTransactionConfiguration::V1(input)
            .try_into_config(&feature_set)
            .unwrap();

        assert_eq!(config.compute_unit_limit, MAX_COMPUTE_UNIT_LIMIT);
        assert_eq!(config.updated_heap_bytes, 65_536);
        assert_eq!(config.priority_fee_lamports, 42);
        assert_eq!(config.loaded_accounts_data_size_limit, 1);
    }
```

**File:** runtime/src/prioritization_fee_cache.rs (L249-266)
```rust
                let (prioritization_fee, calculate_prioritization_fee_us) =
                    measure_us!(transaction_configuration.priority_fee_lamports);
                self.metrics
                    .accumulate_total_calculate_prioritization_fee_elapsed_us(
                        calculate_prioritization_fee_us,
                    );

                // See rounding note on `compute_unit_price_in_microlamports`.
                let compute_unit_price =
                    transaction_configuration.compute_unit_price_in_microlamports();
                self.sender
                    .send(CacheServiceUpdate::TransactionUpdate {
                        slot: bank.slot(),
                        bank_id: bank.bank_id(),
                        compute_unit_price,
                        prioritization_fee,
                        writable_accounts,
                    })
```

**File:** runtime/src/prioritization_fee_cache.rs (L432-451)
```rust
    pub fn get_prioritization_fees(&self, account_keys: &[Pubkey]) -> Vec<(Slot, u64)> {
        self.cache
            .read()
            .unwrap()
            .iter()
            .map(|(slot, slot_prioritization_fee)| {
                let mut fee = slot_prioritization_fee
                    .get_min_compute_unit_price()
                    .unwrap_or_default();
                for account_key in account_keys {
                    if let Some(account_fee) =
                        slot_prioritization_fee.get_writable_account_fee(account_key)
                    {
                        fee = std::cmp::max(fee, account_fee);
                    }
                }
                (*slot, fee)
            })
            .collect()
    }
```

**File:** rpc/src/rpc.rs (L2440-2457)
```rust
    fn get_recent_prioritization_fees(
        &self,
        pubkeys: Vec<Pubkey>,
    ) -> Result<Vec<RpcPrioritizationFee>> {
        let Some(prioritization_fee_cache) = self.prioritization_fee_cache.as_deref() else {
            error!("The PrioritizationFeeCache should always be available for the full RPC API");
            return Err(Error::internal_error());
        };

        Ok(prioritization_fee_cache
            .get_prioritization_fees(&pubkeys)
            .into_iter()
            .map(|(slot, prioritization_fee)| RpcPrioritizationFee {
                slot,
                prioritization_fee,
            })
            .collect())
    }
```

**File:** runtime/src/bank/check_transactions.rs (L134-146)
```rust
        let enable_tx_v1 = self.feature_set.snapshot().enable_tx_v1;
        // Discard v1 transactions until feature gate is activated.
        sanitized_txs
            .iter()
            .zip(lock_results)
            .map(move |(tx, lock_result)| match lock_result {
                Err(err) => Err(err.clone()),
                Ok(())
                    if !enable_tx_v1 && tx.borrow().version() == TransactionVersion::Number(1) =>
                {
                    Err(TransactionError::UnsupportedVersion)
                }
                Ok(()) => Ok(()),
```
