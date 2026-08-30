#No Vulnerability found for this question.

The behavior described is intended, documented protocol design rather than an authorization bypass. `GlobalContractDeployMode::AccountId` is explicitly designed to let a publisher push updates to all references that use `GlobalContractIdentifier::AccountId(publisher)` — this is stated directly in the spec: "Contract is deployed under the owner account id... This allows the owner to update the contract for all its users." [1](#0-0) 

Resolution of `AccountContract::GlobalByAccount(id)` (set via `use_global_contract`, called from `deploy_deterministic_account`) is performed dynamically at each call via `RuntimeContractIdentifier::resolve`, which looks up the current code hash stored under the `GlobalContractCode` trie key for that account id rather than pinning to a fixed hash captured at `use_global_contract`/state-init time. [2](#0-1) [3](#0-2) 

The `DeterministicAccountStateInit` itself only encodes the `GlobalContractIdentifier` enum variant (`AccountId(publisher)` vs `CodeHash(hash)`) — not the bytecode contents — when deriving the deterministic account id, so choosing `AccountId` mode is an explicit, self-selected trust decision by whoever constructs the `state_init` (and thus by extension, anyone who later grants a `FunctionCall` key scoped to that resulting account id). [4](#0-3) 

Test coverage confirms this update propagation is expected/tested behavior for `AccountId`-mode global contracts (nonce-based idempotent updates propagate to all referencing accounts), not treated as a bug. [5](#0-4) 

Since this is the documented, intended trust model for `AccountId`-mode global contracts — and users/relayers who scope keys to a deterministic account pinned to `GlobalContractIdentifier::AccountId(publisher)` are opting into that publisher's ongoing control by construction, not having their authorization bypassed by an unprivileged third party — this does not meet the bar for an unauthorized/exploitable vulnerability under the given rules. Choosing `CodeHash` mode instead yields immutable, hash-pinned code exactly to avoid this scenario, which is the documented mitigation already available.

### Citations

**File:** docs/RuntimeSpec/Actions.md (L445-448)
```markdown
    /// Contract is deployed under the owner account id.
    /// Users will be able reference it by that account id.
    /// This allows the owner to update the contract for all its users.
    AccountId,
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

**File:** runtime/runtime/src/global_contracts.rs (L94-97)
```rust
    let contract = match contract_identifier {
        GlobalContractIdentifier::CodeHash(code_hash) => AccountContract::Global(*code_hash),
        GlobalContractIdentifier::AccountId(id) => AccountContract::GlobalByAccount(id.clone()),
    };
```

**File:** runtime/runtime/src/deterministic_account_id.rs (L121-133)
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
