### Title
Unauthenticated `replace`/`clean` exports allow any caller to overwrite migration state - (File: `state-manipulation/src/lib.rs`)

### Summary
The `state-manipulation` contract's `replace` and `clean` `#[no_mangle]` exports perform raw `storage_write`/`storage_remove` calls on attacker-supplied base64 keys/values with **no predecessor, owner, or self-call check whatsoever**. While a victim account's operator must hold a full-access key to *deploy* this WASM onto a target account (e.g., a staking pool) as part of a migration, once deployed, the exported `replace`/`clean` methods are callable by **any** account with no restriction, so any unprivileged party can race the operator's own migration call in the same block and have their write win.

### Finding Description
The invariant being violated: `final_storage_state == operator_intended_migration_state`.

Looking at `replace`:
```rust
#[cfg(feature = "replace")]
#[no_mangle]
pub fn replace() {
    ...
    for (key, value) in args.entries {
        storage_write(&base64::decode(key).unwrap(), &base64::decode(value).unwrap());
    }
}
``` [1](#0-0) 

and `clean`: [2](#0-1) 

Neither function calls `env::predecessor_account_id()`, compares it against `env::current_account_id()` (self-call), nor checks against any stored owner. There is no `assert_self()`, `assert_owner`, or any equivalent guard anywhere in the file. Any account that can send a `FunctionCall` action to the account this contract is deployed on can invoke `replace` with arbitrary key/value pairs, or `clean` with arbitrary keys, and the write/removal will unconditionally succeed.

Per the contract's own `README.md`, the intended usage is: "Deploy this contract into the account that already has another contract deployed to it," then call `replace`/`clean` to directly manipulate that account's storage (e.g., a staking pool's account map, keyed by the `accounts` `UnorderedMap`/`LookupMap` prefix) during a manual migration, then redeploy the real contract. [3](#0-2) 

Because deployment of contract code requires a full-access key (out of the attacker's reach), the attacker cannot deploy this WASM themselves. However, once the operator has deployed it and begins issuing their own `replace` call(s) to perform the legitimate migration, that operation is a normal, unauthenticated `FunctionCall` transaction sitting in the same block/mempool window as any other transaction targeting the account. An unprivileged attacker who observes the deployed bytecode (e.g., via RPC/mempool monitoring) can submit their own `replace` transaction with a colliding storage key (such as a specific account's serialized `Account` struct inside the staking pool's `accounts` collection) targeting the same block. Since both calls hit `storage_write` with no ordering guard beyond block/transaction inclusion order, whichever transaction is included last for that key wins - and there is nothing in the code preventing the attacker's entry from being the one that persists.

None of the standard guards listed in the validation rubric (`assert_owner`, `assert_called_by_foundation`, `assert_self()`, `is_promise_success()`, `assert_one_yocto()`, etc.) are present in this file, so nothing stops this race.

### Impact Explanation
An attacker who wins this race can inject an arbitrary serialized `Account` entry (or overwrite any other state key, including `total_staked_balance`, `total_stake_shares`, etc., if the operator's migration touches those prefixes) into the target account's storage. If the target is a staking pool's `accounts` collection, the attacker could plant an account record crediting themselves with inflated `stake_shares`/`unstaked` balances, which - once the real staking-pool contract is redeployed - would let them withdraw NEAR that was never rightfully theirs. This is direct, permanent manipulation of on-chain contract state resulting in theft of funds from the account, matching the Critical severity bucket ("NEAR ... moved out of a pool ... by a party not entitled to it").

### Likelihood Explanation
The precondition is that an operator has deployed `state-manipulation.wasm` onto a live, funded account (e.g., a staking pool) for a migration and issues a `replace`/`clean` call. This is a real, documented operational procedure per the contract's own `README.md`. During that narrow window, any observer of the mempool/RPC can submit a competing `replace` call at negligible cost (one function call, minimal gas, no deposit required) targeting the same block. This requires no special privilege, key, or prior relationship with the account - fully consistent with the "unprivileged attacker" profile in scope. The only limiting factor is the attacker needing to notice the deployment happen in real time, which is a timing/observation challenge, not a privilege barrier.

### Recommendation
Add an explicit authorization check to both `replace` and `clean`, e.g., require `near_sys::predecessor_account_id() == near_sys::current_account_id()` (self-call only) or restrict to a hardcoded owner/predecessor recorded at deploy time, so that only the account holder who deployed the migration tool (via a signed transaction from a key they control) can invoke these exports. Alternatively, retire this contract in favor of a migration path that runs entirely as part of a single atomic `migrate()` call inside the final contract itself, removing the exposed intermediate window entirely.

### Proof of Concept
```rust
// near-workspaces test, sandbox
// 1. Deploy state_manipulation.wasm (built with `replace` feature) to account `victim.test.near`
//    to simulate an operator performing a migration.
// 2. In the SAME block, submit two `replace` calls targeting the same storage key
//    (e.g., a serialized staking-pool Account record under the `accounts` collection prefix):
//      - operator_call: entries = [(key, legit_serialized_account)]
//      - attacker_call: entries = [(key, attacker_forged_serialized_account_with_inflated_stake_shares)]
// 3. Await both transactions in the same block (or fire concurrently and await).
// 4. Call view_state and assert the stored value at `key` equals attacker's forged value,
//    not the operator's legit value -- proving the attacker's unauthenticated write
//    can override the operator's migration write with no owner/predecessor check.
assert_eq!(state_items.get(&key).unwrap(), &attacker_forged_value); // fails invariant: operator's write should have won or been protected
```
This directly exercises the missing-authorization root cause identified in `state-manipulation/src/lib.rs` `replace`/`clean`. [4](#0-3)

### Citations

**File:** state-manipulation/src/lib.rs (L75-108)
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

**File:** state-manipulation/README.md (L1-6)
```markdown
# State Manipulation contract

This contract has been designed to put key value pairs into storage with `replace` and clear key/value pairs with `clean` from an account's storage.

Deploy this contract into the account that already has another contract deployed to it.
This contract on call `clean` will remove any items of the state specified (keys should be specified in base64). When compiled with `replace` feature, `replace` method can be called with an array of key/value tuple pairs to insert into state.
```
