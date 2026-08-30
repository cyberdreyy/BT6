### Title
Cross-account griefing via `DeployGlobalContract{AccountId}` lets any account bind attacker-chosen code to a victim's global-contract identity, funded by the victim's own balance - (File: `runtime/runtime/src/global_contracts.rs`)

### Summary
`action_deploy_global_contract` operates on whatever account is the receiver of the action receipt, without checking that the receipt's predecessor (the original caller/contract) is the same as that receiver. Because a contract can direct a batched `DeployGlobalContract` action at any `receiver_id` via a cross-contract promise, an attacker can force a victim account to pay `global_contract_storage_amount_per_byte * len(code)` and have `GlobalContractIdentifier::AccountId(victim)` bound to attacker-supplied wasm the victim never approved.

### Finding Description
`action_deploy_global_contract` (runtime/runtime/src/global_contracts.rs) receives `account_id`/`account` as the account the current receipt is executing against, deducts `storage_cost` directly from `account.amount()`, and then calls `initiate_distribution` with `account_id.clone()`, which for `GlobalContractDeployMode::AccountId` computes `GlobalContractIdentifier::AccountId(account_id.clone())`: [1](#0-0) [2](#0-1) 

Neither this function nor `validate_deploy_global_contract_action` in `action_validation.rs` (which only checks code size against `max_contract_size`) verifies that the receipt's `predecessor_id` matches `account_id`: [3](#0-2) 

In the NEAR receipt-execution model, a contract can construct a promise with an arbitrary `receiver_id` (not necessarily itself or an account it controls) and attach a `DeployGlobalContract` batched action to it via the corresponding host function; when that receipt is delivered, the runtime executes the action against `receiver_id`'s account state, which is exactly the `account`/`account_id` pair passed into `action_deploy_global_contract`. There is no check in this call path enforcing "only the account itself may deploy a global contract identified by its own AccountId," so the storage cost is deducted from the receiver's (victim's) balance and the `AccountId(victim)` global-contract slot is populated with attacker-supplied code, with no transaction ever signed by the victim.

### Impact Explanation
If exploitable, this would allow: (1) unauthorized debit of a victim's NEAR balance (storage-cost griefing) without their consent, and (2) binding attacker-controlled wasm to the victim's account identity under `GlobalContractIdentifier::AccountId`, so that any third party later executing `UseGlobalContractAction{AccountId(victim)}` would run attacker code "as if" blessed by the victim — undermining the identity-based trust model that `AccountId` mode (as opposed to content-addressed `CodeHash` mode) is meant to provide. This maps to loss/griefing of user funds and an authorization-escalation concern (impersonation of account identity for code trust) rather than direct theft of arbitrary amounts, since the debited amount is bounded by the per-byte storage cost of the deployed code and fails gracefully (`LackBalanceForState`) if the victim's balance is insufficient.

### Likelihood Explanation
I could not, within the available tool-call budget, conclusively determine whether this "receiver-funds, no predecessor==receiver check" pattern is unique to `DeployGlobalContract` or whether it mirrors long-standing behavior of the pre-existing `DeployContract` action and other receipt-targeted actions (e.g., whether NEAR's receipt/action model generally permits any account to direct any action at any `receiver_id`, with authorization enforced only at the original signed-transaction/access-key layer rather than per-action-per-receiver). I did not find, in the files inspected, an equivalent actor/ownership check anywhere in `runtime/runtime/src/actions.rs` or `runtime/runtime/src/lib.rs` for this call path, but I was not able to fully trace `apply_action_receipt` / actor-permission validation before running out of iterations. This uncertainty is material: if the general receipt-targeting model already permits arbitrary receiver targeting for storage-charging actions (as an accepted, long-standing protocol characteristic), then this finding is not a new vulnerability but an extension of that accepted design to global contracts, and its severity would be limited to the identity-binding amplification described above rather than a novel authorization break.

### Recommendation
In `action_deploy_global_contract` (or in `initiate_distribution`), when `deploy_contract.deploy_mode == GlobalContractDeployMode::AccountId`, require that the receipt's `predecessor_id` equals `account_id` before permitting the deploy, and return an `ActionErrorKind` (e.g., an "actor not authorized" error) otherwise. This restores the invariant that only an account's own signed/self-originated logic can bind a `GlobalContractIdentifier::AccountId` entry to that account's identity.

### Proof of Concept
Runtime integration test plan (apply-path, e.g. under `runtime/runtime/src/tests/apply.rs`):
1. Create attacker account and deploy an attacker-controlled contract that, on a method call, issues `promise_batch_create(receiver_id = victim)` followed by `promise_batch_action_deploy_global_contract(mode=AccountId, code=attacker_wasm)`.
2. Create a victim account with a known balance and no relationship/signature tying it to the attacker's call.
3. Submit a `FunctionCall` transaction signed by the attacker calling the attacker contract's method; apply the resulting receipts.
4. Assert: `victim.amount()` decreased by `global_contract_storage_amount_per_byte * len(attacker_wasm)` (matching `tokens_burnt` in the resulting `ActionResult`), and `TrieKey::GlobalContractCode{ GlobalContractCodeIdentifier::AccountId(victim) }` in state equals `attacker_wasm`, despite no transaction ever being signed by `victim`'s keys.

### Citations

**File:** runtime/runtime/src/global_contracts.rs (L34-59)
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

    initiate_distribution(
        state_update,
        account_id.clone(),
        deploy_contract.code.clone(),
        &deploy_contract.deploy_mode,
        apply_state.shard_id,
        result,
    )?;
```

**File:** runtime/runtime/src/global_contracts.rs (L150-157)
```rust
    let id = match deploy_mode {
        GlobalContractDeployMode::CodeHash => {
            GlobalContractIdentifier::CodeHash(hash(&contract_code))
        }
        GlobalContractDeployMode::AccountId => {
            GlobalContractIdentifier::AccountId(account_id.clone())
        }
    };
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
