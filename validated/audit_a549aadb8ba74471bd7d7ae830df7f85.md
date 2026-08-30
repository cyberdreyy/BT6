This is confirmed as not a vulnerability. The evidence establishes:

1. `Transaction::gas_keys_required()` at `core/primitives/src/transaction.rs:204-209` is used **only** for a protocol-version gate in `ValidatedTransaction::check_valid_for_config` (`core/primitives/src/transaction.rs:307-311`) — it rejects `TransactionV1` outright when the `GasKeys` protocol feature is disabled. It plays no role in selecting which charging function is called.

2. At every actual charge-routing call site — `chain/chain/src/runtime/mod.rs:779` (`can_verify_and_charge_tx`), `runtime/runtime/src/lib.rs:2111` (`process_transactions`), and `runtime/runtime/src/verifier.rs:929` (`validate_verify_and_charge_transaction`) — the branch is selected exclusively on `tx.nonce().nonce_index()` being `Some`, not on `gas_keys_required()`.

3. `TransactionV1` with `TransactionNonce::Nonce(_)` produces `nonce_index() == None` (`core/primitives/src/transaction.rs:86-91`), so in all three call sites this always routes to `verify_and_charge_tx_ephemeral`, which charges the main account balance — exactly the behavior the signer intended, regardless of `NonceMode::Strict`.

4. `verify_and_charge_gas_key_tx_ephemeral` itself defensively panics if invoked with `nonce_index() == None` (`runtime/runtime/src/verifier.rs:382-384`), and `verify_and_charge_tx_ephemeral` explicitly rejects being invoked on a gas key access key (`InvalidNonceIndex`), confirmed by `test_access_key_tx_rejects_nonce_index` (`runtime/runtime/src/verifier.rs:2323-2358`) and `test_gas_key_tx_missing_nonce_index` (`runtime/runtime/src/verifier.rs:2117-2146`).

So the premise — that `gas_keys_required()` returning `true` for all V1 transactions could cause misrouting between gas-key and main-balance charging — does not hold, because charging routing never consults `gas_keys_required()` at all; it consults `nonce_index()`, which correctly reflects whether the transaction actually references a gas key nonce slot. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6) [8](#0-7) 

#No vulnerability found for this question.

### Citations

**File:** core/primitives/src/transaction.rs (L86-91)
```rust
    pub fn nonce_index(&self) -> Option<NonceIndex> {
        match self {
            TransactionNonce::Nonce { .. } => None,
            TransactionNonce::GasKeyNonce { nonce_index, .. } => Some(*nonce_index),
        }
    }
```

**File:** core/primitives/src/transaction.rs (L203-209)
```rust
    /// Check if this transaction version requires the GasKeys protocol feature to be enabled.
    pub fn gas_keys_required(&self) -> bool {
        match self {
            Transaction::V0(_) => false,
            Transaction::V1(_) => true,
        }
    }
```

**File:** core/primitives/src/transaction.rs (L302-316)
```rust
    pub fn check_valid_for_config(
        config: &RuntimeConfig,
        signed_tx: &SignedTransaction,
        protocol_version: ProtocolVersion,
    ) -> Result<(), InvalidTxError> {
        if !ProtocolFeature::GasKeys.enabled(protocol_version)
            && signed_tx.transaction.gas_keys_required()
        {
            return Err(InvalidTxError::InvalidTransactionVersion);
        }
        if signed_tx.transaction.nonce_mode() == NonceMode::Strict
            && !ProtocolFeature::StrictNonce.enabled(protocol_version)
        {
            return Err(InvalidTxError::InvalidTransactionVersion);
        }
```

**File:** chain/chain/src/runtime/mod.rs (L779-819)
```rust
        if let Some(nonce_index) = tx.nonce().nonce_index() {
            let current_nonce =
                get_gas_key_nonce(&trie, tx.signer_id(), tx.public_key(), nonce_index)?
                    .ok_or_else(|| {
                        let num_nonces = access_key
                            .gas_key_info()
                            .map_or(0, |gas_key_info| gas_key_info.num_nonces);
                        InvalidTxError::InvalidNonceIndex {
                            tx_nonce_index: Some(nonce_index),
                            num_nonces,
                        }
                    })?;
            match verify_and_charge_gas_key_tx_ephemeral(
                runtime_config,
                &signer,
                &access_key,
                current_nonce,
                &tx,
                &cost,
                block_height,
                pending_constraints,
            ) {
                TxVerdict::Success(_) => Ok(()),
                TxVerdict::DepositFailed { error, .. } | TxVerdict::Failed(error) => Err(error),
            }
        } else {
            match verify_and_charge_tx_ephemeral(
                runtime_config,
                &signer,
                &access_key,
                &tx,
                &cost,
                block_height,
                pending_constraints,
            ) {
                TxVerdict::Success(_) => Ok(()),
                TxVerdict::Failed(error) => Err(error),
                // verify_and_charge_tx_ephemeral never returns DepositFailed.
                TxVerdict::DepositFailed { .. } => unreachable!(),
            }
        }
```

**File:** runtime/runtime/src/lib.rs (L2110-2155)
```rust
            // Verify and charge based on transaction type (gas key vs regular access key)
            let verdict = if let Some(nonce_index) = tx.transaction.nonce().nonce_index() {
                // Gas key transaction - load nonce from prefetched cache
                let nonce_entry = gas_key_nonces.get(&(signer_id, pubkey, nonce_index));
                let current_nonce = match nonce_entry.as_deref() {
                    Some(Ok(Some(n))) => *n,
                    Some(Ok(None)) => {
                        metrics::TRANSACTION_PROCESSED_FAILED_TOTAL.inc();
                        tracing::debug!(%tx_hash, "gas key nonce not found");
                        let num_nonces =
                            access_key.gas_key_info().map(|info| info.num_nonces).unwrap_or(0);
                        let outcome = ExecutionOutcomeWithId::failed(
                            tx,
                            InvalidTxError::InvalidNonceIndex {
                                tx_nonce_index: Some(nonce_index),
                                num_nonces,
                            },
                        );
                        processing_state.outcomes.push(outcome);
                        continue;
                    }
                    Some(Err(e)) => return Err(e.clone().into()),
                    None => unreachable!("gas key nonces should've been prefetched"),
                };
                verify_and_charge_gas_key_tx_ephemeral(
                    &processing_state.apply_state.config,
                    account,
                    access_key,
                    current_nonce,
                    &tx.transaction,
                    &cost,
                    Some(block_height),
                    &PendingConstraints::default(),
                )
            } else {
                // Regular access key transaction
                verify_and_charge_tx_ephemeral(
                    &processing_state.apply_state.config,
                    account,
                    access_key,
                    &tx.transaction,
                    &cost,
                    Some(block_height),
                    &PendingConstraints::default(),
                )
            };
```

**File:** runtime/runtime/src/verifier.rs (L380-384)
```rust
    // It's the caller's responsibility to ONLY call this function for transactions with
    // nonce_index (i.e. gas key transactions).
    let Some(nonce_index) = tx.nonce().nonce_index() else {
        panic!("verify_and_charge_gas_key_tx_ephemeral called for non-gas key transaction")
    };
```

**File:** runtime/runtime/src/verifier.rs (L928-953)
```rust
        // Check if this is a gas key transaction
        let verdict = if let Some(nonce_index) = tx.nonce().nonce_index() {
            let current_nonce =
                get_gas_key_nonce(state_update, tx.signer_id(), tx.public_key(), nonce_index)?
                    .unwrap_or(0);
            verify_and_charge_gas_key_tx_ephemeral(
                config,
                &signer,
                &access_key,
                current_nonce,
                tx,
                &transaction_cost,
                block_height,
                &PendingConstraints::default(),
            )
        } else {
            verify_and_charge_tx_ephemeral(
                config,
                &signer,
                &access_key,
                tx,
                &transaction_cost,
                block_height,
                &PendingConstraints::default(),
            )
        };
```

**File:** runtime/runtime/src/verifier.rs (L2323-2358)
```rust
    #[test]
    fn test_access_key_tx_rejects_nonce_index() {
        // Set up a regular access key, then try to use nonce_index with it
        let config = RuntimeConfig::test();
        let (signer, mut state_update, gas_price) =
            setup_common(TESTING_INIT_BALANCE, Balance::ZERO, Some(AccessKey::full_access()));

        let signed_tx = SignedTransaction::from_actions_v1(
            TransactionNonce::from_nonce_and_index(1, 0), // Has nonce_index
            alice_account(),
            bob_account(),
            &*signer,
            vec![Action::Transfer(TransferAction { deposit: Balance::from_yoctonear(100) })],
            CryptoHash::default(),
        );

        let err = validate_verify_and_charge_transaction(
            &config,
            &mut state_update,
            signed_tx,
            gas_price,
            None,
            ProtocolFeature::GasKeys.protocol_version(),
        )
        .expect_err("should fail when using nonce_index with regular access key");

        // The error comes from verify_and_charge_gas_key_tx_ephemeral because nonce_index is present,
        // but the access key is not a gas key
        assert_eq!(
            err,
            InvalidTxError::InvalidAccessKeyError(InvalidAccessKeyError::AccessKeyNotFound {
                account_id: alice_account(),
                public_key: Box::new(signer.public_key()),
            })
        );
    }
```
