### Title
Deterministic account "state init" silently no-ops if attacker pre-deploys a local contract at the derived id, permanently squatting the account with attacker-controlled wasm - (File: runtime/runtime/src/deterministic_account_id.rs)

### Summary
`action_deterministic_state_init` only calls `deploy_deterministic_account` (which in turn calls `use_global_contract`) when `account.contract().is_none()` [1](#0-0) . If an attacker manages to get a `Local` (non-`None`) contract installed at the deterministic account id before the legitimate owner's `DeterministicStateInitAction` arrives, that guard is false and the deploy step is skipped entirely, so the account permanently keeps the attacker's wasm instead of the intended `Global`/`GlobalByAccount` contract, while the transaction proceeds to "succeed" and simply refund the deposit.

### Finding Description
`action_deterministic_state_init` treats "has any contract set" (`account.contract().is_none()`) as the sole signal for whether the `uninit -> active` deterministic-account state transition should run [1](#0-0) . The intended precondition, documented directly above `deploy_deterministic_account`, is stronger: "the account must not have any contract data stored when this is called" [2](#0-1) . The code only checks the weaker condition (`is_none()`), not that the account is actually pristine/uninitialized in the NEP-616 sense.

If the account already exists with `AccountContract::Local(hash)` (e.g. because an attacker previously squatted the id and ran `DeployContract`), `account.contract().is_none()` is `false`, so `deploy_deterministic_account`/`use_global_contract` is never invoked for the legitimate `DeterministicStateInitAction`. Execution falls straight through to the storage-staking/deposit-refund logic at lines 57-91 [3](#0-2) , which only checks `check_storage_stake` against the current (attacker-controlled) storage usage and refunds the deposit — it never errors and never signals that the intended contract was not installed. From the legitimate deployer's perspective, the transaction appears to succeed, but the account contract silently remains the attacker's `Local` wasm rather than becoming the intended `Global`/`GlobalByAccount` identifier that `use_global_contract` would have set via `account.set_contract(...)` [4](#0-3) .

This is exactly the asymmetry called out by `use_global_contract`'s own logic: it explicitly special-cases removing an existing `Local` contract's code (`if account.contract().is_local() { state_update.remove(TrieKey::ContractCode{...}) }`) when it *is* invoked [5](#0-4)  — confirming the codebase is aware that a resident `Local` contract needs explicit cleanup — yet the caller (`action_deterministic_state_init`) never reaches that cleanup path in this scenario because the `is_none()` gate short-circuits before `use_global_contract` is ever called.

Whether the attacker can actually get a `Local` contract installed at the derived deterministic id via a `CreateAccount` action before the legitimate `DeterministicStateInitAction` is submitted is a precondition asserted by the prompt ("CreateAccount bypass... actor_id==account_id per the CreateAccount squat") that I was not able to independently re-verify in this session — I did not locate the `validate_deterministic_state_init` / id-derivation validation code that is supposed to prevent ordinary `CreateAccount` actions from targeting a deterministic-account id, nor confirm whether such validation exists and is bypassable. This is a load-bearing precondition for the exploit chain and needs explicit confirmation via `runtime/runtime/src/action_validation.rs` and `core/primitives-core/src/deterministic_account_id.rs` derivation logic before this can be treated as end-to-end reachable from an unprivileged attacker.

### Impact Explanation
If the precondition holds (attacker can legitimately squat the deterministic id and deploy a `Local` contract before the rightful `DeterministicStateInitAction`), the impact is a permanent authorization-escalation / fund-freezing bug: the deriving owner's expected `Global`/`GlobalByAccount` contract never gets installed, and there is no way to overwrite the attacker's `Local` code through the normal `DeterministicStateInitAction` path since it always short-circuits on `is_none() == false`. This matches "Unauthorized transaction" / permanent state corruption of an account the legitimate deriver believes they control, potentially permanently freezing any funds/deposit sent to that account under this bug going forward.

### Likelihood Explanation
Likelihood hinges entirely on the unverified precondition — whether an ordinary unprivileged account can actually target a deterministic-account id with `CreateAccount` + `DeployContract` before the legitimate `DeterministicStateInitAction` executes. This session could not confirm that `validate_deterministic_state_init` / the derived-id validation is bypassable that way; without independent verification of that gate, I cannot assert the full attack chain is reachable purely from the code reviewed here.

### Recommendation
In `action_deterministic_state_init`, do not gate the `deploy_deterministic_account` call on `account.contract().is_none()` alone. Instead, verify the account is a genuinely fresh/uninitialized deterministic account (e.g., also check storage usage / that no contract-data keys exist, or track an explicit "not yet initialized" marker), and if a non-empty contract/state already exists at the derived id, fail the `DeterministicStateInitAction` with an explicit error rather than silently refunding and no-oping.

### Proof of Concept
Cannot be finalized without first confirming the `CreateAccount`-squat precondition against `action_validation.rs`/id-derivation code, which was not completed in this session due to tool-call exhaustion. If that precondition is confirmed reachable, the PoC would be a `runtime/apply` test: (1) submit `CreateAccount` + `DeployContract` from an attacker account to the target deterministic id (squat), (2) submit the legitimate `DeterministicStateInitAction` with a `Global`/`GlobalByAccount` `state_init.code()`, (3) assert `account.contract()` is still `AccountContract::Local(attacker_hash)` (not `Global`/`GlobalByAccount`), and (4) assert the deposit was fully refunded with no error, diverging from the truly-uninitialized case where `account.contract()` becomes the intended global identifier.

### Citations

**File:** runtime/runtime/src/deterministic_account_id.rs (L38-52)
```rust
    if account.contract().is_none() {
        // `uninit` -> `active` account state transition. "uninit" here is the
        // NEP-616 sense, a deterministic account with no contract yet, not
        // `Account::Uninitialized`: a `0u` id can never reach this, because
        // `validate_deterministic_state_init` pins the receiver to the derived
        // `0s` id.
        deploy_deterministic_account(
            state_update,
            account,
            account_id,
            &action.state_init,
            result,
            storage_usage_config,
        )?;
    }
```

**File:** runtime/runtime/src/deterministic_account_id.rs (L57-93)
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

    Ok(())
```

**File:** runtime/runtime/src/deterministic_account_id.rs (L114-120)
```rust
/// Take the content of a `StateInit` and deploy it on the account.
///
/// Pre-condition: The account must not have any contract data stored when this
/// is called. Otherwise, the storage usage calculations would fail to take
/// overwritten existing values into account.
/// (It would be possible to read value refs first and subtract their length but
/// that is unnecessary work since the pre-condition above holds at the moment.)
```

**File:** runtime/runtime/src/global_contracts.rs (L90-93)
```rust
    clear_account_contract_storage_usage(state_update, account_id, account)?;
    if account.contract().is_local() {
        state_update.remove(TrieKey::ContractCode { account_id: account_id.clone() });
    }
```

**File:** runtime/runtime/src/global_contracts.rs (L94-107)
```rust
    let contract = match contract_identifier {
        GlobalContractIdentifier::CodeHash(code_hash) => AccountContract::Global(*code_hash),
        GlobalContractIdentifier::AccountId(id) => AccountContract::GlobalByAccount(id.clone()),
    };
    account.set_storage_usage(
        account.storage_usage().checked_add(contract_identifier.len() as u64).ok_or_else(|| {
            StorageError::StorageInconsistentState(format!(
                "Storage usage integer overflow for account {}",
                account_id
            ))
        })?,
    );
    account.set_contract(contract).or_inconsistent_state(account_id)?;
    Ok(())
```
