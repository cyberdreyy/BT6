No vulnerability found for this question.

The behavior described in `use_global_contract` is intentional design, not a bug. When a contract deploys a global contract via `action_deploy_global_contract`, it pays a one-time storage cost proportional to the actual code size using `global_contract_storage_amount_per_byte`, and the code bytes are written once to a dedicated global-contract trie key. [1](#0-0) 

When another account subsequently opts in via `UseGlobalContractAction`, it does not duplicate the code into its own account state — it only stores a reference (`AccountContract::Global(hash)` or `AccountContract::GlobalByAccount(id)`), so the fixed identifier-length charge (32 bytes for a hash, or the account-id length) accurately reflects the bytes actually added to *that* account's own trie footprint. [2](#0-1) 

This is explicitly documented as intended behavior: "Only `Local` code counts toward this account's own contract storage; `Global` costs a fixed 32-byte identifier and `GlobalByAccount` costs the publisher id's length." [3](#0-2) 

Since the actual multi-megabyte code lives only once in global storage (paid for by the original deployer), there is no proportional trie-byte cost attributable to each individual using account — `storage_usage` for the using account correctly bounds its own contributed trie bytes (the small reference), not the shared global code. There is no mechanism by which this discrepancy could be leveraged against a third party: no factory pattern or other contract can be misled into assuming a caller's `storage_usage` reflects the size of code that account references, because global contract code is a shared, separately-paid-for resource, not per-account state. `check_storage_stake` correctly enforces backing only for `account.storage_usage()`, which is consistent with what that account actually stores. [4](#0-3) 

No path leads to theft or freezing of funds, consensus divergence, or a shard halt — this is a resource-accounting design choice with no reachable exploit against a third party.

### Citations

**File:** runtime/runtime/src/global_contracts.rs (L34-50)
```rust
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
```

**File:** runtime/runtime/src/global_contracts.rs (L90-107)
```rust
    clear_account_contract_storage_usage(state_update, account_id, account)?;
    if account.contract().is_local() {
        state_update.remove(TrieKey::ContractCode { account_id: account_id.clone() });
    }
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

**File:** protocol-model/spec/accounts-keys.md (L35-35)
```markdown
A receiver account's `AccountContract` tells the runtime where to fetch code for a `FunctionCall`. `Local(hash)` reads code stored under this account; `Global(hash)` and `GlobalByAccount(id)` reference a single shared deployment so many accounts can run the same code without each paying to store it (global contracts; the deploy/use transitions and `GlobalContractIdentifier`, `core/primitives-core/src/global_contract.rs:25`, are in [runtime-execution](runtime-execution.md)). ETH-implicit accounts are created already pointing at a `Global` wallet contract (`actions.rs:249`). Only `Local` code counts toward this account's own contract storage; `Global` costs a fixed 32-byte identifier and `GlobalByAccount` costs the publisher id's length (`account.rs:126`).
```

**File:** runtime/runtime/src/verifier.rs (L47-82)
```rust
pub fn check_storage_stake(
    account: &Account,
    account_balance: Balance,
    runtime_config: &RuntimeConfig,
) -> Result<(), StorageStakingError> {
    let billable_storage_bytes = account.storage_usage();
    let required_amount = runtime_config
        .storage_amount_per_byte()
        .checked_mul(u128::from(billable_storage_bytes))
        .ok_or_else(|| {
            format!(
                "Account's billable storage usage {} overflows multiplication",
                billable_storage_bytes
            )
        })
        .map_err(StorageStakingError::StorageError)?;
    let available_amount = account_balance
        .checked_add(account.locked())
        .ok_or_else(|| {
            format!(
                "Account's amount {} and locked {} overflow addition",
                account.amount(),
                account.locked(),
            )
        })
        .map_err(StorageStakingError::StorageError)?;
    if available_amount >= required_amount {
        Ok(())
    } else {
        if is_zero_balance_account(account) {
            return Ok(());
        }
        Err(StorageStakingError::LackBalanceForStorageStaking(
            required_amount.checked_sub(available_amount).unwrap(),
        ))
    }
```
