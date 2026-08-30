This confirms the design: `RuntimeContractIdentifier::resolve` at `runtime/runtime/src/contract_code.rs:36-48` resolves `AccountContract::GlobalByAccount(id)` to a `GlobalContractIdentifier::AccountId` and looks up its current code hash live via `GlobalContractAccessExt::hash`/`code`, reading `TrieKey::GlobalContractCode { identifier }` from the trie at call time, not at deployment time. The `pipelining.rs` code even explicitly documents the exact race the question describes and mitigates it in-chunk. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** runtime/runtime/src/global_contracts.rs (L94-97)
```rust
    let contract = match contract_identifier {
        GlobalContractIdentifier::CodeHash(code_hash) => AccountContract::Global(*code_hash),
        GlobalContractIdentifier::AccountId(id) => AccountContract::GlobalByAccount(id.clone()),
    };
```

**File:** runtime/runtime/src/contract_code.rs (L36-50)
```rust
    pub(crate) fn resolve(
        account_id: &AccountId,
        account_contract: AccountContract,
        state_update: &TrieUpdate,
        chain_id: &str,
        access: AccessOptions,
    ) -> Result<Self, StorageError> {
        let local_hash = match GlobalContractIdentifier::try_from(account_contract) {
            Ok(gci) => {
                let code_hash = gci.clone().hash(state_update, access)?;
                return Ok(RuntimeContractIdentifier::Global { code_hash, identifier: gci });
            }
            Err(ContractIsLocalError::NotDeployed) => return Ok(RuntimeContractIdentifier::None),
            Err(ContractIsLocalError::Deployed(local_hash)) => local_hash,
        };
```

**File:** runtime/runtime/src/contract_code.rs (L91-106)
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
```

**File:** runtime/runtime/src/pipelining.rs (L173-227)
```rust
        for (action_index, action) in actions.iter().enumerate() {
            let account_id = account_id.clone();
            match action {
                Action::DeployContract(_)
                | Action::UseGlobalContract(_)
                | Action::DeterministicStateInit(_)
                | Action::CreateAccount(_)
                | Action::DeleteAccount(_) => {
                    // Any action that can change the account's executable-code identity within
                    // this chunk must block preparation for the receiver. Otherwise a later
                    // function call could be prepared against the account's current contract
                    // and then executed under a freshly created or recreated account with
                    // different (or no) code.
                    //
                    // FIXME: instead of blocking these accounts, move the handling of
                    // code-identity-changing actions into here, so that the necessary data
                    // dependencies can be established.
                    return self.block_accounts.insert(account_id);
                }
                Action::FunctionCall(function_call) => {
                    let account = if let Some(account) = &account {
                        account
                    } else {
                        let key = TrieKey::Account { account_id: account_id.clone() };
                        let Ok(Some(receiver)) = get_pure::<Account>(state_update, &key) else {
                            // Most likely reason this can happen is because the receipt is for
                            // an account that does not yet exist. This is a routine occurrence
                            // as accounts are created by sending some NEAR to a name that's
                            // about to be created.
                            continue;
                        };
                        account.insert(receiver)
                    };
                    // Check if the account's global contract has been blocked
                    // in this chunk (due to a global contract deployment).
                    match account.contract().as_ref() {
                        AccountContract::None => continue,
                        AccountContract::Global(h) => {
                            if self
                                .block_global_contracts
                                .contains(&GlobalContractIdentifier::CodeHash(*h))
                            {
                                continue;
                            }
                        }
                        AccountContract::GlobalByAccount(id) => {
                            if self
                                .block_global_contracts
                                .contains(&GlobalContractIdentifier::AccountId(id.clone()))
                            {
                                continue;
                            }
                        }
                        AccountContract::Local(_) => {}
                    }
```
