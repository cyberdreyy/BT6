### Title
Durable-nonce transactions lack any chain/cluster domain separator, enabling cross-cluster replay of signed fund-moving transactions - (File: accounts-db/src/blockhash_queue.rs, svm/src/transaction_processor.rs, programs/system/src/system_instruction.rs)

### Summary
Solana/Agave's durable-nonce mechanism, which exists precisely to allow a fully-signed transaction to be broadcast by *anyone* at *any later time* (the on-chain analog of the C4 report's "anyone may call this function for anyone else, funds go to destination regardless" `withdraw()` pattern), derives its replay-protection value solely from a rolling blockhash, with no chain identifier, genesis hash, or other domain separator baked into the signed message or the nonce value itself.

### Finding Description
A durable-nonce transaction is validated by comparing `message.recent_blockhash()` against the value stored in a `NonceState::Initialized` account, produced by `DurableNonce::from_blockhash(&blockhash)` [1](#0-0)  and refreshed every slot purely from the bank's last blockhash, with no cluster-identifying input [2](#0-1) . Validation in the runtime (`check_nonce_transaction_validity` / `load_message_nonce_data`) and in the SVM (`validate_transaction_nonce`) only checks that the stored durable nonce equals the transaction's `recent_blockhash` and that the nonce authority signed - it never checks any genesis hash or chain-id field, because the transaction message format has no such field [3](#0-2) [4](#0-3) . Since the fully-signed bytes are self-contained and submitter-agnostic by design (exactly like the `withdraw()` calls in the report, "msg.sender is not used" and "anyone may call this function for anyone else"), any party holding the signed transaction can relay it to any node that will accept it, as the `send-transaction-service` does purely by signature/expiry bookkeeping without any chain-scoping check [5](#0-4) .

### Impact Explanation
If a second cluster is bootstrapped that shares the mainnet ledger state at some point (e.g., a fork of the network, or - more realistically and commonly practiced - a staging/testnet cluster instantiated from a mainnet snapshot before diverging), any nonce account that was initialized with an identical durable-nonce value at the shared point in history will accept the exact same previously-signed durable-nonce transaction bytes. This lets an observer or the original signer's counterparty replay a transaction the signer only intended for one specific chain onto the other chain, moving funds or executing privileged nonce-authority operations (e.g., `AdvanceNonceAccount` + `Transfer` combos used for offline/custodial signing) without a fresh authorization, exactly mirroring the report's "funds sent to the unsupported chain" scenario. This is a fund-movement/authorization-replay issue reachable by any unprivileged party who has observed or received the signed transaction bytes, no special privilege on the destination cluster required.

### Likelihood Explanation
Exploitation requires two clusters to briefly share identical ledger/nonce state at a fork or snapshot-clone point in time and a durable-nonce transaction signed before divergence to still be circulating (e.g., held by an offline/cold signer, multisig workflow, or exchange withdrawal queue) - the same durable-nonce offline-signing feature explicitly tested in `cli/tests/nonce.rs` where a signed transaction is submitted independently of the original signer [6](#0-5) . This is a realistic, if not everyday, occurrence for teams that regularly snapshot mainnet-beta state to bring up devnets/testnets or private forks for testing, and for any workflow using long-lived offline-signed durable-nonce transactions.

### Recommendation
Bind the signed transaction and/or the durable nonce value to a chain-specific domain separator (e.g., mix the cluster's `genesis_hash`/chain-id into `DurableNonce::from_blockhash`'s hashing input, or require it as an explicit field checked during `verify_nonce_account`/`validate_transaction_nonce`), so that a durable-nonce transaction (and its embedded nonce value) signed for one cluster can never validate against a different cluster's nonce account state, even if that state was cloned from a shared snapshot.

### Proof of Concept
1. Bootstrap cluster A (e.g., mainnet-beta) and create/initialize a nonce account at slot N; the account's stored durable-nonce value is `DurableNonce::from_blockhash(&blockhash_at_slot_N)` [2](#0-1) .
2. Sign an offline durable-nonce transaction (e.g., `AdvanceNonceAccount` + `Transfer`) using that nonce value, as in the CLI offline-signing flow [6](#0-5) .
3. Fork/clone cluster A's state at slot N to bring up cluster B (e.g., a testnet snapshot-clone), which now contains an identical nonce account with the identical stored durable-nonce value.
4. Submit the same signed transaction bytes to cluster B. `check_nonce_transaction_validity`/`validate_transaction_nonce` on cluster B only compares `recent_blockhash` against the locally stored nonce value and signer set - both match, since neither includes any chain-specific data - so the transaction executes and moves funds on cluster B despite never being intended for it [7](#0-6) .

### Citations

**File:** programs/system/src/system_instruction.rs (L50-52)
```rust
            let next_durable_nonce =
                DurableNonce::from_blockhash(&invoke_context.environment_config.blockhash);
            if data.durable_nonce == next_durable_nonce {
```

**File:** accounts-db/src/blockhash_queue.rs (L85-89)
```rust
    pub fn refresh_durable_nonce(&mut self) {
        self.durable_nonce = self
            .last_hash
            .map(|hash| DurableNonce::from_blockhash(&hash));
    }
```

**File:** runtime/src/bank/check_transactions.rs (L258-300)
```rust
    pub(super) fn check_nonce_transaction_validity(
        &self,
        message: &impl SVMMessage,
        next_durable_nonce: &DurableNonce,
        strict_nonce_size_check: bool,
        strict_nonce_authority_check: bool,
    ) -> Option<(Pubkey, u64)> {
        let nonce_is_advanceable = message.recent_blockhash() != next_durable_nonce.as_hash();
        if !nonce_is_advanceable {
            return None;
        }

        let (nonce_address, nonce_data) =
            self.load_message_nonce_data(message, strict_nonce_size_check)?;

        if strict_nonce_authority_check
            && !message
                .get_ix_signers(NONCED_TX_MARKER_IX_INDEX as usize)
                .any(|signer| signer == &nonce_data.authority)
        {
            return None;
        }

        let previous_lamports_per_signature = nonce_data.get_lamports_per_signature();

        Some((nonce_address, previous_lamports_per_signature))
    }

    pub(super) fn load_message_nonce_data(
        &self,
        message: &impl SVMMessage,
        strict_nonce_size_check: bool,
    ) -> Option<(Pubkey, NonceData)> {
        let nonce_address = message.get_durable_nonce()?;
        let nonce_account = self.get_account_with_fixed_root(nonce_address)?;
        if strict_nonce_size_check && nonce_account.data().len() != NonceState::size() {
            return None;
        }
        let nonce_data =
            nonce_account::verify_nonce_account(&nonce_account, message.recent_blockhash())?;

        Some((*nonce_address, nonce_data))
    }
```

**File:** svm/src/transaction_processor.rs (L833-892)
```rust
    fn validate_transaction_nonce<CB: TransactionProcessingCallback>(
        account_loader: &mut AccountLoader<CB>,
        message: &impl SVMMessage,
        nonce_address: &Pubkey,
        next_durable_nonce: &DurableNonce,
        next_lamports_per_signature: u64,
        strict_nonce_size_check: bool,
        error_counters: &mut TransactionErrorMetrics,
    ) -> TransactionResult<NonceInfo> {
        // When SIMD83 is enabled, if the nonce has been used in this batch already, we must drop
        // the transaction. This is the same as if it was used in different batches in the same slot.
        // It is possible that the nonce account was used, closed, closed and reopened, closed and
        // spoofed by a non-system program, or had its authority changed. Such a transaction cannot
        // be processed, even as fee-only.

        let Some(mut nonce_account) = account_loader
            .load_transaction_account(nonce_address, true)
            .map(|loaded| loaded.account)
        else {
            error_counters.account_not_found += 1;
            return Err(TransactionError::AccountNotFound);
        };

        if strict_nonce_size_check && nonce_account.data().len() != NonceState::size() {
            error_counters.blockhash_not_found += 1;
            return Err(TransactionError::BlockhashNotFound);
        }

        // This function verifies:
        // * Nonce account owner is SystemProgram
        // * Nonce account parses as State::Initialized
        // * Stored durable nonce matches the message blockhash
        let Some(nonce_data) = verify_nonce_account(&nonce_account, message.recent_blockhash())
        else {
            error_counters.blockhash_not_found += 1;
            return Err(TransactionError::BlockhashNotFound);
        };

        // We must still check that the nonce account is usable and that its authority has signed.
        let nonce_can_be_advanced = &nonce_data.durable_nonce != next_durable_nonce;
        let nonce_authority_is_valid = message
            .get_ix_signers(NONCED_TX_MARKER_IX_INDEX as usize)
            .any(|signer| signer == &nonce_data.authority);

        if nonce_can_be_advanced && nonce_authority_is_valid {
            let next_nonce_state = NonceState::new_initialized(
                &nonce_data.authority,
                *next_durable_nonce,
                next_lamports_per_signature,
            );
            nonce_account
                .set_state(&NonceVersions::new(next_nonce_state))
                .expect("Serializing into a validated nonce account cannot fail");

            Ok(NonceInfo::new(*nonce_address, nonce_account))
        } else {
            error_counters.blockhash_not_found += 1;
            Err(TransactionError::BlockhashNotFound)
        }
    }
```

**File:** send-transaction-service/src/send_transaction_service.rs (L420-426)
```rust
                if verify_nonce_account.is_none() && signature_status.is_none() && expired {
                    info!("Dropping expired durable-nonce transaction: {signature}");
                    result.expired += 1;
                    stats.expired_transactions.fetch_add(1, Ordering::Relaxed);
                    return false;
                }
            }
```

**File:** cli/tests/nonce.rs (L322-348)
```rust
    // Verify we cannot contact the cluster
    authority_config.command = CliCommand::ClusterVersion;
    process_command(&authority_config).await.unwrap_err();
    authority_config.command = CliCommand::Transfer {
        amount: SpendAmount::Some(10 * LAMPORTS_PER_SOL),
        to: to_address,
        from: 0,
        sign_only: true,
        dump_transaction_message: true,
        allow_unfunded_recipient: true,
        no_wait: false,
        blockhash_query: BlockhashQuery::Static(nonce_hash),
        nonce_account: Some(nonce_address),
        nonce_authority: 0,
        memo: None,
        fee_payer: 0,
        derived_address_seed: None,
        derived_address_program_id: None,
        compute_unit_price: None,
    };
    authority_config.output_format = OutputFormat::JsonCompact;
    let sign_only_reply = process_command(&authority_config).await.unwrap();
    let sign_only = parse_sign_only_reply_string(&sign_only_reply);
    let authority_presigner = sign_only.presigner_of(&authority_pubkey).unwrap();
    assert_eq!(sign_only.blockhash, nonce_hash);

    // And submit it
```
