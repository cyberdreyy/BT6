### Title
Deterministic-account deposit-refund receipt is not invalidated when a later action in the same `ActionReceipt` causes the receipt to fail final storage-stake re-check, enabling a double-refund of the attached deposit - ([File: runtime/runtime/src/deterministic_account_id.rs])

### Summary
`action_deterministic_state_init` computes and emits a `Receipt::new_balance_refund` for the *unused portion* of `action.deposit` based on `account.storage_usage()` measured immediately after `deploy_deterministic_account` runs, before any later action in the same `ActionReceipt` (e.g. `AddKey`) can further raise `storage_usage()`. That refund receipt is unconditionally accumulated into `result.new_receipts` via `result.merge(...)` in the per-action loop and is not purged if a later action, or the receipt-final `check_storage_stake` re-check (`runtime/runtime/src/lib.rs:955-977`), fails the whole receipt. On failure the account-side state (balance increase, storage usage, contract/data writes) is discarded by `state_update.rollback()` (`lib.rs:1025-1034`), but the previously emitted deposit-refund receipt is not discarded, and the generic failure path additionally refunds the receipt's full attached deposit via `refund_unspent_gas_and_deposits`. The two refunds together exceed the amount actually deposited, an apparent value-conservation break.

### Finding Description
`action_deterministic_state_init` (`runtime/runtime/src/deterministic_account_id.rs:15-94`) does:
1. Create the account with zero balance if it doesn't exist, then `deploy_deterministic_account` sets code + data and `account.set_storage_usage(...)` for exactly the state-init payload [1](#0-0) .
2. Immediately call `check_storage_stake(account, account.amount(), &apply_state.config)` and, based on the *current* `storage_usage`, either mark the whole `action.deposit` for refund, or move `missing_amount` into `account.amount()` and refund the remainder (`action.deposit - missing_amount`) [2](#0-1) .
3. Push `Receipt::new_balance_refund(...)` onto `result.new_receipts` right there, inside the single action's `ActionResult` [3](#0-2) .

This per-action `ActionResult` (including the refund receipt just generated) is merged into the receipt-level `result` via `result.merge(new_result)?` inside the action-execution loop (`runtime/runtime/src/lib.rs:892-951`) regardless of what happens to *subsequent* actions in the receipt [4](#0-3) .

After all actions run, the runtime performs one more authoritative check using the account's *final* storage usage and balance:
```
if result.result.is_ok() {
    if let Some(ref account) = account {
        match check_storage_stake(account, account.amount(), &apply_state.config) { ... }
``` [5](#0-4) 

If a subsequent action in the same `ActionReceipt` (e.g. `AddKey`) increases `storage_usage` such that this final check fails, `result.result` becomes `Err(LackBalanceForState)`. On error, `state_update.rollback()` discards every trie write from the receipt, including the deterministic account's balance top-up and storage writes [6](#0-5) . However, the deposit-refund receipt that `action_deterministic_state_init` already placed into `result.new_receipts` was computed and merged before this rollback, and nothing observed in the loop or in the post-loop bookkeeping (`refund_unspent_gas_and_deposits`, the output-data-receiver handling at `lib.rs:1092-1130` which manipulates `result.new_receipts` even along the `Err` branch) purges action-level receipts when the overall receipt later fails.

Separately, per the documented failure-refund semantics ("Refunds — `refund_unspent_gas_and_deposits`... refunds unspent gas and, on failure, the full deposit"), a failed `ActionReceipt` also generates a refund of the receipt's *full* attached deposit back to the sender. Combined with the already-emitted partial-deposit refund receipt from step 3 above (which is not rolled back), the total refunded value can exceed the amount the attacker ever actually deposited — the account never durably received or kept any of `action.deposit` (rolled back), yet up to two refund receipts referencing portions of that deposit are dispatched.

### Impact Explanation
This is a token-conservation violation: an attacker can cause more NEAR to be refunded than was ever attached/spent, i.e., token inflation. Category: token inflation / loss of value conservation, reachable purely through an unprivileged transaction containing `[DeterministicStateInitAction, AddKeyAction]` in one `ActionReceipt`, with no validator, node-operator, or privileged access required.

### Likelihood Explanation
- Preconditions: attacker needs only to sign an ordinary transaction containing a `DeterministicStateInitAction` (with `data`/`code` sized so the initial `storage_usage` sits at or below `action.deposit`'s covered amount) followed by one or more `AddKey` (or other storage-increasing) actions targeting the same receiver in the same `ActionReceipt`, and to size the attached deposit / subsequent storage growth so that the account passes `check_storage_stake` right after `deploy_deterministic_account` but fails it after the later action.
- Cost: only standard gas/deposit for one transaction; fully repeatable, deterministic account ids can be regenerated by varying the state-init `data` payload.
- No signature/nonce/access-key/gas-metering check blocks this path since all of these gate the *initial* deposit and are already satisfied when the transaction is accepted; the flaw is purely in mid-receipt refund bookkeeping.

### Recommendation
Do not emit the deposit-refund receipt from inside `action_deterministic_state_init` before the receipt is known to fully succeed. Instead, defer/stage the refund amount in `ActionResult` and only materialize the `Receipt::new_balance_refund` after the receipt-level final `check_storage_stake` (`lib.rs:955-977`) has passed, or make the post-loop refund/rollback logic explicitly drop any refund receipts generated by actions when the overall `result.result` ends up `Err`. Additionally, recompute `check_storage_stake` for the deterministic-account refund decision using the account's storage usage as it will stand after all actions in the receipt, not the intermediate value right after `deploy_deterministic_account`.

### Proof of Concept
Runtime/test-loop integration test plan (analogous to `test-loop-tests/src/tests/deterministic_account_id.rs`):
1. Deploy a global contract; compute a `DeterministicAccountStateInit` whose `data` yields `storage_usage` just below the ZBA/refund threshold such that `deposit` exactly covers it with the *initial* deploy-only usage.
2. Build one transaction/`ActionReceipt` with actions `[DeterministicStateInitAction{state_init, deposit}, AddKeyAction{...}]` targeting the derived deterministic account id, where the `AddKey` action's storage cost is enough to push final `storage_usage` above what the *remaining* balance (after the intermediate deposit_refund) can cover.
3. Run the receipt through `apply_action_receipt` and assert:
   - The receipt fails with `ActionErrorKind::LackBalanceForState` (post-loop check at `lib.rs:955-977`).
   - Despite failure, a `new_balance_refund` receipt for `action.deposit - missing_amount` (generated inside `action_deterministic_state_init`) is present in `result.new_receipts`.
   - A second refund for the full original `action.deposit` is also produced via the failure path (`refund_unspent_gas_and_deposits`).
   - Sum of tokens paid out to the refund receiver across both receipts exceeds the originally attached `action.deposit`, while the deterministic account itself was never created/funded (state rolled back) — violating `storage_amount_per_byte * storage_usage <= amount + locked` / total-supply conservation for the receipt.

### Citations

**File:** runtime/runtime/src/deterministic_account_id.rs (L57-91)
```rust
    // Use attached deposit to satisfy storage staking requirements and refund
    // the rest.
    let deposit_refund = match check_storage_stake(account, account.amount(), &apply_state.config) {
        Ok(_) => {
            // no additional storage needed, refunding all
            action.deposit
        }
        Err(StorageStakingError::LackBalanceForStorageStaking(missing_amount)) => {
            if missing_amount <= action.deposit {
                // use exactly as much as needed and refund the rest
                let new_balance = safe_add_balance(account.amount(), missing_amount)?;
                account.set_amount(new_balance);
                action
                    .deposit
                    .checked_sub(missing_amount)
                    .expect("just checked missing_amount <= action.deposit")
            } else {
                result.result = Err(ActionErrorKind::LackBalanceForState {
                    account_id: account_id.clone(),
                    amount: missing_amount,
                }
                .into());
                return Ok(());
            }
        }
        Err(StorageStakingError::StorageError(err)) => {
            return Err(RuntimeError::StorageError(StorageError::StorageInconsistentState(err)));
        }
    };

    if deposit_refund > Balance::ZERO {
        result
            .new_receipts
            .push(Receipt::new_balance_refund(receipt.balance_refund_receiver(), deposit_refund));
    }
```

**File:** runtime/runtime/src/deterministic_account_id.rs (L121-154)
```rust
fn deploy_deterministic_account(
    state_update: &mut TrieUpdate,
    account: &mut Account,
    account_id: &AccountId,
    state_init: &DeterministicAccountStateInit,
    result: &mut ActionResult,
    storage_usage_config: &StorageUsageConfig,
) -> Result<(), RuntimeError> {
    // Step 1: set contract code (includes storage usage accounting)
    use_global_contract(state_update, account_id, account, state_init.code(), result)?;
    if result.result.is_err() {
        return Ok(());
    }

    // Step 2: insert provided key-value pairs
    let mut required_storage_usage = account.storage_usage();
    for (key, value) in state_init.data() {
        let trie_key = TrieKey::ContractData { account_id: account_id.clone(), key: key.to_vec() };

        let value_bytes = value.len() as u64;
        let key_bytes = key.len() as u64;
        let extra_per_record_bytes = storage_usage_config.num_extra_bytes_record;

        let new_bytes = value_bytes
            .checked_add(key_bytes)
            .and_then(|acc| acc.checked_add(extra_per_record_bytes))
            .ok_or(IntegerOverflowError {})?;
        state_update.set(trie_key, value.clone());
        required_storage_usage =
            required_storage_usage.checked_add(new_bytes).ok_or(IntegerOverflowError {})?;
    }
    account.set_storage_usage(required_storage_usage);

    Ok(())
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

**File:** runtime/runtime/src/lib.rs (L1024-1034)
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
```
