### Title
`replace`/`clean` state-manipulation contract exports allow storage write with no caller authorization - (File: `state-manipulation/src/lib.rs`)

### Summary
The `state-manipulation` contract's exported `replace` and `clean` functions parse caller-supplied JSON containing base64 keys/values and call `storage_write`/`storage_remove` directly, with no check on `predecessor_account_id`, no owner field, and no access-key restriction of any kind. Once this contract is deployed on any account (its documented purpose per `state-manipulation/README.md` is to be deployed onto an account that already has another contract, to patch its state), any unprivileged caller — not just the account owner performing the intended maintenance — can call `replace`/`clean` to overwrite or erase arbitrary storage entries on that account.

### Finding Description
The invariant claimed is: `storage_write(key, value)` is only ever invoked when `predecessor_account_id == account_owner`. Tracing the code shows this binding does not hold.

`replace` reads raw input, decodes the JSON `entries` array, and for every `(key, value)` pair calls `storage_write` unconditionally: [1](#0-0) 

`clean` behaves identically for `storage_remove`: [2](#0-1) 

Neither function reads `predecessor_account_id`, compares it to any stored owner, or performs `assert_one_yocto`/`assert_self()` style gating — no such call exists anywhere in the file. The `input()` helper simply copies the raw args register with no signer validation: [3](#0-2) 

Exploit flow: once this contract is deployed to an account (per its own README, intended for an account that "already has another contract deployed to it," for state migration/patching purposes), any unrelated account can call `replace` with attacker-chosen key/value pairs, then immediately call it again with different values, fully overwriting whatever the legitimate owner intended to write — winning the race or simply hijacking the account's storage layout (e.g., balances, owner fields, staking/lockup accounting) if the underlying contract's storage schema is known.

### Impact Explanation
Any state variable stored under a raw storage key on the account hosting this contract — including balance-like fields such as `last_total_balance`, `total_staked_balance`, `total_stake_shares`, `deposit_amount`, `lockup_information`, `vesting_information`, or `ft.total_supply` if this tool is layered atop such a contract — can be directly overwritten by an unauthorized party via `storage_write`, or erased via `storage_remove`. This is a direct, unauthorized mutation of contract state that can enable theft of held NEAR/wNEAR, forged unlocked balances, or forged owner/whitelist fields, matching the Critical impact category (funds moved/released without entitlement).

### Likelihood Explanation
The precondition is that this contract must first be deployed onto the target account. That deployment step is performed by the legitimate account holder as part of an intended maintenance/migration workflow (per the README), not by the attacker — the attacker does not need a full-access key or deploy rights on the victim account. Once deployed, the exported functions are callable by literally any account with no authorization gate, so exploitation cost is a single low-gas transaction, repeatable indefinitely across `replace`/`clean` calls and across any account that has this contract deployed, until the legitimate owner deploys a fixed contract or removes it.

### Recommendation
Add a `predecessor_account_id` check comparing against a fixed authorized principal (e.g., an owner stored at deploy/init time or hardcoded at build time) at the top of both `replace` and `clean`, panicking otherwise. Alternatively, remove the general-purpose, unauthenticated storage-write primitive design entirely and require the caller to be `current_account_id` only via a signed batch transaction executed by the same full-access key that deployed the contract, never as a separately callable public method with no check.

### Proof of Concept
`cargo test` plan (extending the existing `near-workspaces` harness in the same file):
1. `worker.dev_deploy(&wasm)` the `state_manipulation.wasm` contract to account `victim`.
2. From a second, unrelated `worker.dev_create_account()` account `attacker` (not the deployer/signer), call `victim.call(&worker, "replace").args_json(json!({"entries": [[base64(key), base64(value_A)]]}))`.
3. Assert via `contract.view_state(worker, None)` that `key` now equals `value_A`, proving the write succeeded despite `attacker` having no relationship to `victim`.
4. Immediately call `replace` again from `attacker` with `value_B` for the same key; assert `view_state` now shows `value_B`, proving repeated arbitrary overwrite with no authorization check anywhere in `replace`/`clean` (`state-manipulation/src/lib.rs:75-108`).

### Citations

**File:** state-manipulation/src/lib.rs (L63-73)
```rust
fn input() -> Option<Vec<u8>> {
    unsafe { near_sys::input(ATOMIC_OP_REGISTER) };
    let len = register_len(ATOMIC_OP_REGISTER)?;

    let buffer = vec![0u8; len as usize];

    // Read data from register into buffer
    unsafe { near_sys::read_register(ATOMIC_OP_REGISTER, buffer.as_ptr() as _) };

    Some(buffer)
}
```

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
