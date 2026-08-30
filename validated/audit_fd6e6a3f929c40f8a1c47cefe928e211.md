No vulnerability found for this question.

**Analysis:** The code in `runtime/runtime/src/global_contracts.rs` shows that `use_global_contract` performs `state_update.contains_key(&key, AccessOptions::DEFAULT)` against `TrieKey::GlobalContractCode { identifier }` on the **local shard's own trie** before mutating the account's contract pointer.#No vulnerability found for this question.

**Rationale:**

The guard check in `use_global_contract` ( [1](#0-0) ) and the runtime code lookup in `contract_code.rs` ( [2](#0-1) ) both query the exact same `TrieKey::GlobalContractCode { identifier }` against the same per-shard `TrieUpdate`. Since both reads occur against the local shard's own committed trie state (never cross-shard), there is no window in which the guard could observe the code as present while a later lookup finds it absent, or vice versa.

If the opt-in transaction (`UseGlobalContractAction`) lands on shard S2 before the distribution receipt from S1 has been applied there, `state_update.contains_key(&key, ...)` on S2 deterministically returns `false`, causing the action to fail immediately with `ActionErrorKind::GlobalContractDoesNotExist` ( [3](#0-2) ) — no account mutation, no storage fee is charged, and no state is committed. This exact scenario is already covered by an existing test, `test_use_non_existent_global_contract`, which asserts the transaction fails with `GlobalContractDoesNotExist` rather than succeeding [4](#0-3) .

Because the account's contract pointer (`AccountContract::Global`/`GlobalByAccount`) is only set after the `contains_key` check succeeds within the same atomic `apply_action` call on the same trie snapshot ( [5](#0-4) ), and global contract code once distributed to a shard is never removed, there is no reachable path where an account can bind to code that is present at opt-in but later resolves to `StorageInconsistentState` at call time on that same shard. The guard and the call-time lookup are provably in agreement for a given shard's trie state.

### Citations

**File:** runtime/runtime/src/global_contracts.rs (L82-89)
```rust
    let key = TrieKey::GlobalContractCode { identifier: contract_identifier.clone().into() };
    if !state_update.contains_key(&key, AccessOptions::DEFAULT)? {
        result.result = Err(ActionErrorKind::GlobalContractDoesNotExist {
            identifier: contract_identifier.clone(),
        }
        .into());
        return Ok(());
    }
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

**File:** runtime/runtime/src/contract_code.rs (L91-116)
```rust
impl GlobalContractAccessExt for GlobalContractIdentifier {
    fn hash(self, store: &TrieUpdate, access: AccessOptions) -> Result<CryptoHash, StorageError> {
        if let GlobalContractIdentifier::CodeHash(hash) = self {
            return Ok(hash);
        }
        let key = TrieKey::GlobalContractCode { identifier: self.into() };
        let value_ref =
            store.get_ref(&key, KeyLookupMode::MemOrFlatOrTrie, access)?.ok_or_else(|| {
                let TrieKey::GlobalContractCode { identifier } = key else { unreachable!() };
                StorageError::StorageInconsistentState(format!(
                    "Global contract identifier not found {:?}",
                    identifier
                ))
            })?;
        Ok(value_ref.value_hash())
    }

    fn code(self, store: &TrieUpdate) -> Result<Option<ContractCode>, StorageError> {
        let key = TrieKey::GlobalContractCode { identifier: self.clone().into() };
        let code_hash = match self {
            GlobalContractIdentifier::AccountId(_) => None,
            GlobalContractIdentifier::CodeHash(hash) => Some(hash),
        };
        let code = store.get(&key, AccessOptions::DEFAULT)?;
        Ok(code.map(|code| ContractCode::new(code, code_hash)))
    }
```

**File:** test-loop-tests/src/tests/global_contracts.rs (L55-68)
```rust
#[test]
fn test_use_non_existent_global_contract() {
    let mut env = GlobalContractsTestEnv::setup(Balance::from_near(1));

    let identifier = env.global_contract_identifier(&GlobalContractDeployMode::CodeHash);
    let tx = env.use_global_contract_tx(&env.account_shard_0.clone(), identifier);
    let outcome = env.execute_tx(tx);
    assert_matches!(
        outcome.status,
        FinalExecutionStatus::Failure(TxExecutionError::ActionError(ActionError {
            kind: ActionErrorKind::GlobalContractDoesNotExist { .. },
            index: _
        }))
    );
```
