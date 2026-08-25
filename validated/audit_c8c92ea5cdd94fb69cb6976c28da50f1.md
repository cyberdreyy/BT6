### Title
Transaction v1's `priority_fee` field is a directly user-specified fee, not one derived from `compute_unit_price`/`compute_unit_limit` — ([File: runtime-transaction/src/transaction_meta.rs])

### Summary
In the v1 transaction message format, `priority_fee` is a raw, user-supplied field on `solana_message::v1::TransactionConfig`, and it is consumed verbatim as `priority_fee_lamports` when building the canonical `TransactionConfiguration` used for fee charging, cost/priority calculation, and prioritization-fee metrics. This mirrors the reported BribeVault pattern: a fee amount that should be derived from a canonical calculation (`compute_unit_price * compute_unit_limit`, as is done for legacy/v0 transactions) is instead accepted as-is from user input, with no validation tying it to `compute_unit_price`/`compute_unit_limit`.

### Finding Description
For legacy and v0 transactions, the prioritization fee is *computed* from the compute-budget instructions via `ComputeBudgetLimits::get_prioritization_fee()`, which multiplies `compute_unit_price` by `compute_unit_limit`: [1](#0-0) 

For v1 transactions, however, `priority_fee_lamports` is taken directly from the wire-format `TransactionConfig.priority_fee` field — an arbitrary `u64` chosen by whoever constructs the message — with only heap-size and limit clamping applied, no relationship enforced to any "price × units" computation: [2](#0-1) [3](#0-2) 

The v1 wire format itself confirms `priority_fee` is a standalone field alongside `compute_unit_limit`, with no `compute_unit_price` field at all: [4](#0-3) 

This `priority_fee_lamports` value flows unchanged into:
- Fee calculation and fee-payer debiting: `solana_fee::calculate_fee_details` uses it directly as the "prioritization fee" component of `FeeDetails`. [5](#0-4) [6](#0-5) 
- Reward/burn accounting and block-priority computation in the scheduler/forwarding path, where the "reward" driving transaction prioritization is derived straight from this user-declared value. [7](#0-6) 
- The prioritization-fee cache used to answer `getRecentPrioritizationFees`, which also consumes `priority_fee_lamports` (and a derived `compute_unit_price`) without cross-checking it against any independently computed value. [8](#0-7) 

For the legacy/v0 path, `compute_unit_price` is an independently meaningful, auditable quantity — it is what RPCs and the fee-market rely on to reason about "lamports per compute unit," and it's algebraically tied to the total fee. In the v1 path, the protocol has no field that independently constrains `priority_fee`: a transaction can declare a high `compute_unit_limit` (which drives real execution-cost/CU allocation and cost-model accounting) while declaring an arbitrary, disconnected `priority_fee` (which drives what the fee payer is actually charged and how the transaction is prioritized/rewarded). This decouples the fee actually paid from any computable "price per unit," which is exactly the BribeVault defect pattern: the amount charged is admin/user-declared instead of calculated from the canonical inputs.

### Impact Explanation
Because `compute_unit_price_in_microlamports()` is only ever *derived* from `priority_fee_lamports / compute_unit_limit` for display/metric purposes (not enforced as an input constraint), and because the reward used to prioritize the transaction (`bank.calculate_reward_and_burn_fee_details`) is fed directly by this same declared value, a user can construct a v1 transaction that:
- Declares an artificially large `priority_fee` disconnected from any real "per-CU price," inflating its computed reward/priority in the leader's transaction-priority queue relative to its actual resource consumption, since `calculate_priority_and_cost` divides reward by cost with no sanity check that reward is consistent with a genuine price-per-unit. [9](#0-8) 
- Or, conversely, present a `getRecentPrioritizationFees`/fee-estimation consumer with distorted signals, since the prioritization-fee cache records this same unconstrained value as if it were a genuine market price.

This is a fairness/economic-integrity concern (unprivileged users can manipulate their own perceived priority-fee reward without a corresponding "price commitment"), not a fund-theft or consensus-divergence bug, since all validators compute the same (unvalidated) value deterministically from the same message bytes — so it does not cause replay-path panics or fork divergence. The severity is analogous to the original Medium-severity BribeVault finding: correctness/fairness of a fee-derived value is undermined, but no direct fund loss or crash results.

### Likelihood Explanation
Any user with the ability to construct a v1-format transaction can trigger this by simply setting an arbitrary `priority_fee` value independent of `compute_unit_limit`; no privileged access, special program, or race condition is required. The V1 message format and its plumbing already exist in this codebase (`solana_message::v1`, `SanitizedMessage::V1`, and the corresponding `try_into_config` path), making this reachable today via ordinary transaction submission.

### Recommendation
Require that v1 transactions either (a) supply `compute_unit_price` alongside `compute_unit_limit` and derive `priority_fee` via the same `get_prioritization_fee()` computation used for legacy/v0 (rejecting any mismatch), or (b) explicitly document/enforce that `priority_fee` is a fee ceiling/commitment validated against a computed minimum, so that the reward used for prioritization cannot be inflated independent of the transaction's actual declared price-per-compute-unit. At minimum, `VersionedTransactionConfiguration::try_into_config`'s `V1` branch should validate internal consistency of `priority_fee` against `compute_unit_limit` rather than trusting the field verbatim, matching the guarantee already provided in the `LegacyAndV0` branch. [10](#0-9) 

### Proof of Concept
1. Construct a `solana_message::v1::Message` with `TransactionConfig { priority_fee: Some(1_000_000), compute_unit_limit: Some(1), .. }` — i.e., a declared reward of 1,000,000 lamports for only 1 CU of requested execution, an implied "price" of 1,000,000 lamports/CU with no instruction-level justification.
2. Submit it through normal transaction ingestion; `VersionedTransactionConfiguration::from_v1_config`/`try_into_config` accept `priority_fee_lamports = 1_000_000` unchanged (only heap-size bounds and `compute_unit_limit`/`loaded_accounts_data_size_limit` clamping are checked). [2](#0-1) [11](#0-10) 
3. `calculate_priority_and_cost` computes `reward` directly from this `fee_details.priority_fee_lamports` and divides by the (tiny, CU-limit-bounded) `cost`, producing an inflated priority score relative to transactions that legitimately set a market compute-unit price via legacy/v0 instructions. [12](#0-11) 

Note: I was unable to fully inspect the `solana_message::v1` crate's own field-level validation (if any) beyond what is shown in `storage-proto` and `transaction_meta.rs`; if that crate independently enforces a `priority_fee = compute_unit_price * compute_unit_limit` invariant at sanitize time, this finding would be moot. I did not find such validation in the code reachable via search, but a full audit of `solana_message::v1::Message`/`TransactionConfig` sanitize routines could not be completed within the available tool calls — a Devin session with full repository access would be needed to conclusively confirm or refute the absence of that check.

### Citations

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

**File:** runtime-transaction/src/transaction_meta.rs (L139-178)
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
    }
```

**File:** storage-proto/proto/confirmed_block.proto (L48-53)
```text
message TransactionConfig {
    optional uint64 priority_fee = 1;
    optional uint32 compute_unit_limit = 2;
    optional uint32 loaded_accounts_data_size_limit = 3;
    optional uint32 heap_size = 4;
}
```

**File:** fee/src/lib.rs (L29-39)
```rust
pub fn calculate_fee_details(
    message: &impl SVMStaticMessage,
    lamports_per_signature: u64,
    prioritization_fee: u64,
    _fee_features: FeeFeatures,
) -> FeeDetails {
    FeeDetails::new(
        calculate_signature_fee(SignatureCounts::from(message), lamports_per_signature),
        prioritization_fee,
    )
}
```

**File:** runtime/src/bank/check_transactions.rs (L172-207)
```rust
                    let compute_budget_and_limits = tx
                        .borrow()
                        .transaction_configuration(feature_set)
                        .map(|config| {
                            let fee_details = calculate_fee_details(
                                tx.borrow(),
                                self.fee_structure.lamports_per_signature,
                                config.priority_fee_lamports,
                                fee_features,
                            );
                            if let Some(compute_budget) = self.compute_budget {
                                // This block of code is only necessary to retain legacy behavior of the code.
                                // It should be removed along with the change to favor transaction's compute budget limits
                                // over configured compute budget in Bank.
                                compute_budget.get_compute_budget_and_limits(
                                    config.loaded_accounts_data_size_limit,
                                    fee_details,
                                )
                            } else {
                                SVMTransactionExecutionAndFeeBudgetLimits {
                                    budget: SVMTransactionExecutionBudget {
                                        compute_unit_limit: u64::from(config.compute_unit_limit),
                                        heap_size: config.updated_heap_bytes,
                                        ..SVMTransactionExecutionBudget::new_with_defaults(
                                            raise_cpi_limit,
                                        )
                                    },
                                    loaded_accounts_data_size_limit: config
                                        .loaded_accounts_data_size_limit,
                                    fee_details,
                                }
                            }
                        })
                        .inspect_err(|_err| {
                            error_counters.invalid_compute_budget += 1;
                        })?;
```

**File:** core/src/transaction_priority.rs (L32-65)
```rust
pub(crate) fn calculate_priority_and_cost<Tx: TransactionMeta + SVMStaticMessage>(
    bank: &Bank,
    transaction: &Tx,
    transaction_configuration: &TransactionConfiguration,
) -> (u64, u64) {
    let cost = CostModel::calculate_cost_for_executed_transaction(
        transaction,
        u64::from(transaction_configuration.compute_unit_limit),
        transaction_configuration.loaded_accounts_data_size_limit,
        &bank.feature_set,
    )
    .sum();
    let fee_details = solana_fee::calculate_fee_details(
        transaction,
        bank.fee_structure().lamports_per_signature,
        transaction_configuration.priority_fee_lamports,
        bank.fee_features(),
    );
    let reward = bank
        .calculate_reward_and_burn_fee_details(&CollectorFeeDetails::from(fee_details))
        .get_deposit();

    // We need a multiplier here to avoid rounding down too aggressively.
    // For many transactions, the cost will be greater than the fees in terms of raw lamports.
    // For the purposes of calculating prioritization, we multiply the fees by a large number so that
    // the cost is a small fraction.
    // An offset of 1 is used in the denominator to explicitly avoid division by zero.
    const MULTIPLIER: u64 = 1_000_000;
    (
        reward
            .saturating_mul(MULTIPLIER)
            .saturating_div(cost.saturating_add(1)),
        cost,
    )
```

**File:** runtime/src/prioritization_fee_cache.rs (L249-258)
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
```
