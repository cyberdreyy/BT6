### Title
Unbounded `priority_fee` in V1 Transaction Message Skips Fee Clamping Applied to Legacy/V0 Paths - (File: runtime-transaction/src/transaction_meta.rs)

### Summary
The `TokenManager.sol` bug class ("no upper bound on fee") maps to agave's transaction-fee configuration path for the new `v1` message format. Unlike legacy/v0 transactions, whose prioritization fee is derived and bounded via `ComputeBudgetInstructionDetails::sanitize_and_convert_to_compute_budget_limits`, a `v1` transaction's `priority_fee` is taken directly from user-supplied message data with no upper bound.

### Finding Description
`VersionedTransactionConfiguration::try_into_config` handles two branches: `LegacyAndV0` and `V1` [1](#0-0) . For the `LegacyAndV0` branch, `priority_fee_lamports` is computed via `compute_budget_limits.get_prioritization_fee()`, which itself derives from `compute_unit_price` and `compute_unit_limit`, both of which are separately clamped (`compute_unit_limit` is `.min(MAX_COMPUTE_UNIT_LIMIT)`) before the fee is derived [2](#0-1) .

For the `V1` branch, however, `compute_unit_limit` and `loaded_accounts_data_size_limit` are explicitly clamped (`.min(MAX_COMPUTE_UNIT_LIMIT)`, `.min(MAX_LOADED_ACCOUNTS_DATA_SIZE_BYTES...)`), but `priority_fee_lamports` is passed through untouched from the raw, attacker-controlled `TransactionConfig.priority_fee: Option<u64>` field [3](#0-2) . This is confirmed by the dedicated regression test `test_try_into_config_v1_no_clamping`, which explicitly documents that `priority_fee_lamports` passes through with no clamping while other fields are clamped [4](#0-3) .

The raw `priority_fee` originates from the wire-format `v1::TransactionConfig`/`SanitizedTransactionView` `transaction_config_view.priority_fee_lamports()` with only `.unwrap_or(0)` applied and no range check, both when building `CachedTransactionMeta` from a `SanitizedTransactionView` [5](#0-4)  and when reconstructing `from_v1_config` [6](#0-5) . This value flows to `FeeDetails::new(signature_fee, prioritization_fee)` for fee accounting and ultimately into `validate_fee_payer`, where the fee is subtracted from the payer's balance [7](#0-6) .

### Impact Explanation
Because `priority_fee_lamports` for v1 transactions is unbounded (up to `u64::MAX`) while sibling fields (`compute_unit_limit`, `loaded_accounts_data_size_limit`) enforce explicit upper bounds, a v1 transaction can declare an arbitrarily large prioritization fee. This is directly analogous to the reported bug class: absence of an upper bound on a fee-affecting parameter that other, similar parameters do bound. Practical consequences depend on downstream handling: if the ordinary user is the fee payer, `validate_fee_payer`'s checked-arithmetic will simply reject the transaction with `InsufficientFundsForFee` for typical balances (bounded impact, no fund loss for that payer), but the fee value still propagates unclamped through reward/burn calculations, RPC-visible fee details, and priority-based transaction ordering/forwarding logic (e.g., `calculate_priority` in `forwarding_stage.rs`), creating an inconsistency between how legacy/v0 and v1 transactions are treated for fee-based prioritization and accounting, and reintroducing exactly the missing-bound risk the external report describes for a differently-typed but structurally analogous parameter.

### Likelihood Explanation
Any ordinary user can construct and submit a v1-format transaction with a fee payer of their choosing and an arbitrary `priority_fee` value set directly in the message bytes — no privileged access is required, since v1 message parsing/validation only enforces bounds on `heap_size`, `compute_unit_limit`, and `loaded_accounts_data_size_limit`, not on `priority_fee`.

### Recommendation
Apply an explicit upper bound (and possibly lower bound of 0, which is already implicit) to `priority_fee_lamports` in the `V1` branch of `VersionedTransactionConfiguration::try_into_config`, consistent with how `compute_unit_limit` and `loaded_accounts_data_size_limit` are clamped, e.g., by capping it against a maximum derived from `MAX_COMPUTE_UNIT_LIMIT * MAX_COMPUTE_UNIT_PRICE` or another well-defined ceiling, and reject (rather than silently pass through) values exceeding that bound with `TransactionError::SanitizeFailure`.

### Proof of Concept
1. Construct a `solana_message::v1::Message` with `TransactionConfig { priority_fee: Some(u64::MAX), compute_unit_limit: Some(200_000), .. }`.
2. Sign and submit via RPC/QUIC as an ordinary transaction.
3. Observe that `VersionedTransactionConfiguration::V1(...).try_into_config()` returns `priority_fee_lamports: u64::MAX` unclamped, as demonstrated by `test_try_into_config_v1_no_clamping` [4](#0-3) , in contrast to `compute_unit_limit`, which is clamped in the sibling test `test_try_into_config_v1_clamps_compute_unit_limit` [8](#0-7) .

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

**File:** runtime-transaction/src/transaction_meta.rs (L230-249)
```rust
    #[test]
    fn test_try_into_config_v1_no_clamping() {
        let feature_set = FeatureSet::all_enabled();

        let input = TransactionConfiguration {
            updated_heap_bytes: 65_536,
            compute_unit_limit: 123_456,
            priority_fee_lamports: 42,
            loaded_accounts_data_size_limit: 789_012,
        };

        let config = VersionedTransactionConfiguration::V1(input)
            .try_into_config(&feature_set)
            .unwrap();

        assert_eq!(config.updated_heap_bytes, 65_536);
        assert_eq!(config.compute_unit_limit, 123_456);
        assert_eq!(config.priority_fee_lamports, 42);
        assert_eq!(config.loaded_accounts_data_size_limit, 789_012);
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

**File:** compute-budget/src/compute_budget_limits.rs (L56-69)
```rust
    pub fn get_prioritization_fee(&self) -> u64 {
        get_prioritization_fee(self.compute_unit_price, u64::from(self.compute_unit_limit))
    }
}

fn get_prioritization_fee(compute_unit_price: u64, compute_unit_limit: u64) -> u64 {
    let micro_lamport_fee: MicroLamports =
        (compute_unit_price as u128).saturating_mul(compute_unit_limit as u128);
    micro_lamport_fee
        .saturating_add(MICRO_LAMPORTS_PER_LAMPORT.saturating_sub(1) as u128)
        .checked_div(MICRO_LAMPORTS_PER_LAMPORT as u128)
        .and_then(|fee| u64::try_from(fee).ok())
        .unwrap_or(u64::MAX)
}
```

**File:** runtime-transaction/src/runtime_transaction/transaction_view.rs (L94-107)
```rust
    let versioned_transaction_config =
        if let Some(transaction_config_view) = transaction.transaction_config() {
            // NOTE: only txv1 has `transaction_config_view`, which must have been validated for
            // SanitizedTransactionView.
            VersionedTransactionConfiguration::V1(TransactionConfiguration {
                priority_fee_lamports: transaction_config_view.priority_fee_lamports().unwrap_or(0),
                compute_unit_limit: transaction_config_view.compute_unit_limit().unwrap_or(0),
                loaded_accounts_data_size_limit: transaction_config_view
                    .loaded_accounts_data_size_limit()
                    .unwrap_or(0),
                updated_heap_bytes: transaction_config_view
                    .requested_heap_size()
                    .unwrap_or(HEAP_LENGTH as u32),
            })
```

**File:** svm/src/account_loader.rs (L373-421)
```rust
pub fn validate_fee_payer(
    payer_account: &mut AccountSharedData,
    payer_index: IndexOfAccount,
    error_metrics: &mut TransactionErrorMetrics,
    rent: &Rent,
    fee: u64,
    relax_post_exec_min_balance_check: bool,
) -> Result<()> {
    if payer_account.lamports() == 0 {
        error_metrics.account_not_found += 1;
        return Err(TransactionError::AccountNotFound);
    }
    let system_account_kind = get_system_account_kind(payer_account).ok_or_else(|| {
        error_metrics.invalid_account_for_fee += 1;
        TransactionError::InvalidAccountForFee
    })?;
    let min_balance = match system_account_kind {
        SystemAccountKind::System => 0,
        SystemAccountKind::Nonce => {
            // Should we ever allow a fees charge to zero a nonce account's
            // balance. The state MUST be set to uninitialized in that case
            rent.minimum_balance(NonceState::size())
        }
    };

    payer_account
        .lamports()
        .checked_sub(min_balance)
        .and_then(|v| v.checked_sub(fee))
        .ok_or_else(|| {
            error_metrics.insufficient_funds += 1;
            TransactionError::InsufficientFundsForFee
        })?;

    let pre_balance = payer_account.lamports();
    payer_account
        .checked_sub_lamports(fee)
        .map_err(|_| TransactionError::InsufficientFundsForFee)?;
    let post_balance = payer_account.lamports();

    check_static_account_rent_state_transition(
        pre_balance,
        post_balance,
        payer_account.data().len(),
        rent,
        payer_index,
        relax_post_exec_min_balance_check,
    )
}
```
