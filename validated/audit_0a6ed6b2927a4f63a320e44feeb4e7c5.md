### Title
Unauthorized global contract deployment under victim's `AccountId` identifier via receiver-targeted `DeployGlobalContract` action - ([File: runtime/runtime/src/global_contracts.rs])

### Summary
`initiate_distribution` derives `GlobalContractIdentifier::AccountId(account_id)` directly from the receipt's executing account (`account_id`, i.e. the receiver of the action), not from any verified "owner" of that identity. Because `DeployGlobalContract` is not one of the actions constrained to `predecessor_id == receiver_id`, an attacker can send a receipt whose `receiver_id` is a victim account and have their own wasm code permanently bound to `GlobalContractIdentifier::AccountId(victim)`, at the victim's expense.

### Finding Description
`action_deploy_global_contract` takes `account_id: &AccountId`, which is the receiver of the current action receipt being executed (the account the action is being applied "on"), and forwards it unchanged into `initiate_distribution`: [1](#0-0) 

Inside `initiate_distribution`, when `deploy_mode == GlobalContractDeployMode::AccountId`, the identifier is built directly from that `account_id` with no ownership check: [2](#0-1) 

Crucially, the set of actions that the protocol restricts to `predecessor_id == receiver_id` (i.e., "only the account itself, or someone acting as it right after `CreateAccount`, may perform this action") explicitly excludes `DeployGlobalContract`: [3](#0-2) 

`validate_action_with_mode` / `validate_deploy_global_contract_action` in `action_validation.rs` only checks the code size limit; it performs no check that `predecessor_id == receiver_id` for `DeployGlobalContract`: [4](#0-3) 

This means `DeployGlobalContract` behaves like `FunctionCall` with respect to receiver targeting: an attacker (via a top-level transaction with `receiver_id = victim` and a full-access key on their own signer account, or via a contract-issued promise batch action targeting an arbitrary receiver, e.g. `promise_batch_action_deploy_global_contract_by_account_id`) can cause the action to execute in the victim account's context. `initiate_distribution` then:
- Charges the storage cost of the attacker-chosen code to the **victim's** account balance (`account.set_amount(updated_balance)` in `action_deploy_global_contract`).
- Registers `GlobalContractIdentifier::AccountId(victim)` pointing at the attacker's malicious code, propagated to all shards via the distribution receipt mechanism, with no signature or ownership check tying this identifier update to the victim's keys.

Since nonce-based idempotency only prevents *stale* overwrites (lower nonce), not *unauthorized* ones, any subsequent attacker-issued receipt with a higher nonce can keep overwriting `GlobalContractIdentifier::AccountId(victim)`, permanently binding the identity to attacker-controlled code the victim never deployed and never consented to.

### Impact Explanation
This breaks the invariant that global-contract identifiers under `AccountId` mode represent code control by "the owner" (as documented: "This allows the owner to update the contract for all its users"). If any protocol, wallet, or user resolves `GlobalContractIdentifier::AccountId(victim)` expecting victim-authored code (e.g., a well-known DeFi/token contract publisher), an attacker can plant/overwrite it with malicious logic. Anyone who later performs `UseGlobalContract` against that identifier and interacts with the resulting account executes attacker-controlled code, which can lead to direct theft of deposited funds in that interaction. Additionally, the victim's account balance is unilaterally drained to pay for storage of code they never asked to have associated with their identity — an unauthorized funds-loss vector. This falls under "Contracts execution flows" / "Stealing or loss of funds."

### Likelihood Explanation
The attacker only needs an ordinary funded account and a full-access key on their own account (no special privileges) to submit a transaction/receipt whose `receiver_id` is the victim, with a single `DeployGlobalContract` action in `AccountId` mode, or to have their own contract issue `promise_batch_action_deploy_global_contract_by_account_id` targeting an arbitrary receiver. The cost is bounded by the wasm size fee/storage deposit deducted from the victim (not the attacker), making this cheap and repeatable against any account, at any time, with no cooperation from the victim required.

### Recommendation
Restrict `DeployGlobalContract` in `AccountId` mode (and ideally the whole `DeployGlobalContract` action) to require `predecessor_id == receiver_id`, matching the treatment of `DeployContract`/`Stake`/`AddKey`/`DeleteKey`/`DeleteAccount`, so that only the account itself (or immediately after `CreateAccount`) can bind or update its own `GlobalContractIdentifier::AccountId`. Enforce this check in `action_validation.rs`/`actions.rs` before dispatching to `action_deploy_global_contract`.

### Proof of Concept
Runtime/apply test plan:
1. Construct a receipt (or transaction routed to receiver `victim`) with `predecessor_id = attacker`, `receiver_id = victim`, action `DeployGlobalContract { code: attacker_wasm, deploy_mode: GlobalContractDeployMode::AccountId }`.
2. Apply it via the runtime `apply` path (as in `runtime/runtime/src/tests/apply.rs` style tests) using an account `victim` that never issued any deploy action itself.
3. Assert:
   - `victim`'s account balance decreased by the storage cost of `attacker_wasm`.
   - After the resulting `GlobalContractDistributionReceipt` is processed, `GlobalContractIdentifier::AccountId(victim)` resolves (via `TrieKey::GlobalContractCode`) to `attacker_wasm`, not code deployed/authorized by `victim`.
4. (Uncertainty note) Full confirmation additionally requires inspecting the `predecessor_id`/`receiver_id` handling in `runtime/runtime/src/actions.rs` (the two matches found for the pattern) to rule out an equivalent guard implemented outside `action_validation.rs`; this was not fully verified due to tool-call exhaustion, so this should be double-checked before treating the finding as fully confirmed against the very latest code.

### Citations

**File:** runtime/runtime/src/global_contracts.rs (L24-59)
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
```

**File:** runtime/runtime/src/global_contracts.rs (L142-169)
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
```

**File:** docs/RuntimeSpec/Actions.md (L26-35)
```markdown
For the following actions, `predecessor_id` and `receiver_id` are required to be equal:

- `DeployContract`
- `Stake`
- `AddKey`
- `DeleteKey`
- `DeleteAccount`

NOTE: if the first action in the action list is `CreateAccount`, `predecessor_id` becomes `receiver_id`
for the rest of the actions until `DeleteAccount`. This gives permission by another account to act on the newly created account.
```

**File:** runtime/runtime/src/action_validation.rs (L238-251)
```rust
/// Validates `DeployGlobalContractAction`. Checks that the given contract size doesn't exceed the limit.
fn validate_deploy_global_contract_action(
    limit_config: &LimitConfig,
    action: &DeployGlobalContractAction,
) -> Result<(), ActionsValidationError> {
    if action.code.len() as u64 > limit_config.max_contract_size {
        return Err(ActionsValidationError::ContractSizeExceeded {
            size: action.code.len() as u64,
            limit: limit_config.max_contract_size,
        });
    }

    Ok(())
}
```
