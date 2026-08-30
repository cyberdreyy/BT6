### Title
`CreateAccount` + `AddKey` can front-run/squat a `NearDeterministicAccount` id because `check_account_existence`/`action_create_account` only special-case `NearImplicit`/`EthImplicit` accounts - (File: runtime/runtime/src/actions.rs)

### Summary
`check_account_existence` rejects a `CreateAccount` action against an account id that doesn't yet exist only when the id is `NearImplicitAccount` or `EthImplicitAccount` (via `account_is_implicit`), explicitly to prevent hijacking the future owner's access-key control. `action_create_account` itself contains no account-type gating at all - it only enforces top-level-length/registrar and sub-account/predecessor rules. `AccountType::NearDeterministicAccount` (used for `DeterministicStateInit`) is not covered by either check, and `DeterministicStateInit` treats an already-existing target account as a silent no-op rather than an error.

### Finding Description
`check_account_existence` (runtime/runtime/src/actions.rs:787-818) rejects `CreateAccount` on a non-existent account only if `account_is_implicit(account_id, ...)` is true, which - per the accompanying comment - only matches `NearImplicitAccount`/`EthImplicitAccount`: [1](#0-0) 

`action_create_account` (runtime/runtime/src/actions.rs:167-210) performs no `AccountType`/implicit check at all; it only validates top-level-length/registrar rules or sub-account/predecessor ownership: [2](#0-1) 

Meanwhile, `check_account_existence`'s handling of `Action::DeterministicStateInit` explicitly allows the target account to already exist and, in that case, does nothing to the account and does not abort the receipt: [3](#0-2) 

This means an attacker who can predict/compute a `NearDeterministicAccount` id (deterministically derived from public state-init data, e.g. contract code/constructor args a deployer intends to publish) can submit an ordinary `CreateAccount` + `AddKey` transaction for that exact account id before the legitimate `DeterministicStateInit` transaction lands. As long as the id satisfies the ordinary top-level-length rule or is a sub-account the attacker is entitled to create, `action_create_account` will happily create it and let the attacker install their own access key - exactly the "hijack an account without having the private key" scenario the code comment describes for implicit accounts, but here that protection is absent for the deterministic-account type. When the legitimate `DeterministicStateInit` transaction later arrives targeting the now-existing (attacker-controlled) account, `check_account_existence` treats it as a harmless no-op instead of failing, so the deployer gets no error signal that their deterministic contract was never actually installed, while the attacker retains the access key on that address.

### Impact Explanation
This is an authorization-escalation / account-hijack risk within the Contracts execution / Unauthorized transaction category: an unprivileged attacker can seize control (via a self-added access key) of an account id that a legitimate user/protocol expects to be initialized deterministically with specific contract code, and any funds later sent to that address (trusting the deterministic derivation to correspond to the intended code) are exposed to the attacker's key rather than the intended owner/contract.

### Likelihood Explanation
Exploitability depends on preconditions I could not fully verify in this pass: (1) the exact string/length format `NearDeterministicAccount` ids take (whether they satisfy `is_top_level()` with sufficient length, or are sub-accounts requiring a specific predecessor to create), and (2) whether `get_account_type`/`account_is_implicit` or some other gate (e.g. `validate_action_account_id`, `verify_and_charge_tx_ephemeral`) implicitly restricts who/what can be the predecessor for such ids. I was not able to locate the implementation of `account_is_implicit`/`get_account_type` or `validate_action_account_id`/`verify_and_charge_tx_ephemeral` in this pass to confirm or rule out a third gate that closes this gap specifically for `NearDeterministicAccount`. The asymmetry between the two functions shown above is confirmed by the code, but whether it is practically reachable (i.e., whether an attacker can actually front-run a specific deterministic id with a plain `CreateAccount`) depends on those unverified details.

### Recommendation
Extend `check_account_existence`'s `CreateAccount` guard (and/or `action_create_account`) to also reject `CreateAccount` against non-existent `NearDeterministicAccount` ids, mirroring the `account_is_implicit` treatment, unless it can be confirmed that the account id's structural constraints (top-level length / required predecessor) already make it impossible for an arbitrary unprivileged account to win that race.

### Proof of Concept
Could not produce a concrete, verified reproduction because I was unable to confirm the exact `AccountType::NearDeterministicAccount` id format and could not verify `account_is_implicit`, `get_account_type`, `validate_action_account_id`, or `verify_and_charge_tx_ephemeral` within the available tool budget, so the necessary preconditions for a reproducible unit/integration test could not be established with certainty in this pass.

### Citations

**File:** runtime/runtime/src/actions.rs (L167-210)
```rust
pub(crate) fn action_create_account(
    fee_config: &RuntimeFeesConfig,
    account_creation_config: &AccountCreationConfig,
    account: &mut Option<Account>,
    actor_id: &mut AccountId,
    account_id: &AccountId,
    predecessor_id: &AccountId,
    result: &mut ActionResult,
) {
    if account_id.is_top_level() {
        if account_id.len() < account_creation_config.min_allowed_top_level_account_length as usize
            && predecessor_id != &account_creation_config.registrar_account_id
        {
            // A short top-level account ID can only be created registrar account.
            result.result = Err(ActionErrorKind::CreateAccountOnlyByRegistrar {
                account_id: account_id.clone(),
                registrar_account_id: account_creation_config.registrar_account_id.clone(),
                predecessor_id: predecessor_id.clone(),
            }
            .into());
            return;
        } else {
            // OK: Valid top-level Account ID
        }
    } else if !account_id.is_sub_account_of(predecessor_id) {
        // The sub-account can only be created by its root account. E.g. `alice.near` only by `near`
        result.result = Err(ActionErrorKind::CreateAccountNotAllowed {
            account_id: account_id.clone(),
            predecessor_id: predecessor_id.clone(),
        }
        .into());
        return;
    } else {
        // OK: Valid sub-account ID by proper predecessor.
    }

    *actor_id = account_id.clone();
    *account = Some(Account::new(
        Balance::ZERO,
        Balance::ZERO,
        AccountContract::None,
        fee_config.storage_usage_config.num_bytes_account,
    ));
}
```

**File:** runtime/runtime/src/actions.rs (L794-817)
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
```

**File:** runtime/runtime/src/actions.rs (L828-833)
```rust
        Action::DeterministicStateInit(_) => {
            // Existing and non existing is valid for DeterministicStateInit.
            // Does not exist => The account will be created by the action.
            // Does exist => Nothing happens but the receipt is not aborted to
            // allow optional init before other actions.
        }
```
