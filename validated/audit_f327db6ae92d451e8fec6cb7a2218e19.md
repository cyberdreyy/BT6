### Title
Unauthenticated arbitrary storage read/write in `state-manipulation` contract enables custody-state tampering - (File: `state-manipulation/src/lib.rs`)

### Summary
The `state-manipulation` contract exposes two raw WASM entry points, `replace` and `clean`, that write or delete arbitrary key/value pairs directly in the deployed account's contract storage. Neither function verifies the caller's identity (no `predecessor_account_id`/owner check anywhere in the module), so once this contract is deployed on an account, *any* unprivileged caller can rewrite or erase that account's persisted state — including the `STATE` blob that a staking-pool, lockup, or multisig contract on the same account depends on for ownership, balances, and vesting schedules.

### Finding Description
`replace()` deserializes a caller-supplied `entries: Vec<(&str, &str)>` and calls `storage_write` on each pair without any authorization check: [1](#0-0) 

`clean()` deserializes a caller-supplied `keys: Vec<&str>` and calls `storage_remove` on each, again with no authorization check: [2](#0-1) 

There is no `#[near_bindgen]`/access-control wrapper and no assertion such as `env::predecessor_account_id() == env::current_account_id()` anywhere in the file — the entire contract is built from raw `#[no_mangle]` exports using `near_sys` directly, bypassing the SDK's usual method-level guards.

This is the same bug class as the reported `just-extend` prototype-pollution issue: an unconstrained "write into arbitrary key path" primitive that lets any caller mutate internal state that other code implicitly trusts, breaking the binding between *what the application's own state-transition logic wrote* and *what is actually stored* under keys like the borsh-serialized `STATE` register used by `staking-pool`, `lockup`, or `multisig`.

### Impact Explanation
Per the README, this contract is meant to be "deployed into the account that already has another contract deployed to it," to patch broken state: [3](#0-2) 

While it is deployed (which is documented, intended usage, not a hypothetical redeploy by the attacker), the account's storage — e.g., a `staking-pool`'s `owner_id`/`total_staked_balance`/`total_stake_shares`, or a `lockup`'s vesting schedule, or a `multisig`'s `num_confirmations`/`confirmations` — is writable and deletable by any unprivileged party, not just the account owner who deployed it for maintenance. An attacker can overwrite the `STATE` key to change `owner_id` to their own account (claims moved to a party not entitled to them), fabricate `stake_shares`/`total_staked_balance` values (claims exceeding assets actually held), or delete/clear a lockup's vesting-schedule key to disrupt the releasable-amount computation (funds frozen or early release depending on how the corrupted state is later deserialized). This is a Critical-severity custody-binding break: value recorded in storage can diverge arbitrarily from value actually owed/held, and control (ownership) itself can be seized.

### Likelihood Explanation
Exploitation requires only that the `state-manipulation` contract be deployed and live on the target account (its documented, intended use), after which no signature, ownership, or membership check gates `replace`/`clean` — any account can call these methods. Given the contract's explicit purpose is to be attached temporarily to already-deployed, value-bearing accounts (staking pools, lockups, multisigs) for state fixes, the exposure window is exactly when it is most dangerous.

### Recommendation
Add an explicit authorization check at the top of both `replace()` and `clean()`, e.g. asserting `predecessor_account_id() == current_account_id()` (only the account itself, via a full-access key held by the true owner, may invoke it) before performing any `storage_write`/`storage_remove`, mirroring the caller checks already used elsewhere in this codebase (e.g. `multisig/src/lib.rs`'s `add_request` predecessor check).

### Proof of Concept
1. Owner deploys `state_manipulation.wasm` (with `replace`/`clean` features) onto an account that also holds a `staking-pool` or `lockup` contract's persisted `STATE`.
2. Any other account (not the owner, no special key) calls:
   ```
   near-cli execute change-method network testnet contract <target> call replace \
     '{"entries":[["U1RBVEU=", "<base64 of attacker-crafted STATE borsh bytes with owner_id = attacker>"]]}' \
     --prepaid-gas '100 TeraGas' --attached-deposit '0 NEAR' signer <attacker> sign-with-keychain
   ```
3. Because `replace()` performs `storage_write` unconditionally (`state-manipulation/src/lib.rs:75-92`), the target account's `STATE` is now attacker-controlled, e.g. `owner_id` becomes the attacker's account.
4. Subsequent calls into the original (still-deployed alongside, or redeployed by owner) staking-pool/lockup contract read this tampered `STATE`, trusting the attacker as owner/beneficiary.

### Citations

**File:** state-manipulation/src/lib.rs (L75-92)
```rust
#[cfg(feature = "replace")]
#[no_mangle]
pub fn replace() {
    #[derive(serde::Deserialize)]
    struct ReplaceInput<'a> {
        #[serde(borrow)]
        entries: Vec<(&'a str, &'a str)>,
    }

    let input = input().unwrap();
    let args: ReplaceInput = serde_json::from_slice(&input).unwrap();
    for (key, value) in args.entries {
        storage_write(
            &base64::decode(key).unwrap(),
            &base64::decode(value).unwrap(),
        );
    }
}
```

**File:** state-manipulation/src/lib.rs (L94-108)
```rust
#[cfg(feature = "clean")]
#[no_mangle]
pub fn clean() {
    #[derive(serde::Deserialize)]
    struct CleanInput<'a> {
        #[serde(borrow)]
        keys: Vec<&'a str>,
    }

    let input = input().unwrap();
    let args: CleanInput = serde_json::from_slice(&input).unwrap();
    for key in args.keys {
        storage_remove(&base64::decode(key).unwrap());
    }
}
```

**File:** state-manipulation/README.md (L1-19)
```markdown
# State Manipulation contract

This contract has been designed to put key value pairs into storage with `replace` and clear key/value pairs with `clean` from an account's storage.

Deploy this contract into the account that already has another contract deployed to it.
This contract on call `clean` will remove any items of the state specified (keys should be specified in base64). When compiled with `replace` feature, `replace` method can be called with an array of key/value tuple pairs to insert into state.

## Parameters

JSON format for `clean`:

```json
{"keys":["<base64 encoded key byte string>", "...", "..."]}
```

JSON format for `replace`:
```json
{"entries":[["<base64 key byte string>", "<base64 value byte string>"], ["...", "..."]]}
```
```
