The evidence gathered fully answers the question and confirms there is no vulnerability.

`action_use_global_contract` (in `runtime/runtime/src/global_contracts.rs:64-108`) only ever mutates the account passed into it — the receipt's own receiver account — by calling `clear_account_contract_storage_usage` on that same account and then adding `contract_identifier.len()` to `account.storage_usage()`: [1](#0-0) 

The function signature takes a single `account: &mut Account` (the receiver of the `UseGlobalContract` action) and never touches the deploying account's state at all — there's no lookup or reference to the original deployer's account record anywhere in this path. `AccountContract::identifier_storage_usage()` confirms that for `Global`/`GlobalByAccount` variants storage usage is only ever the identifier size (32 bytes for a code hash, or `id.len()` for an account-based reference), not the underlying code length: [2](#0-1) 

This is corroborated by `get_contract_storage_usage`, used elsewhere in accounting, which for `Global`/`GlobalByAccount` contracts returns only `identifier_storage_usage()`, never the actual code length: [3](#0-2) 

The deploying account's debit happens once, at deploy time, in `action_deploy_global_contract`, which charges `storage_cost = global_contract_storage_amount_per_byte * code.len()` against the deployer's own `amount` and burns it into `result.tokens_burnt` — this is a one-time balance debit (not a storage_usage debit) on the deployer, and it is never reversed, refunded, or re-attributed later: [4](#0-3) 

The existing integration test `test_deploy_and_call_global_contract` already exercises exactly the scenario described in the question — one account deploys, other unrelated accounts (`account_shard_0`, `account_shard_1`) call `UseGlobalContract` — and asserts that each referencing account's `storage_usage` increases by exactly `identifier.len()`, nothing more: [5](#0-4) 

There is no code path in `use_global_contract`, `action_use_global_contract`, or the surrounding receipt-application logic (`GlobalContractDistributionReceipt` handling) that looks up or mutates any account other than the one passed in as `account`/`account_id`. The global contract code itself is stored under a shard-wide `TrieKey::GlobalContractCode` keyed by identifier — not under any specific account's trie subtree — so there is no shared byte cost that could be double-counted or misattributed to a third party. The deployer pays once in `amount` (burnt), and each user of the global contract pays only for its own small identifier in `storage_usage`; the two charges are on different accounts, different resources (balance vs. storage_usage), and never overlap or double-count against an unrelated third account.

### No vulnerability found for this question.

### Citations

**File:** runtime/runtime/src/global_contracts.rs (L24-61)
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
```

**File:** runtime/runtime/src/global_contracts.rs (L90-106)
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
```

**File:** core/primitives-core/src/account.rs (L182-188)
```rust
    pub fn identifier_storage_usage(&self) -> u64 {
        match self {
            AccountContract::None | AccountContract::Local(_) => 0u64,
            AccountContract::Global(_) => 32u64,
            AccountContract::GlobalByAccount(id) => id.len() as u64,
        }
    }
```

**File:** runtime/runtime/src/actions.rs (L410-424)
```rust
fn get_contract_storage_usage(
    state_update: &TrieUpdate,
    account_id: &AccountId,
    account: &Account,
) -> Result<StorageUsage, StorageError> {
    Ok(match account.contract().as_ref() {
        AccountContract::None => 0,
        AccountContract::Local(code_hash) => {
            get_code_len_or_default(state_update, account_id.clone(), *code_hash)?
        }
        AccountContract::Global(_) | AccountContract::GlobalByAccount(_) => {
            account.contract().identifier_storage_usage()
        }
    })
}
```

**File:** test-loop-tests/src/tests/global_contracts.rs (L136-147)
```rust
    for account in [env.account_shard_0.clone(), env.account_shard_1.clone()] {
        let identifier = env.global_contract_identifier(&deploy_mode);
        let baseline_storage_usage = env.get_account_state(account.clone()).storage_usage;

        env.use_global_contract(&account, identifier.clone());
        let account_state = env.get_account_state(account.clone());
        let use_cost = INITIAL_BALANCE.checked_sub(account_state.amount).unwrap();
        assert_eq!(use_cost, env.use_global_contract_cost(&identifier));
        assert_eq!(
            account_state.storage_usage,
            baseline_storage_usage + identifier.len() as StorageUsage
        );
```
