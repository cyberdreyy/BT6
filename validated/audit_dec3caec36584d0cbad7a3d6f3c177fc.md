### Title
Global contract storage-cost is permanently burnt from total supply without any account debit or distribution receipt when a sibling action in the same receipt fails - ([File: runtime/runtime/src/global_contracts.rs])

### Summary
`action_deploy_global_contract` debits `storage_cost` from an in-memory `Account` copy and unconditionally increments `result.tokens_burnt`, but the balance debit is only persisted to `state_update` if the *entire* receipt succeeds. If a later sibling action in the same action-receipt fails, the receipt is rolled back atomically (state writes, including the nonce write in `increment_nonce`, and the queued `GlobalContractDistribution` receipt), yet `result.tokens_burnt` is still unconditionally folded into `tx_burnt_amount`/`stats.balance.tx_burnt_amount`, permanently and artificially decrementing the chain's recorded total supply with no matching account debit.

### Finding Description
In `action_deploy_global_contract` (`runtime/runtime/src/global_contracts.rs:24-62`), `storage_cost` is subtracted from `account.amount()` and `result.tokens_burnt` is incremented (`runtime/runtime/src/global_contracts.rs:40-50`) *before* `initiate_distribution` writes an incremented nonce to `state_update` (`increment_nonce`, `global_contracts.rs:172-188`) and pushes a `GlobalContractDistribution` receipt only into `result.new_receipts` (`global_contracts.rs:162-169`).

In `apply_action_receipt` (`runtime/runtime/src/lib.rs:892-951`), actions in the receipt's action list run in a loop; on the first action error the loop breaks (`lib.rs:947-950`). Crucially:
- The mutated `account` (holding the debited balance from a successful `DeployGlobalContract`) is only persisted via `set_account` when `result.result.is_ok()` (`lib.rs:955-960`); on any later failure this call is skipped entirely, so the balance debit never reaches `state_update`.
- On overall failure, `state_update.rollback()` discards all pending trie writes from the receipt (`lib.rs:1024-1034`), including the nonce increment written by `increment_nonce`, and the queued distribution receipt in `result.new_receipts` is never dispatched since the whole action-receipt is atomic (fails-or-succeeds-as-a-whole for state and generated receipts).
- However, `tx_burnt_amount` is computed unconditionally from `result.tokens_burnt` (`lib.rs:1039-1051`) with **no gating on `result.result`**, and is unconditionally added to `stats.balance.tx_burnt_amount` (`lib.rs:1087-1088`), which ultimately reduces `new_total_supply` for the block (per `core/primitives/src/block.rs:193`, documented in `protocol-model/spec/economics.md:41`).

An unprivileged attacker reaches this by submitting a transaction/receipt whose action list is `[DeployGlobalContract(small code), DeployGlobalContract(code sized so the combined/second storage_cost exceeds the account's remaining balance)]`. The first action succeeds (debits balance in memory, bumps `result.tokens_burnt`, writes nonce, queues distribution receipt). The second action's `checked_sub` fails, setting `result.result = Err(LackBalanceForState)` (`global_contracts.rs:40-47`) without touching `tokens_burnt`. The loop breaks with `result.result` = `Err`, causing: no `set_account` (balance debit lost), `state_update.rollback()` (nonce write lost, no contract published), and no dispatch of the distribution receipt — yet the first action's `storage_cost` remains inside `result.tokens_burnt` and is burnt from network supply.

No existing check (signature, nonce, access-key, storage-staking, or size-limit) prevents this, because the rollback logic only covers trie/state writes and generated receipts, not the already-accumulated `tokens_burnt` scalar.

### Impact Explanation
Every exploitation permanently decrements the network's canonical `total_supply` (computed deterministically and identically by all honest nodes, so this does not cause a chain split, but it does corrupt the supply accounting relative to the sum of real account balances) by `storage_cost` (bytes deployed × `global_contract_storage_amount_per_byte`, e.g. 0.0001 N/byte per `core/parameters/res/runtime_configs/parameters.snap:282`), with zero actual debit from any account and no distribution ever occurring. This is a token-supply-loss bug (falls under "token inflation or loss"): the reported/consensus total supply is falsely and irreversibly reduced without any real transfer of value, and it is fully repeatable and scales with attacker-chosen contract code size and number of repetitions.

### Likelihood Explanation
Trivially reachable by any funded account: it requires only crafting a normal transaction with two `DeployGlobalContract` actions where the second is sized to exceed the account's remaining balance after the first debit. Cost to the attacker is just the gas/fees of the transaction (unaffected by this bug, since gas accounting is prepaid separately from storage cost). It is deterministic and repeatable indefinitely, and requires no privileged access, no validator role, and no additional preconditions beyond a funded account.

### Recommendation
Only fold `result.tokens_burnt` into `tx_burnt_amount`/`stats.balance.tx_burnt_amount` when `result.result.is_ok()`; on failure, reset/zero the storage-cost portion of `tokens_burnt` (or gate the whole `result.tokens_burnt` contribution on the receipt succeeding), so burnt-token accounting stays consistent with the rolled-back state and undelivered distribution receipt. Alternatively, move the storage-cost debit/burn to occur only after all actions in the receipt have succeeded (i.e., alongside the final `set_account`/commit path), mirroring how the balance mutation itself is already gated.

### Proof of Concept
Unit test in `runtime/runtime/src/global_contracts.rs` or an integration test in `test-loop-tests/src/tests/global_contracts.rs`:
1. Fund an account with a balance just enough to cover one small `DeployGlobalContract` deploy plus base fees but not a second, larger one.
2. Submit a single transaction/receipt with actions `[DeployGlobalContract(code_a, small), DeployGlobalContract(code_b, sized to exceed remaining balance)]`.
3. Run the receipt through `apply_action_receipt`.
4. Assert:
   - The receipt's execution outcome status is `Failure(LackBalanceForState)`.
   - `outcome.tokens_burnt > 0` (equal to `code_a`'s storage cost + relevant exec fees) — demonstrating tokens were recorded as burnt.
   - The account's on-chain balance after the receipt is applied equals its pre-receipt balance minus only prepaid gas/fees (i.e., NOT reduced by `code_a`'s storage cost) — demonstrating no matching debit occurred.
   - The `GlobalContractNonce` trie key for `code_a`'s identifier is absent/unchanged (rolled back).
   - No `GlobalContractDistribution` receipt was forwarded/buffered by the `ReceiptSink`.
   - Compare `stats.balance.tx_burnt_amount` (or the resulting block's `balance_burnt`) against the actual change in total account balances to show the divergence. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

**File:** runtime/runtime/src/global_contracts.rs (L24-62)
```rust
pub(crate) fn action_deploy_global_contract(
    state_update: &mut TrieUpdate,
    account: &mut Account,
    account_id: &AccountId,
    apply_state: &ApplyState,
    deploy_contract: &DeployGlobalContractAction,
    result: &mut ActionResult,
) -> Result<(), RuntimeError> {
    let _span = tracing::debug_span!(target: "runtime", "action_deploy_global_contract").entered();

    let storage_cost = apply_state
        .config
        .fees
        .storage_usage_config
        .global_contract_storage_amount_per_byte
        .saturating_mul(deploy_contract.code.len() as u128);
    let Some(updated_balance) = account.amount().checked_sub(storage_cost) else {
        result.result = Err(ActionErrorKind::LackBalanceForState {
            account_id: account_id.clone(),
            amount: storage_cost,
        }
        .into());
        return Ok(());
    };
    result.tokens_burnt =
        result.tokens_burnt.checked_add(storage_cost).ok_or(IntegerOverflowError)?;
    account.set_amount(updated_balance);

    initiate_distribution(
        state_update,
        account_id.clone(),
        deploy_contract.code.clone(),
        &deploy_contract.deploy_mode,
        apply_state.shard_id,
        result,
    )?;

    Ok(())
}
```

**File:** runtime/runtime/src/global_contracts.rs (L142-188)
```rust
fn initiate_distribution(
    state_update: &mut TrieUpdate,
    account_id: AccountId,
    contract_code: Arc<[u8]>,
    deploy_mode: &GlobalContractDeployMode,
    current_shard_id: ShardId,
    result: &mut ActionResult,
) -> Result<(), RuntimeError> {
    let id = match deploy_mode {
        GlobalContractDeployMode::CodeHash => {
            GlobalContractIdentifier::CodeHash(hash(&contract_code))
        }
        GlobalContractDeployMode::AccountId => {
            GlobalContractIdentifier::AccountId(account_id.clone())
        }
    };
    // Increment the nonce and write it to state immediately to prevent multiple
    // distributions with the same nonce from being initiated. This requires
    // allowing the same nonce in the freshness check when applying the
    // distribution receipt.
    let nonce = increment_nonce(state_update, &id)?;
    let distribution_receipt =
        GlobalContractDistributionReceipt::new(id, current_shard_id, vec![], contract_code, nonce);
    let distribution_receipts =
        Receipt::new_global_contract_distribution(account_id, distribution_receipt);
    // No need to set receipt_id here, it will be generated as part of apply_action_receipt
    result.new_receipts.push(distribution_receipts);
    Ok(())
}

/// Increments the nonce for the given global contract identifier and writes
/// it to state immediately.
fn increment_nonce(
    state_update: &mut TrieUpdate,
    id: &GlobalContractIdentifier,
) -> Result<u64, RuntimeError> {
    let identifier: GlobalContractCodeIdentifier = id.clone().into();

    let nonce_key = TrieKey::GlobalContractNonce { identifier };
    let stored_nonce = get_nonce(state_update, &nonce_key)?;

    let new_nonce = stored_nonce.checked_add(1).ok_or_else(|| {
        RuntimeError::UnexpectedIntegerOverflow("increment_global_contract_nonce".into())
    })?;
    set_nonce(state_update, nonce_key, new_nonce);
    Ok(new_nonce)
}
```

**File:** runtime/runtime/src/lib.rs (L892-951)
```rust
            for (action_index, action) in action_receipt.actions().iter().enumerate() {
                let action_hash = create_action_hash_from_receipt_id(
                    receipt.receipt_id(),
                    apply_state.block_height,
                    action_index,
                );
                let mut new_result = self.apply_action(
                    action,
                    state_update,
                    apply_state,
                    preparation_pipeline,
                    &mut account,
                    &mut actor_id,
                    receipt,
                    &action_receipt,
                    Arc::clone(&promise_results),
                    &action_hash,
                    action_index,
                    &action_receipt.actions(),
                    epoch_info_provider,
                    storage_proof_size_before_receipt,
                )?;
                if new_result.result.is_ok() {
                    if let Err(e) = new_result.new_receipts.iter().try_for_each(|receipt| {
                        validate_receipt(
                            &apply_state.config.wasm_config.limit_config,
                            receipt,
                            apply_state.current_protocol_version,
                            ValidateReceiptMode::NewReceipt,
                        )
                    }) {
                        new_result.result =
                            Err(ActionErrorKind::NewReceiptValidationError(e).into());
                    }
                }
                result.merge(new_result)?;
                if let (true, Some(size_before), Some(limit)) = (
                    result.result.is_ok(),
                    storage_proof_size_before_receipt,
                    storage_proof_limit_for_all_actions,
                ) {
                    let recorded_by_receipt = state_update
                        .trie
                        .recorded_storage_size_upper_bound()
                        .saturating_sub(size_before);
                    if recorded_by_receipt > limit {
                        result.set_error(
                            ActionErrorKind::ReceiptStorageProofSizeExceeded {
                                limit: limit as u64,
                            }
                            .into(),
                        );
                    }
                }
                // TODO storage error
                if let Err(ref mut res) = result.result {
                    res.index = Some(action_index as u64);
                    break;
                }
            }
```

**File:** runtime/runtime/src/lib.rs (L954-977)
```rust
        // Going to check balance covers account's storage.
        if result.result.is_ok() {
            if let Some(ref account) = account {
                match check_storage_stake(account, account.amount(), &apply_state.config) {
                    Ok(()) => {
                        set_account(state_update, account_id.clone(), account);
                    }
                    Err(StorageStakingError::LackBalanceForStorageStaking(amount)) => {
                        result.set_error(ActionError {
                            index: None,
                            kind: ActionErrorKind::LackBalanceForState {
                                account_id: account_id.clone(),
                                amount,
                            },
                        });
                    }
                    Err(StorageStakingError::StorageError(err)) => {
                        return Err(RuntimeError::StorageError(
                            StorageError::StorageInconsistentState(err),
                        ));
                    }
                }
            }
        }
```

**File:** runtime/runtime/src/lib.rs (L1024-1051)
```rust
        // Committing or rolling back state.
        match &result.result {
            Ok(_) => {
                state_update.commit(StateChangeCause::ReceiptProcessing {
                    receipt_hash: receipt.get_hash(),
                });
            }
            Err(_) => {
                state_update.rollback();
            }
        };
        // If the receipt is a refund, then we consider it free without burnt gas.
        let gas_burnt: Gas =
            if receipt.predecessor_id().is_system() { Gas::ZERO } else { result.gas_burnt };
        // `price_deficit` is strictly less than `gas_burn_price * gas_burnt`.
        let mut tx_burnt_amount = safe_gas_to_balance(gas_burn_price, gas_burnt)?
            .checked_sub(gas_refund_result.price_deficit)
            .unwrap();
        if !ProtocolFeature::AccountCostIncrease.enabled(apply_state.current_protocol_version) {
            tx_burnt_amount = safe_add_balance(tx_burnt_amount, gas_refund_result.price_surplus)?;
        }
        tx_burnt_amount = safe_add_balance(tx_burnt_amount, gas_refund_result.refund_penalty)?;
        tx_burnt_amount =
            safe_add_balance(tx_burnt_amount, gas_refund_result.create_account_charge)?;
        tx_burnt_amount = safe_add_balance(tx_burnt_amount, result.tokens_burnt)?;
        // The amount of tokens burnt for the execution of this receipt. It's used in the execution
        // outcome.
        let tokens_burnt = tx_burnt_amount;
```

**File:** runtime/runtime/src/lib.rs (L1087-1088)
```rust
        stats.balance.tx_burnt_amount =
            safe_add_balance(stats.balance.tx_burnt_amount, tx_burnt_amount)?;
```
