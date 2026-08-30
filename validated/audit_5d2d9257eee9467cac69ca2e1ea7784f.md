### Title
Top-level `CreateAccount` bypasses deterministic-account keylessness invariant because `account_is_implicit` doesn't recognize the NEP-616 `0s`+40-hex pattern - (File: runtime/runtime/src/actions.rs, core/primitives/src/utils.rs)

### Summary
`check_account_existence`'s `Action::CreateAccount` branch only refuses creation when `account_is_implicit` returns true, and that helper only classifies `AccountType::NearImplicitAccount` (64-hex, no prefix) and `AccountType::EthImplicitAccount` (`0x`+40-hex) as implicit. The NEP-616 deterministic account ID format produced by `derive_near_deterministic_account_id` is `0s`+40-hex, a distinct string shape that is not covered by either branch, so it is treated as an ordinary `NamedAccount` and top-level `CreateAccount` is permitted on it.

### Finding Description
`derive_near_deterministic_account_id` builds ids as `format!("0s{}", hex::encode(&hash[12..32]))`, i.e. a 42-character string `0s` + 40 lowercase hex chars: [1](#0-0) .

`account_is_implicit` decides whether `CreateAccount` must be rejected, but it only tests for `AccountType::NearImplicitAccount` or (when eth-implicit accounts are enabled) `AccountType::EthImplicitAccount`: [2](#0-1) .

`check_account_existence`'s `Action::CreateAccount` arm calls exactly this helper and only errors out (`OnlyImplicitAccountCreationAllowed`) if it returns true; otherwise the create is allowed to proceed as a normal named-account creation: [3](#0-2) .

Since the `0s`+40-hex format is neither the 64-hex `NearImplicitAccount` shape nor the `0x`+40-hex `EthImplicitAccount` shape, an attacker-chosen id of this shape is classified as an ordinary `NamedAccount`, and `CreateAccount` combined with `AddKey(attacker_pubkey, FullAccess)` in the same transaction succeeds (subject only to the normal top-level account length/registrar rule, which a 42-character name clears without needing the registrar). Later, when the legitimate deterministic-account flow submits `DeterministicStateInitAction{state_init}` to the same id, `validate_deterministic_state_init` accepts it because `derive_near_deterministic_account_id(&state_init) == receiver_id`, and `action_deterministic_state_init` observes `account.contract().is_none()` and deploys the contract via `deploy_deterministic_account` — but this path never touches or removes access keys. The attacker's `FullAccess` key survives, breaking the NEP-616 invariant that deterministic accounts have no signing key and are governed solely by their deployed code.

### Impact Explanation
This is an authorization-escalation / unauthorized-transaction bug: an attacker retains a `FullAccess` key over an account that any relayer, protocol, or user believes is a keyless, contract-only deterministic account. Any funds later transferred to that account can be drained by the attacker via a `Transfer` action signed with the surviving key, constituting theft of user/protocol funds — matching the "Unauthorized transaction"/"theft of funds" bounty category for Contracts execution flows.

### Likelihood Explanation
The attacker needs no privileged access: they compute `id = derive_near_deterministic_account_id(&state_init)` for a `state_init` of their own choosing (arbitrary code/data pointer), then submit an ordinary `CreateAccount`+`AddKey` transaction to `id` before anyone funds/initializes it. This requires no preimage search (the attacker picks the `state_init` freely and derives the resulting id ex-post) and no elevated permissions — only that the target `id` string clears the top-level account creation length rule, which the fixed 42-character format does unconditionally. The attack is fully repeatable for any `id` the attacker wants to preempt.

### Recommendation
Extend `account_is_implicit` (or add a dedicated classification check used by `check_account_existence`) to also recognize the `0s`+40-hex deterministic-account pattern and reject top-level `CreateAccount` targeting such ids, mirroring the existing protection for `NearImplicitAccount`/`EthImplicitAccount`. Alternatively, have `action_create_account`/`check_account_existence` explicitly parse and reject any account id matching the deterministic-account format before allowing ordinary named-account creation actions on it.

### Proof of Concept
Runtime/apply integration test:
1. Construct a `DeterministicAccountStateInit` and compute `id = derive_near_deterministic_account_id(&state_init)`.
2. Submit a transaction with `Action::CreateAccount` + `Action::AddKey(attacker_pubkey, FullAccess)` targeting `id`; assert it succeeds (no `OnlyImplicitAccountCreationAllowed` error).
3. Submit `Action::DeterministicStateInit(state_init)` to `id`; assert it succeeds via `deploy_deterministic_account` since `account.contract().is_none()`.
4. Query `get_access_key(id, attacker_pubkey)`; assert the key still resolves as `FullAccess`.
5. Fund `id` via `Transfer` from a third party, then submit a `Transfer` out of `id` signed by `attacker_pubkey`; assert it succeeds, demonstrating fund theft.

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
