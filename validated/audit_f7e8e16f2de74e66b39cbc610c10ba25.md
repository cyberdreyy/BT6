### Title
Missing actor-permission (predecessor==receiver) check on `UseGlobalContractAction` allows an attacker to rebind a victim account's contract to an attacker-controlled `GlobalContractIdentifier::AccountId`, enabling later fund drain without victim consent - ([File: runtime/runtime/src/global_contracts.rs])

### Summary
`action_use_global_contract` / `use_global_contract` in `runtime/runtime/src/global_contracts.rs` mutates the *receiver* account's `AccountContract` field based solely on whether the referenced `GlobalContractIdentifier` exists in the trie, with no check that the receipt's predecessor/signer is the receiver itself. `docs/RuntimeSpec/Actions.md` documents that the "predecessor_id must equal receiver_id" (actor-permission / `ActorNoPermission`) restriction is enforced only for `DeployContract`, `Stake`, `AddKey`, `DeleteKey`, `DeleteAccount` — `UseGlobalContract` is conspicuously absent from that list, and neither `validate_use_global_contract_action` (`runtime/runtime/src/action_validation.rs:253-259`) nor `use_global_contract` (`runtime/runtime/src/global_contracts.rs:75-108`) perform any predecessor/receiver identity check.

### Finding Description
`use_global_contract` (runtime/runtime/src/global_contracts.rs:75-108) is invoked for any receipt containing a `UseGlobalContractAction`, operating directly on `account_id`/`account`, which are the *receiver's* account state: [1](#0-0) 

The only gate is `state_update.contains_key(&key, ...)` — i.e., whether the referenced `GlobalContractIdentifier` (a `CodeHash` or an `AccountId`) has *any* global contract registered under it anywhere on chain, not whether the caller is authorized to modify the receiver account: [2](#0-1) 

Validation of the action (`validate_use_global_contract_action`, `runtime/runtime/src/action_validation.rs:253-259`) only checks that an `AccountId`-form identifier is a syntactically valid account id — it does not check receiver==predecessor: [3](#0-2) 

`docs/RuntimeSpec/Actions.md` explicitly enumerates the actions that require `predecessor_id == receiver_id` for authorization ("Administrative actions"), and `UseGlobalContract`/`DeployGlobalContract` are not in that set: [4](#0-3) 

This matches the `ActorNoPermission` error definition, which documents the same restricted set: "Administrative actions like `DeployContract`, `Stake`, `AddKey`, `DeleteKey`. can be proceed only if sender=receiver": [5](#0-4) 

**Exploit flow (attacker rebinds victim, "flipped" scenario in the question):**
1. Attacker (ordinary funded account, own signing key) submits `DeployGlobalContractAction{code: benign_code, deploy_mode: AccountId}` on `attacker.near`, registering global contract code under `GlobalContractIdentifier::AccountId(attacker.near)`.
2. Attacker signs a `SignedTransaction` with `signer_id = attacker.near` (using attacker's own full access key — no leaked keys, no special privilege) and `receiver_id = victim.near`, action list `[UseGlobalContractAction{contract_identifier: AccountId(attacker.near)}]`.
3. Because `UseGlobalContract` is not subject to the `predecessor_id == receiver_id` actor-permission check that protects `DeployContract`/`AddKey`/etc., this receipt executes and calls `use_global_contract` against `victim.near`'s account, setting `victim.near.contract = AccountContract::GlobalByAccount(attacker.near)` — with zero involvement or signature from `victim.near`.
4. Attacker later redeploys malicious code under the same `AccountId(attacker.near)` global-contract slot (`DeployGlobalContractAction{deploy_mode: AccountId}` intentionally allows the owner to update code in place — this is by-design mutability, per the docs).
5. Because `AccountContract::GlobalByAccount` is resolved to code *by identifier lookup at execution time* (`RuntimeContractIdentifier::resolve` in `runtime/runtime/src/contract_code.rs:36-73`, and `GlobalContractAccessExt::code`/`hash`), the *next* call executed on `victim.near` (a permissionless `FunctionCall` receipt anyone can send) runs the attacker's newly-swapped-in malicious code with `victim.near`'s own balance and predecessor context, letting the code issue a self-transfer/drain action moving `victim.near`'s NEAR to the attacker.

This is exactly the scenario the invariant is meant to prevent: a third party's future contract redeployment (step 4) retroactively controls another account's already-existing funds (step 5), without the account owner (`victim.near`) ever consenting to step 3's `UseGlobalContract` action.

### Impact Explanation
Direct theft of user funds: an unprivileged attacker can force any target account to bind its contract identity to an attacker-owned, attacker-mutable global contract reference and subsequently drain the victim account's NEAR balance via a self-transfer method embedded in swapped-in code. This falls under "theft of user funds" / "authorization escalation across accounts" in the NEAR bounty taxonomy — the most severe category, since it requires no compromise of the victim at all, only two ordinary attacker transactions.

### Likelihood Explanation
Preconditions are minimal and entirely within an ordinary unprivileged attacker's control: fund one account, sign one `DeployGlobalContractAction` (AccountId mode) and one `UseGlobalContractAction` transaction targeting an arbitrary victim receiver, then later redeploy and call. Cost is limited to gas/storage-staking fees for two small transactions. The attack is repeatable against any account that does not already have `UseGlobalContract` blocked (there is no opt-out), and does not require the victim to interact at all until the final drain-triggering call.

### Recommendation
Add `UseGlobalContract` (and review `DeployGlobalContract` in `AccountId` mode, plus `DeterministicStateInit`) to the actor-permission-gated action set so that `UseGlobalContractAction` can only be executed when `predecessor_id == receiver_id` (or immediately following a `CreateAccount` action performed by the same actor), mirroring the existing `ActorNoPermission` check used for `DeployContract`/`AddKey`/`DeleteKey`/`Stake`/`DeleteAccount`. This should be enforced both at the runtime execution dispatcher and reflected in `docs/RuntimeSpec/Actions.md`.

### Proof of Concept
Runtime/`apply.rs`-style integration test:
1. Create `attacker.near` and `victim.near` accounts, fund both.
2. Send receipt: `attacker.near` → `attacker.near`, action `DeployGlobalContractAction{code: benign_wasm, deploy_mode: AccountId}`. Assert global contract code stored under `AccountId(attacker.near)`.
3. Send receipt: predecessor `attacker.near`, receiver `victim.near`, action `UseGlobalContractAction{contract_identifier: AccountId(attacker.near)}`, signed only by `attacker.near`'s own key (not `victim.near`'s). Assert this receipt succeeds (no `ActorNoPermission` error) and `victim.near.contract() == AccountContract::GlobalByAccount(attacker.near)`.
4. Send receipt: `attacker.near` → `attacker.near`, action `DeployGlobalContractAction{code: malicious_wasm_that_transfers_predecessor_balance_to_attacker, deploy_mode: AccountId}` (redeploy under same identifier).
5. Record `victim.near`'s balance, then send a `FunctionCall` receipt to `victim.near` invoking the malicious exported method.
6. Assert `victim.near`'s balance decreased and `attacker.near`'s balance increased by a corresponding amount, with no transaction ever signed by `victim.near`'s own keys — proving unauthorized code-binding and subsequent fund drain.

### Citations

**File:** runtime/runtime/src/global_contracts.rs (L75-108)
```rust
pub(crate) fn use_global_contract(
    state_update: &mut TrieUpdate,
    account_id: &AccountId,
    account: &mut Account,
    contract_identifier: &GlobalContractIdentifier,
    result: &mut ActionResult,
) -> Result<(), RuntimeError> {
    let key = TrieKey::GlobalContractCode { identifier: contract_identifier.clone().into() };
    if !state_update.contains_key(&key, AccessOptions::DEFAULT)? {
        result.result = Err(ActionErrorKind::GlobalContractDoesNotExist {
            identifier: contract_identifier.clone(),
        }
        .into());
        return Ok(());
    }
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
}
```

**File:** runtime/runtime/src/action_validation.rs (L253-259)
```rust
fn validate_use_global_contract_action(
    action: &UseGlobalContractAction,
) -> Result<(), ActionsValidationError> {
    validate_global_contract_identifier(&action.contract_identifier)?;

    Ok(())
}
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

**File:** core/primitives/src/errors.rs (L743-748)
```rust
    /// Administrative actions like `DeployContract`, `Stake`, `AddKey`, `DeleteKey`. can be proceed only if sender=receiver
    /// or the first TX action is a `CreateAccount` action
    ActorNoPermission {
        account_id: AccountId,
        actor_id: AccountId,
    } = 4,
```
