### Title
`replace`/`clean` in `state-manipulation` contract have no caller restriction, allowing any account to overwrite or erase live contract state during a migration window - (File: `state-manipulation/src/lib.rs`)

### Summary
The `replace` and `clean` `#[no_mangle]` exports write/remove arbitrary storage keys taken directly from the caller-supplied JSON input, with no check on `env::predecessor_account_id()`, owner, or any access key at all. Once this WASM is deployed (temporarily, by design) over a live contract's account to perform a migration, any unprivileged account can call `replace` or `clean` in the same block as the operator's legitimate migration call and have their own key/value pairs be the ones that persist, directly rewriting the account's on-chain state.

### Finding Description
The binding that should hold is: `state_after_migration_block == operator_intended_state`, i.e., only the deploying operator's `replace`/`clean` calls determine the resulting storage of the account. Tracing the code: [1](#0-0)  shows `replace()` reads raw `input()`, deserializes a list of base64 `(key, value)` pairs, and calls `storage_write` for each pair with zero authorization check. Likewise [2](#0-1)  shows `clean()` deserializes a list of base64 keys and calls `storage_remove` for each, again with no check of who the caller is.

Nowhere in the file is `env::predecessor_account_id()`, an owner field, or any access-key/method-restriction logic referenced — the entire module (`storage_write`, `storage_remove`, `input`, `replace`, `clean`) contains no authorization primitive at all [3](#0-2) . As the README documents, this contract is explicitly meant to be "deploy[ed]... into the account that already has another contract deployed to it" to perform a one-off migration [4](#0-3) . Because NEAR's function-call model lets *any* account submit a `FunctionCall` action to *any other* account's deployed contract (calling doesn't require the caller to hold a key on the target account), once this code is live on an account, `replace`/`clean` are open, unauthenticated write primitives over that account's full key-value storage. An attacker who observes the migration transaction in the mempool (or simply races it) can submit their own `replace`/`clean` call in the same block; whichever transaction is applied last in that block determines the final storage state, so the attacker's arbitrary key/value pairs can silently overwrite the operator's intended migrated state (e.g., rewriting `STATE` fields, balances, or account-owner records used by whatever contract governs the account, including lockup/staking-pool accounting fields once the follow-up "real" contract is deployed).

No existing guard (`assert_owner`, `assert_self`, `is_promise_success`, predecessor checks, etc.) exists anywhere in this crate to stop this — the file simply has none of these assertions.

### Impact Explanation
Whoever wins the block-ordering race controls the final bytes written to the account's storage trie during the migration window. If the account under migration is a lockup, staking pool, or any contract whose critical accounting (`owner_account_id`, `lockup_information`, `staked_balance`, vesting schedule, etc.) is represented as raw state key/values, an attacker can inject arbitrary values — e.g., set themselves as owner, zero out a debt, or fabricate a balance — which on subsequent contract redeployment/resumption directly translates into unauthorized fund control or theft. This matches the Critical category: "an account whitelisted or a lockup deployed with parameters its rightful creator never chose" / direct manipulation enabling theft of the account's funds, since the resulting state is attacker-authored rather than operator-authored.

### Likelihood Explanation
The precondition is that the state-manipulation WASM is currently deployed to a live account undergoing migration — which is exactly the documented, intended use of this contract per its README. Given that, the attack requires no special privilege: any NEAR account can submit a `FunctionCall` to `replace`/`clean` on the target account paying only ordinary gas costs, and simply needs to land in the same block (or before finalization of the operator's legitimate call) to have their write take effect. This is trivially repeatable against any account running this build during its migration window.

### Recommendation
Add caller authorization to both `replace` and `clean` — e.g., require `near_sys::predecessor_account_id()` to equal a hardcoded/initialized `owner_account_id`, or require the call be signed by a specific known key/account before allowing any `storage_write`/`storage_remove`. At minimum, restrict these exported functions to only be callable by the account itself (self-calls) or a designated migration operator account, and ideally have the migration process complete and redeploy the target contract atomically (e.g., via a single batched transaction with `DeployContract` + initialization) rather than leaving an open window with unauthenticated storage-write methods live on-chain.

### Proof of Concept
```rust
// cargo test in state-manipulation, using near-workspaces
#[tokio::test]
async fn unauthorized_replace_wins_race() -> anyhow::Result<()> {
    let worker = workspaces::sandbox().await?;
    let contract = worker.dev_deploy(&fs::read("res/state_manipulation.wasm").await?).await?;
    let attacker = worker.dev_create_account().await?;

    // Operator's legitimate migration write
    let operator_entries = serde_json::json!({ "entries": [[base64::encode(b"STATE"), base64::encode(b"operator_value")]] });
    // Attacker's competing write submitted in the same block
    let attacker_entries = serde_json::json!({ "entries": [[base64::encode(b"STATE"), base64::encode(b"attacker_value")]] });

    let op_tx = contract.call(&worker, "replace").args_json(&operator_entries)?.max_gas().transact();
    let atk_tx = attacker
        .call(&worker, contract.id(), "replace")
        .args_json(&attacker_entries)?
        .max_gas()
        .transact();

    // Fire both, no ordering guarantee enforced by the contract
    let _ = tokio::join!(op_tx, atk_tx);

    let state_items = contract.view_state(&worker, None).await?;
    let final_value = state_items.get(&b"STATE".to_vec()).unwrap();

    // Binding under test: final_value should equal operator's intended value.
    // Because no predecessor/owner check exists, this assertion can fail —
    // the attacker's call, with no authorization at all, is able to determine
    // the final state depending on block/tx ordering.
    assert_eq!(final_value, b"operator_value"); // demonstrably not guaranteed
    Ok(())
}
```

### Citations

**File:** state-manipulation/src/lib.rs (L1-61)
```rust
#![cfg_attr(target_arch = "wasm32", no_std)]
#![cfg_attr(target_arch = "wasm32", feature(alloc_error_handler))]

#[macro_use]
extern crate alloc;

use alloc::vec::Vec;

const ATOMIC_OP_REGISTER: u64 = 0;
const EVICTED_REGISTER: u64 = 8;

#[cfg(all(not(feature = "clean"), not(feature = "replace")))]
core::compile_error!("one of the `clean` or `replace` features must be set");

// Set up global allocator by default if in wasm32 architecture.
#[cfg(target_arch = "wasm32")]
#[global_allocator]
static ALLOC: wee_alloc::WeeAlloc = wee_alloc::WeeAlloc::INIT;

#[cfg(target_arch = "wasm32")]
#[alloc_error_handler]
fn oom(_: core::alloc::Layout) -> ! {
    core::arch::wasm32::unreachable()
}

// Update panic handler in wasm32 environments
#[cfg(all(target_arch = "wasm32", not(feature = "std")))]
#[panic_handler]
#[allow(unused_variables)]
fn panic(info: &core::panic::PanicInfo) -> ! {
    core::arch::wasm32::unreachable()
}

fn register_len(register_id: u64) -> Option<u64> {
    let len = unsafe { near_sys::register_len(register_id) };
    if len == core::u64::MAX {
        None
    } else {
        Some(len)
    }
}

#[cfg(feature = "replace")]
/// Writes key-value into storage.
fn storage_write(key: &[u8], value: &[u8]) {
    unsafe {
        near_sys::storage_write(
            key.len() as _,
            key.as_ptr() as _,
            value.len() as _,
            value.as_ptr() as _,
            EVICTED_REGISTER,
        )
    };
}

#[cfg(feature = "clean")]
/// Removes storage at given key.
fn storage_remove(key: &[u8]) {
    unsafe { near_sys::storage_remove(key.len() as _, key.as_ptr() as _, EVICTED_REGISTER) };
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

**File:** state-manipulation/README.md (L1-6)
```markdown
# State Manipulation contract

This contract has been designed to put key value pairs into storage with `replace` and clear key/value pairs with `clean` from an account's storage.

Deploy this contract into the account that already has another contract deployed to it.
This contract on call `clean` will remove any items of the state specified (keys should be specified in base64). When compiled with `replace` feature, `replace` method can be called with an array of key/value tuple pairs to insert into state.
```
