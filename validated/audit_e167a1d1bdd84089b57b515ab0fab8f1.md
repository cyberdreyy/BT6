### Title
Top-level `CreateAccount` on an unclaimed `0s`+40-hex ID bypasses implicit-account protection, allowing key-hijack of a future NEP-616 deterministic account - (File: runtime/runtime/src/actions.rs)

### Summary
`check_account_existence`'s `Action::CreateAccount` branch only rejects account IDs that `account_is_implicit` classifies as `NearImplicitAccount`/`EthImplicitAccount`; it has no case for the `0s`+40-hex `NearDeterministicAccount` shape used by NEP-616. An attacker can therefore `CreateAccount`+`AddKey(FullAccess)` on a self-chosen `0s…` string (of a length that satisfies the ordinary top-level naming rules) before anyone deploys a deterministic-account `state_init` there, and later `action_deterministic_state_init` will happily deploy the contract onto that pre-existing, attacker-keyed account without ever touching access keys.

### Finding Description
`account_is_implicit` in [1](#0-0)  only special-cases `AccountType::NearImplicitAccount` and, when ETH-implicit accounts are enabled, whatever `AccountType::is_implicit()` reports from the `near-account-id` crate — a helper that predates and is not documented to be aware of the newer NEP-616 `0s`+40-hex deterministic-account shape produced by `derive_near_deterministic_account_id` in [2](#0-1) .

`check_account_existence`'s `Action::CreateAccount` branch calls exactly this function to decide whether a target is an implicit-shaped account that must be created only via a bare `Transfer`: [3](#0-2) 

Because the deterministic-account pattern is not represented among the checked `AccountType` variants, a `0s…` string is treated as an ordinary named account. If its length clears the top-level account-name rules enforced in `action_create_account`, an attacker can submit `CreateAccount` + `AddKey(attacker_pubkey, FullAccess)` to that exact string in one transaction and successfully claim it with a signing key — something that is supposed to be structurally impossible for a deterministic account per NEP-616 (the whole design point being "code-defined behavior, no signing key").

Later, when the legitimate deterministic-account flow sends a `DeterministicStateInitAction` to the same `id` (validated by `validate_deterministic_state_init`, which pins receiver == `derive_near_deterministic_account_id(state_init)`), `action_deterministic_state_init` finds the account already exists but `account.contract().is_none()` (attacker never deployed anything), so it proceeds to `deploy_deterministic_account`: [4](#0-3) 

`deploy_deterministic_account` only manipulates contract code/data and storage usage — it never inspects or removes access keys: [5](#0-4) 

The result: the account now runs the intended contract, but the attacker's `FullAccess` key from step 1 still resolves, breaking the "keyless, code-only-governed account" invariant and letting the attacker later sign a `Transfer` to drain any funds sent to `id`.

### Impact Explanation
This is unauthorized access/theft of funds: any account (relayer, dApp, or ordinary user) that funds `id` believing it is exclusively governed by the deployed deterministic contract is actually exposed to an attacker-held `FullAccess` key that predates the contract deployment and was never removed. This maps to the "Contracts execution flows / Unauthorized transaction" bounty category — permanent compromise/theft of funds sent to an account that third parties reasonably believe is keyless.

### Likelihood Explanation
The attacker needs no privileged access: they pick their own `DeterministicAccountStateInit`, compute `id = derive_near_deterministic_account_id(&state_init)` client-side (no preimage search — the attacker fully controls the input), and race to submit an ordinary `CreateAccount`+`AddKey` transaction to that `id` before anyone else claims it. This is repeatable for any `id` an attacker anticipates a protocol will target for deterministic-account use, and costs only normal account-creation/storage-staking fees plus gas.

### Recommendation
Extend `account_is_implicit` (or add a dedicated check in `check_account_existence`'s `CreateAccount` branch and in `action_create_account`'s top-level-name rule) to also reject any account ID matching the `0s`+40-hex (and `0u` universal-account) deterministic-account shape, exactly as is already done for `NearImplicitAccount`/`EthImplicitAccount`, so ordinary `CreateAccount` can never claim an ID reserved for NEP-616 deterministic derivation.

### Proof of Concept
Runtime/apply integration test:
1. Compute `state_init` and `id = derive_near_deterministic_account_id(&state_init)`.
2. Submit a transaction to `id` with actions `[CreateAccount, AddKey(attacker_pubkey, FullAccess)]` from a funding account acting as predecessor; assert it succeeds (no `OnlyImplicitAccountCreationAllowed` error).
3. Submit `DeterministicStateInitAction { state_init }` to receiver `id`; assert it succeeds and deploys the contract (`account.contract().is_some()`).
4. Query `get_access_key(id, attacker_pubkey)`; assert it still resolves to a `FullAccess` key.
5. Submit a `Transfer` from `id` signed by `attacker_pubkey`; assert it succeeds, demonstrating fund drainage from a supposedly keyless account.

### Citations

**File:** core/primitives/src/utils.rs (L468-477)
```rust
/// From `near-account-id` version `1.0.0-alpha.2`, `is_implicit` returns true for ETH-implicit accounts.
/// This function is a wrapper for `is_implicit` method so that we can easily differentiate its behavior
/// based on whether ETH-implicit accounts are enabled.
pub fn account_is_implicit(account_id: &AccountId, eth_implicit_accounts_enabled: bool) -> bool {
    if eth_implicit_accounts_enabled {
        account_id.get_account_type().is_implicit()
    } else {
        account_id.get_account_type() == AccountType::NearImplicitAccount
    }
}
```

**File:** core/primitives/src/utils.rs (L493-503)
```rust
/// Returns a NEP-616 compliant deterministic account id.
/// This is a NEAR-implicit account ID which is fully defined by its initial state.
pub fn derive_near_deterministic_account_id(
    state_init: &DeterministicAccountStateInit,
) -> AccountId {
    use sha3::Digest;
    let mut hasher = sha3::Keccak256::new();
    borsh::to_writer(&mut hasher, state_init).expect("borsh must not fail");
    let hash: [u8; 32] = hasher.finalize().into();
    format!("0s{}", hex::encode(&hash[12..32])).parse().unwrap()
}
```

**File:** runtime/runtime/src/actions.rs (L794-818)
```rust
    match action {
        Action::CreateAccount(_) => {
            if account.is_some() {
                return Err(ActionErrorKind::AccountAlreadyExists {
                    account_id: account_id.clone(),
                }
                .into());
            } else {
                if account_is_implicit(account_id, config.wasm_config.eth_implicit_accounts) {
                    // If the account doesn't exist and it's implicit, then you
                    // should only be able to create it using single transfer action.
                    // Because you should not be able to add another access key to the account in
                    // the same transaction.
                    // Otherwise you can hijack an account without having the private key for the
                    // public key. We've decided to make it an invalid transaction to have any other
                    // actions on the implicit hex accounts.
                    // The easiest way is to reject the `CreateAccount` action.
                    // See https://github.com/nearprotocol/NEPs/pull/71
                    return Err(ActionErrorKind::OnlyImplicitAccountCreationAllowed {
                        account_id: account_id.clone(),
                    }
                    .into());
                }
            }
        }
```

**File:** runtime/runtime/src/deterministic_account_id.rs (L27-52)
```rust
    let account = match maybe_account {
        Some(account) => account,
        None => {
            // cspell:ignore nonexist
            // `nonexist` -> `uninit` account state transition
            // Create with zero balance now and check later how much of the
            // provided deposit is needed.
            let new_account = create_deterministic_account(Balance::ZERO, storage_usage_config);
            maybe_account.insert(new_account)
        }
    };
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

**File:** runtime/runtime/src/deterministic_account_id.rs (L121-154)
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

    // Step 2: insert provided key-value pairs
    let mut required_storage_usage = account.storage_usage();
    for (key, value) in state_init.data() {
        let trie_key = TrieKey::ContractData { account_id: account_id.clone(), key: key.to_vec() };

        let value_bytes = value.len() as u64;
        let key_bytes = key.len() as u64;
        let extra_per_record_bytes = storage_usage_config.num_extra_bytes_record;

        let new_bytes = value_bytes
            .checked_add(key_bytes)
            .and_then(|acc| acc.checked_add(extra_per_record_bytes))
            .ok_or(IntegerOverflowError {})?;
        state_update.set(trie_key, value.clone());
        required_storage_usage =
            required_storage_usage.checked_add(new_bytes).ok_or(IntegerOverflowError {})?;
    }
    account.set_storage_usage(required_storage_usage);

    Ok(())
```
