### Title
Deterministic account ID squatting via ordinary `CreateAccount` — `account_is_implicit` does not cover `AccountType::NearDeterministic` - (File: runtime/runtime/src/actions.rs)

### Summary
`account_is_implicit()` in `core/primitives/src/utils.rs` only special-cases `AccountType::NearImplicitAccount` (and `EthImplicitAccount` when enabled), never `AccountType::NearDeterministic`. As a result, `check_account_existence()`'s `Action::CreateAccount` branch does not reject `CreateAccount` for an account id shaped like a NEP-616 deterministic account id (`"0s" + 40 hex chars`), letting an ordinary user pre-create and install an access key at that id before the legitimate `DeterministicStateInitAction` targeting the same id ever arrives.

### Finding Description
`account_is_implicit` is defined as: [1](#0-0) 

It only tests `is_implicit()` / `AccountType::NearImplicitAccount`, and never checks for `AccountType::NearDeterministic` (the type produced for ids matching `derive_near_deterministic_account_id`, i.e. `"0s" + hex`, at: [2](#0-1) 

`check_account_existence()`'s `Action::CreateAccount` branch uses exactly this helper to decide whether to reject the `CreateAccount` action with `OnlyImplicitAccountCreationAllowed`: [3](#0-2) 

Because `account_is_implicit` returns `false` for a `NearDeterministic`-typed id, the `else` branch is taken and the `CreateAccount` action proceeds as an ordinary account creation — the attacker can combine `CreateAccount` + `AddKey(attacker_pubkey)` + `Transfer` in the same top-level-account-creation transaction (the "single action only" restriction that protects implicit accounts is never triggered for this id shape).

Later, when the legitimate party submits a `DeterministicStateInitAction` whose `derive_near_deterministic_account_id(state_init)` equals that same id, `check_account_existence()`'s `DeterministicStateInit` branch explicitly treats "account already exists" as a benign no-op rather than an error: [4](#0-3) 

So the receipt does not abort with `AccountAlreadyExists`; instead the intended code/access-key installation is silently skipped, leaving the attacker's pre-planted access key in permanent control of the account, while the legitimate initializer's action appears to succeed with no error signal.

### Impact Explanation
This falls under "Contracts execution flows / Unauthorized transaction": an unprivileged attacker can squat a NEP-616 deterministic account id ahead of its legitimate owner, installing their own access key there. Any future `DeterministicStateInitAction` that would derive to the same id is silently neutralized (no code/keys installed, no error raised), permanently freezing/hijacking that deterministic account slot instead of the intended state_init owner ever gaining control. This is an authorization-exactness violation: a deterministic account's state should only ever be installed by the party whose `state_init` hashes to that id.

### Likelihood Explanation
The attacker only needs to be able to fund a top-level account creation transaction with `receiver_id` set to the 42-character `"0s"+40hex` string (which, being longer than the historical top-level minimum-length threshold, is not gated by the registrar/system-account restriction that blocks short top-level names) and include `CreateAccount + AddKey + Transfer` actions. This is entirely within the described unprivileged attacker capability (ordinary funded account, signs/submits its own transactions). The practical constraint is that the attacker must know or predict the target deterministic id in advance (e.g., because the `state_init` content — code hash, fixed init keys, well-known factory template — is public/deterministic ahead of time), which is plausible for standardized/templated deterministic-account use cases (factories, canonical multisig templates, etc.). I was not able to fully verify the exact top-level-account-length gate in this checkout (that code path was not located during this review), so this is noted as an area of residual uncertainty affecting exact feasibility, though the core defect (`account_is_implicit` omitting `NearDeterministic`) is confirmed directly in the cited code.

### Recommendation
Extend `account_is_implicit` (or add a parallel check specifically in `check_account_existence`'s `CreateAccount` branch) to also treat `AccountType::NearDeterministic` ids as reserved/non-creatable via ordinary `CreateAccount`, rejecting such actions the same way implicit accounts are rejected (`OnlyImplicitAccountCreationAllowed` or a new dedicated error), so that deterministic accounts can only ever be materialized via a matching `DeterministicStateInitAction`. Additionally, consider making the `DeterministicStateInit` existing-account no-op path distinguish "account created by a matching state-init" from "account pre-existing via unrelated action", e.g., by erroring with `AccountAlreadyExists`/`InvalidDeterministicStateInitReceiver` when the existing account was not itself created by a `DeterministicStateInit` for the same derived id.

### Proof of Concept
1. Unit test in `core/primitives/src/utils.rs` (or a new test module): construct `account_id = "0s".to_string() + &"a".repeat(40)` (40 lowercase hex chars), parse to `AccountId`, assert `account_id.get_account_type() == AccountType::NearDeterministic`, and assert `account_is_implicit(&account_id, false) == false` and `account_is_implicit(&account_id, true) == false`.
2. Unit test on `check_account_existence` in `runtime/runtime/src/actions.rs`: call it with `Action::CreateAccount(_)`, `account = None`, the above `account_id`, and default `RuntimeConfig`; assert it returns `Ok(())` (i.e., not rejected via `OnlyImplicitAccountCreationAllowed`), confirming the account-squatting path is open.
3. Integration/test-loop test (e.g. alongside `test-loop-tests/src/tests/deterministic_account_id.rs`): (a) submit an ordinary top-level `CreateAccount` + `AddKey` transaction targeting the deterministic-shaped id; (b) submit a `DeterministicStateInitAction` whose `state_init` derives to that same id; assert that the second transaction's receipt succeeds silently without installing the expected code/keys (verify via account/contract state inspection that the intended code was not deployed), demonstrating the deterministic account was hijacked/frozen rather than properly initialized.

### Citations

**File:** core/primitives/src/utils.rs (L471-477)
```rust
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

**File:** runtime/runtime/src/actions.rs (L828-833)
```rust
        Action::DeterministicStateInit(_) => {
            // Existing and non existing is valid for DeterministicStateInit.
            // Does not exist => The account will be created by the action.
            // Does exist => Nothing happens but the receipt is not aborted to
            // allow optional init before other actions.
        }
```
