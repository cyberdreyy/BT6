#No vulnerability found for this question.

**Rationale:** The behavior described is explicit, documented, and intentional design—not a bug. The `GlobalContractDeployMode::AccountId` mode exists specifically so that "the owner can update the contract for all its users" who reference it by account id, as opposed to `CodeHash` mode which is documented as immutable [1](#0-0) . This same distinction is defined at the type level in `GlobalContractDeployMode` [2](#0-1) .

When a victim account signs a `UseGlobalContractAction` with `GlobalContractIdentifier::AccountId(X)`, they are explicitly opting into "whatever code account X currently has (or will have) deployed under that identity," rather than pinning to a specific `CodeHash`. This is the same trust model as choosing a mutable dependency over a content-addressed/immutable one; the alternative (`CodeHash` mode) exists precisely for users who want immutability and content-pinning. Choosing `AccountId` mode is the victim's own informed choice, and the redeployment path is gated by nonce-based ordering to guarantee deterministic propagation, not to prevent legitimate updates by the owner [3](#0-2) .

The resolution path `RuntimeContractIdentifier::resolve` and `GlobalContractAccessExt::hash`/`code` correctly and deterministically reflect whatever code is currently stored under `TrieKey::GlobalContractCode` for that identifier at the queried state root—this is expected, not a determinism violation, since the account never claimed to pin a hash [4](#0-3) [5](#0-4) .

This exact scenario (owner redeploying under `AccountId` mode, and all "users" of that identifier observing the new code without re-signing) is covered by an existing test that treats it as correct/expected behavior, explicitly checking that the newer contract version becomes active for all users after redeployment: `test_global_contract_nonce_prevents_stale_overwrite` [6](#0-5) . The pytest integration test `deploy_call_global_smart_contract.py` also demonstrates redeployment under `AccountId` mode being picked up by users who already called `use_global_contract` [7](#0-6) .

No authorization escalation occurs: the "attacker" is simply the legitimate holder of account `X`'s keys exercising their own account's contract-deployment privilege, which is the exact privilege the `AccountId` deploy mode grants by design. The victim's signed `UseGlobalContractAction` names the identifier, not a hash, precisely because that is the documented semantic of choosing mutable-by-owner mode. There is no state-root divergence, double-spend, fund theft, or freezing—storage usage tracking via `identifier.len()` (constant regardless of code size) is also working as designed since the account only stores a reference, not the code itself [8](#0-7) .

### Citations

**File:** docs/RuntimeSpec/Actions.md (L440-449)
```markdown
pub enum GlobalContractDeployMode {
    /// Contract is deployed under its code hash.
    /// Users will be able reference it by that hash.
    /// This effectively makes the contract immutable.
    CodeHash,
    /// Contract is deployed under the owner account id.
    /// Users will be able reference it by that account id.
    /// This allows the owner to update the contract for all its users.
    AccountId,
}
```

**File:** chain/jsonrpc/openapi/openrpc.json (L5474-5488)
```json
      "GlobalContractDeployMode": {
        "oneOf": [
          {
            "const": "CodeHash",
            "description": "Contract is deployed under its code hash.\nUsers will be able reference it by that hash.\nThis effectively makes the contract immutable.",
            "type": "string"
          },
          {
            "const": "AccountId",
            "description": "Contract is deployed under the owner account id.\nUsers will be able reference it by that account id.\nThis allows the owner to update the contract for all its users.",
            "type": "string"
          }
        ],
        "title": "GlobalContractDeployMode"
      },
```

**File:** runtime/runtime/src/global_contracts.rs (L172-188)
```rust
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

**File:** runtime/runtime/src/contract_code.rs (L32-73)
```rust
impl RuntimeContractIdentifier {
    /// Resolve a contract identifier from an account's contract field.
    ///
    /// Returns `RuntimeContractIdentifier::None` if the account has no contract deployed.
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

        if account_id.get_account_type() == AccountType::EthImplicitAccount {
            // Accounts that look like eth implicit accounts and have existed prior to the
            // eth-implicit accounts protocol change (these accounts are discussed in the
            // description of #11606) may have something else deployed to them. Only return
            // something here if the accounts have a wallet contract hash. Otherwise use the
            // regular path to grab the deployed contract.
            if LegacyEthWallet::resolve(local_hash).is_some() {
                // ETH implicit wallet accounts use global contracts, including
                // those created in old protocol versions.
                let global_hash = eth_wallet_global_contract_hash(chain_id);
                return Ok(RuntimeContractIdentifier::Global {
                    code_hash: global_hash,
                    identifier: GlobalContractIdentifier::CodeHash(global_hash),
                });
            }
        }

        Ok(RuntimeContractIdentifier::AccountLocal {
            code_hash: local_hash,
            account_id: account_id.clone(),
        })
    }
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

**File:** test-loop-tests/src/tests/global_contracts_distribution.rs (L268-324)
```rust
/// Test that nonce-based idempotency prevents stale overwrites during global contract updates.
///
/// Deploys a trivial contract first (AccountId mode), waits for distribution,
/// then deploys rs_contract (AccountId mode) with a higher auto-incremented nonce.
/// Verifies all shards have the newer version by calling a function that only
/// exists in the rs_contract.
#[test]
#[cfg_attr(feature = "protocol_feature_spice", ignore)]
fn test_global_contract_nonce_prevents_stale_overwrite() {
    init_test_logger();
    let mut env = GlobalContractsReshardingTestEnv::setup();

    let deploy_user = env.users[0].clone();

    // Step 1: Deploy trivial contract as first version (AccountId mode).
    tracing::info!(target: "test", "Deploying first version of global contract (trivial contract)...");
    let tx = env.chunk_producer_node().tx_deploy_global_contract(
        &deploy_user,
        near_test_contracts::trivial_contract().to_vec(),
        GlobalContractDeployMode::AccountId,
    );
    env.env.runner_for_account(&env.chunk_producer).run_tx(tx, Duration::seconds(5));

    // Step 2: Deploy rs_contract as second version (AccountId mode).
    // This will have a higher auto-incremented nonce.
    tracing::info!(target: "test", "Deploying second version of global contract (rs_contract)...");
    let tx = env.chunk_producer_node().tx_deploy_global_contract(
        &deploy_user,
        near_test_contracts::rs_contract().to_vec(),
        GlobalContractDeployMode::AccountId,
    );
    env.env.runner_for_account(&env.chunk_producer).run_tx(tx, Duration::seconds(5));

    // Step 3: Have all users use the global contract and verify that the rs_contract
    // version (v2) is active by calling "log_something" which only exists in rs_contract.
    tracing::info!(target: "test", "Calling use global contract from all users to verify rs_contract is active...");
    for user in &env.users {
        let identifier = GlobalContractIdentifier::AccountId(deploy_user.clone());
        let tx = env.chunk_producer_node().tx_use_global_contract(user, identifier);
        env.env.runner_for_account(&env.chunk_producer).run_tx(tx, Duration::seconds(5));
    }

    // Step 4: Call "log_something" on each user's account. This method only exists in
    // the rs_contract, so if the trivial contract had overwritten it, this would fail.
    tracing::info!(target: "test", "Calling contract method from all users to verify rs_contract is active...");
    for user in &env.users {
        let tx = env.chunk_producer_node().tx_call(
            user,
            user,
            "log_something",
            vec![],
            Balance::ZERO,
            Gas::from_teragas(300),
        );
        env.env.runner_for_account(&env.chunk_producer).run_tx(tx, Duration::seconds(5));
    }
}
```

**File:** pytest/tests/contracts/deploy_call_global_smart_contract.py (L55-67)
```python
    # Redeploy global contract using AccountId method
    deploy_mode = GlobalContractDeployMode()
    deploy_mode.enum = 'accountId'
    deploy_mode.accountId = ()
    deploy_global_contract(rpc, nodes[0], test_contract, deploy_mode, 50)

    identifier = GlobalContractIdentifier()
    identifier.enum = "accountId"
    identifier.accountId = nodes[0].signer_key.account_id
    use_global_contract(rpc, nodes[1], identifier, 60)

    call_contract(rpc, nodes[0], nodes[1].signer_key.account_id, 70)
    call_contract(rpc, nodes[1], nodes[1].signer_key.account_id, 80)
```

**File:** core/primitives-core/src/global_contract.rs (L33-38)
```rust
    pub fn len(&self) -> usize {
        match self {
            GlobalContractIdentifier::CodeHash(_) => 32,
            GlobalContractIdentifier::AccountId(account_id) => account_id.len(),
        }
    }
```
