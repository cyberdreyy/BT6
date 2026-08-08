Based on my research, I found a genuine analog to the reported bug class in the compute-budget parameter validation logic, where two parallel code paths compute the same transaction configuration but apply different validation rules to `loaded_accounts_data_size_limit`.

### Title
V1 transaction message path bypasses the `NonZeroU32` validation that legacy/V0 messages enforce on `loaded_accounts_data_size_limit` - ([File: runtime-transaction/src/transaction_meta.rs])

### Summary
For legacy and V0 (compute-budget-instruction based) transaction messages, `ComputeBudgetInstructionDetails::sanitize_and_convert_to_compute_budget_limits` explicitly rejects a requested `loaded_accounts_data_size_limit` of `0` by requiring `NonZeroU32::new(...)`, returning `TransactionError::InvalidLoadedAccountsDataSizeLimit` otherwise [1](#0-0) . However, the newer "V1" message config path (`TransactionConfig`) does not apply this same check when converting to `TransactionConfiguration`, only validating and clamping `updated_heap_bytes` and `compute_unit_limit` while leaving `loaded_accounts_data_size_limit` unchecked for zero [2](#0-1) .

### Finding Description
`VersionedTransactionConfiguration::try_into_config` handles two enum variants representing the two ways a transaction can express its compute budget:
- `Self::LegacyAndV0`: derived from legacy `ComputeBudgetInstruction`s, going through `sanitize_and_convert_to_compute_budget_limits`, which enforces `loaded_accounts_bytes` is `NonZeroU32` [3](#0-2) .
- `Self::V1`: derived directly from a `TransactionConfig` struct embedded in newer message formats, which only validates `updated_heap_bytes` bounds/alignment and clamps `compute_unit_limit`, but sets `loaded_accounts_data_size_limit` via `.min(MAX_LOADED_ACCOUNTS_DATA_SIZE_BYTES.get())` with no zero-check [4](#0-3) .

This means a V1-format transaction can set `config.loaded_accounts_data_size_limit = Some(0)` (via `TransactionConfig`) and the resulting `TransactionConfiguration.loaded_accounts_data_size_limit` will be `0`, whereas the equivalent legacy compute-budget instruction is explicitly rejected as invalid. This is precisely the bug-class from the report: a parameter that is validated on one path (`LegacyAndV0`) is not validated on an alternate path (`V1`) that produces the same downstream data structure.

The `0` value then flows into `Bank::get_compute_budget_and_limits`/cost-model paths, e.g. `CostModel::calculate_loaded_accounts_data_size_cost` and `LoadedTransactionDataSize::with_max_size`, which use it as a comparison bound [5](#0-4) . A limit of `0` means any account load immediately exceeds `requested_loaded_accounts_data_size_limit`, causing `TransactionError::MaxLoadedAccountsDataSizeExceeded` for every account touched — this is a functional/logic bug (unintended fee-only processing / inconsistent transaction rejection behavior), not a memory-safety issue, since all downstream consumers use `saturating_*` arithmetic and comparisons rather than raw division by the limit.

### Impact Explanation
The observable effect is inconsistent validation: a V1-format transaction can carry an unvalidated `loaded_accounts_data_size_limit` of `0`, which other code paths for legacy transactions explicitly reject as `InvalidLoadedAccountsDataSizeLimit`. This produces divergent behavior between message formats for logically identical configuration, and could result in transactions being processed as "fee-only" (failing the loaded-account-data-size check) when they should have been rejected earlier with a clear sanitize error — a "wrong data returned/processed" class of issue for a single JSON-RPC-submitted transaction, without requiring privileged access. I did not find any location where this value is used as an unguarded divisor, so I cannot confirm a crash/panic; the primary confirmed impact is inconsistent/incorrect validation behavior for V1 messages.

### Likelihood Explanation
Likelihood is limited by the fact that the "V1" message/`TransactionConfig` format did not appear widely referenced or clearly reachable via the standard JSON-RPC `sendTransaction`/`simulateTransaction` decoding paths in the areas I was able to search; I could not confirm within the available index that arbitrary unprivileged users can currently construct and submit a V1-format message through the public RPC surface to exercise this exact code path end-to-end. This is a real code-level inconsistency between the two validation paths, but I was unable to fully verify its live reachability from an unprivileged single JSON-RPC call within the scope of this investigation.

### Recommendation
Apply the same `NonZeroU32` validation (rejecting `loaded_accounts_data_size_limit == 0`) in the `Self::V1` branch of `VersionedTransactionConfiguration::try_into_config` as is already enforced in `ComputeBudgetInstructionDetails::sanitize_and_convert_to_compute_budget_limits`, returning `TransactionError::InvalidLoadedAccountsDataSizeLimit` for consistency across both message-format code paths [2](#0-1) .

### Proof of Concept
Not fully verified due to inability to confirm live RPC reachability of V1-format messages within the scope of my search. Conceptually: construct a `TransactionConfig` with `loaded_accounts_data_size_limit = Some(0)` inside a V1 `SanitizedMessage`/`SanitizedVersionedMessage`, submit it, and observe that `VersionedTransactionConfiguration::try_into_config` returns `loaded_accounts_data_size_limit: 0` without error [6](#0-5) , unlike the equivalent legacy `ComputeBudgetInstruction::set_loaded_accounts_data_size_limit(0)` which is rejected [7](#0-6) .

### Citations

**File:** compute-budget-instruction/src/compute_budget_instruction_details.rs (L136-146)
```rust
        let loaded_accounts_bytes =
            if let Some((_index, requested_loaded_accounts_data_size_limit)) =
                self.requested_loaded_accounts_data_size_limit
            {
                NonZeroU32::new(requested_loaded_accounts_data_size_limit)
                    .ok_or(TransactionError::InvalidLoadedAccountsDataSizeLimit)?
            } else {
                MAX_LOADED_ACCOUNTS_DATA_SIZE_BYTES
            }
            .min(MAX_LOADED_ACCOUNTS_DATA_SIZE_BYTES);

```

**File:** compute-budget-instruction/src/compute_budget_instruction_details.rs (L481-492)
```rust
        // invalid: loaded_account_data_size can't be zero
        let instruction_details = ComputeBudgetInstructionDetails {
            requested_compute_unit_limit: Some((1, 0)),
            requested_compute_unit_price: Some((2, 0)),
            requested_heap_size: Some((3, 40 * 1024)),
            requested_loaded_accounts_data_size_limit: Some((4, 0)),
            ..ComputeBudgetInstructionDetails::default()
        };
        assert_eq!(
            instruction_details.sanitize_and_convert_to_compute_budget_limits(&feature_set),
            Err(TransactionError::InvalidLoadedAccountsDataSizeLimit)
        );
```

**File:** runtime-transaction/src/transaction_meta.rs (L139-177)
```rust
    pub(crate) fn try_into_config(
        &self,
        feature_set: &FeatureSet,
    ) -> Result<TransactionConfiguration, TransactionError> {
        match self {
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
        }
```

**File:** svm/src/account_loader.rs (L474-511)
```rust
#[derive(PartialEq, Eq, Debug, Clone)]
struct LoadedTransactionDataSize {
    loaded_accounts_data_size: u32,
    requested_loaded_accounts_data_size_limit: u32,
}

impl LoadedTransactionDataSize {
    fn with_max_size(requested_loaded_accounts_data_size_limit: u32) -> Self {
        Self {
            loaded_accounts_data_size: 0,
            requested_loaded_accounts_data_size_limit,
        }
    }

    fn increase_calculated_data_size(
        &mut self,
        data_size_delta: usize,
        error_metrics: &mut TransactionErrorMetrics,
    ) -> Result<()> {
        // this branch is unreachable in practice (though not by construction),
        // since it would imply an account >4gb in size
        let Ok(data_size_delta) = u32::try_from(data_size_delta) else {
            self.loaded_accounts_data_size = u32::MAX;
            error_metrics.max_loaded_accounts_data_size_exceeded += 1;
            return Err(TransactionError::MaxLoadedAccountsDataSizeExceeded);
        };

        self.loaded_accounts_data_size = self
            .loaded_accounts_data_size
            .saturating_add(data_size_delta);

        if self.loaded_accounts_data_size > self.requested_loaded_accounts_data_size_limit {
            error_metrics.max_loaded_accounts_data_size_exceeded += 1;
            Err(TransactionError::MaxLoadedAccountsDataSizeExceeded)
        } else {
            Ok(())
        }
    }
```
