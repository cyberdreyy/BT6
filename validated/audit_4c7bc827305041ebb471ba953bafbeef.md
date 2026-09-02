### Title
Missing predecessor/owner check on `replace`/`clean` lets any account overwrite migration state - (File: `state-manipulation/src/lib.rs`)

### Summary
The `state-manipulation` contract's `replace` and `clean` exports write/delete arbitrary storage keys with no caller restriction whatsoever, and this contract is explicitly meant to be deployed onto an account that already holds another live contract (e.g. a staking pool) to perform a state migration. Because any account can call these methods, an unprivileged attacker can race the operator's own migration call within the same block and have their own `replace` call be the last write, corrupting a target data structure such as the staking pool's accounts map.

### Finding Description
The broken binding is: `final_state_at(account) == operator_intended_migration_state`. This should hold whenever the account's owner performs a migration via `state-manipulation`, but the contract provides no mechanism to enforce it.

`replace()` and `clean()` are plain `#[no_mangle]` exports that deserialize base64 key/value pairs from `input()` and call `storage_write`/`storage_remove` directly, with no `predecessor_account_id` check, no owner field, and no signature/whitelist of any kind: [1](#0-0) [2](#0-1) 

Per the project's own README, this contract is intended to be temporarily deployed "into the account that already has another contract deployed to it" so that an operator can insert/remove state during a migration: [3](#0-2) 

On NEAR, any public function exported by a deployed contract is callable by any account that can send a transaction — calling a method requires no special permission on the target account, only knowledge of its name and the ability to pay gas. Since `replace`/`clean` have no `assert_eq!(env::predecessor_account_id(), ...)` guard, once the operator deploys `state-manipulation.wasm` onto, say, a staking pool's account to fix up its `accounts` `LookupMap` prefix, any other account can also call `replace` on that same account in the same block, targeting the identical storage-key prefix (e.g. the staking pool's account-map key for the attacker's own `account_id`) or even the operator's own key, with attacker-chosen bytes. Because transaction ordering within a block is not guaranteed to favor the operator's transaction, the attacker's `storage_write` can be applied after the operator's, so the account ends up holding attacker-supplied bytes for a key such as an account record (`stake_shares`, `unstaked`, `unstaked_available_epoch_height`), completely displacing the legitimate migrated value. No `assert_self()`, owner check, or one-yocto guard exists anywhere in this crate to prevent it.

### Impact Explanation
Whoever writes last to the targeted collection-prefix key wins the migration. If the target key encodes a staking-pool account record, the attacker can fabricate arbitrary `stake_shares`/`unstaked` balances for an account they control, and once the real staking-pool contract code is redeployed on top of this manipulated state, the attacker can withdraw NEAR they never legitimately staked — direct theft of funds from the pool's escrow. This is repeatable for any account that has `state-manipulation` deployed during a migration window and matches the Critical category: "NEAR ... moved out of a pool ... by a party not entitled to it."

### Likelihood Explanation
The precondition is that the account owner has deployed `state-manipulation.wasm` (with the `replace`/`clean` features) onto a live contract account to perform a migration — this only happens during an operator-initiated upgrade, so the attack window is narrow but fully attacker-triggerable with a single transaction of near-zero cost (no deposit, minimal gas) submitted to land in the same block as the operator's `replace` call. No special privilege, key, or prior relationship with the victim account is required — any account that can observe the pending migration transaction (e.g. via the public mempool/RPC) can submit a competing `replace` call.

### Recommendation
Add a predecessor check to both `replace` and `clean` restricting the caller to `env::current_account_id()` (self-call only, invoked via a batched `FunctionCall` action in the same transaction as `DeployContract`) or to a single designated owner account, e.g. `assert_eq!(near_sys::predecessor_account_id(), near_sys::current_account_id())`. Better still, perform the deploy + `replace`/`clean` + redeploy-back sequence as a single atomic transaction (multiple actions in one `SignedTransaction`) rather than as separate transactions, so no other transaction can be interleaved in the same block.

### Proof of Concept
```rust
// cargo test -p state-manipulation --features "replace clean"
// using near-workspaces sandbox, extending the existing `workspaces_test`

#[tokio::test]
async fn attacker_races_operator_migration() -> anyhow::Result<()> {
    let worker = workspaces::sandbox().await?;
    let wasm = tokio::fs::read("res/state_manipulation.wasm").await?;
    let contract = worker.dev_deploy(&wasm).await?; // simulates account with migration tool deployed

    let attacker = worker.dev_create_account().await?; // unprivileged, unrelated account

    let key = base64::encode(b"accounts_prefix_victim_account");
    let operator_value = base64::encode(b"legit_stake_shares_state");
    let attacker_value = base64::encode(b"attacker_forged_stake_shares");

    // Operator's legitimate migration call and attacker's call targeting the SAME key,
    // submitted to land in the same block.
    let operator_call = contract
        .call(&worker, "replace")
        .args_json(&serde_json::json!({ "entries": [[key.clone(), operator_value]] }))?
        .max_gas();
    let attacker_call = attacker
        .call(&worker, contract.id(), "replace")
        .args_json(&serde_json::json!({ "entries": [[key.clone(), attacker_value.clone()]] }))?
        .max_gas();

    let (_r1, _r2) = tokio::join!(operator_call.transact(), attacker_call.transact());

    let state_items = contract.view_state(&worker, None).await?;
    let final_bytes = state_items.get(&base64::decode(&key)?).unwrap();

    // Binding violated: final state equals attacker's value, not the operator's.
    assert_eq!(final_bytes, &base64::decode(&attacker_value)?);
    Ok(())
}
```
This demonstrates that `final_state_at(account) != operator_intended_migration_state`, confirming an unprivileged attacker can overwrite the operator's migration write in the same block.

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
