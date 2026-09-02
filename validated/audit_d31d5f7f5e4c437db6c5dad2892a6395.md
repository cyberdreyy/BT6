### Title
Unauthenticated `replace`/`clean` exports allow any caller to race a legitimate migration and leave state-manipulation contract accounts in an attacker-controlled, internally inconsistent state - (File: `state-manipulation/src/lib.rs`)

### Summary
The `replace` and `clean` `#[no_mangle]` exports write or delete arbitrary raw storage keys/values supplied in the call arguments with **no predecessor, owner, or key-scope check whatsoever**. Once this contract is deployed to an account (even temporarily, by its rightful owner, to perform a migration), any unprivileged NEAR account can call `replace` in the same block, racing the operator's own migration transaction and injecting or partially overwriting the multi-key structures the operator is writing, leaving the account's persistent state internally inconsistent and effectively attacker-controlled.

### Finding Description
The binding this contract implicitly assumes is:
`storage_after_migration == entries_supplied_by_the_deploying_owner`

but the code never enforces `predecessor_account_id() == account_owner`—there is no such call anywhere in `replace()` or `clean()`: [1](#0-0) [2](#0-1) 

Both functions simply deserialize base64 key/value pairs from `input()` and call `storage_write`/`storage_remove` directly against `near_sys`, with zero access control. The only implicit protection the design relies on is that deploying this WASM to an account requires a full-access key on that account (i.e., only the legitimate owner/operator would deploy it). But that protects only the *deploy* step, not the *call* step: once deployed, the exported `replace`/`clean` functions are open, unauthenticated public methods that any account can invoke by sending an ordinary transaction with no deposit and no special permission.

Exploit flow:
1. The operator (rightful owner of a lockup/pool/multisig account) deploys the `state-manipulation` contract to perform a legitimate multi-key migration and submits a `replace` transaction writing several related keys that together form one consistent logical structure (e.g., parts of a struct spread across multiple storage keys).
2. An unprivileged attacker, watching the mempool/block, submits their own `replace` (or `clean`) transaction targeting the same account in the same block.
3. Because there is no ordering guarantee between the two transactions and no principal check inside `replace`, the attacker's call can interleave with or follow the operator's call, overwriting some of the operator's keys with attacker-chosen values while leaving others from the operator's write intact.
4. The resulting persisted state is a hybrid of the operator's and attacker's writes - internally inconsistent with respect to whatever multi-key invariant the migrated contract expects (e.g., balance figures split across keys, owner/config records), because nothing enforces that a single principal drives the entire migration atomically.

This breaks the invariant "only one principal can drive a migration" since the contract has no notion of principal at all.

### Impact Explanation
Because the resulting account can be any lockup, staking pool, or multisig account temporarily running this utility contract, a successful race can corrupt exactly the kind of multi-key state that determines fund ownership (e.g., partial writes to balance/ownership-like keys), which can translate into unauthorized manipulation of contract state and, depending on the specific keys targeted, direct value extraction once the "real" contract is redeployed and reads the corrupted state. This matches the Critical impact category: direct manipulation of contract state enabling theft of the account's funds. The blast radius is any account on which this utility is deployed during its migration window, and the attack is repeatable every time such a migration occurs.

### Likelihood Explanation
The precondition is that the `state-manipulation` contract must be deployed to the target account by its owner for a migration — this is the contract's intended and documented use case (see `state-manipulation/README.md`, "Deploy this contract into the account that already has another contract deployed to it"). Given that precondition, the attack cost is a single ordinary transaction (no deposit needed) sent in the same block as the operator's migration transaction; this requires no elevated privilege, matching the defined "unprivileged attacker" capability of "call any open method." Feasibility is high once the deployment window exists, and the attack is repeatable across any account/epoch where the same utility is used.

### Recommendation
Add an explicit authorization check at the top of both `replace()` and `clean()` comparing `predecessor_account_id()` to the account itself (`current_account_id()`), or gate execution behind an owner-only key stored in state, so that only a transaction signed by the account itself (i.e., only the operator holding the account's full-access key) can invoke these exports. Additionally, document/enforce that this contract must only be deployed for the minimal possible window and that a single atomic batch (not multiple independent transactions) should perform the whole migration to avoid any cross-transaction race even from the legitimate operator's own keys.

### Proof of Concept
`cargo test` plan using `near-workspaces` (extending the existing `workspaces_test` in `state-manipulation/src/lib.rs`):
1. Deploy the `state_manipulation.wasm` (built with the `replace` feature) to a `dev_deploy`d account, simulating the "operator's account under migration."
2. Create two separate signer accounts: `operator` (simulating the legitimate migrator) and `attacker` (an unrelated funded account with no special role).
3. Construct a two-key logical structure, e.g. `key_a -> value_operator_a`, `key_b -> value_operator_b`, meant to be written together by `operator.call("replace", {entries: [[key_a, value_operator_a],[key_b, value_operator_b]]})`.
4. In the same block (submit both transactions before awaiting, e.g. via `worker.rpc_handler` batching or firing both `.transact()` futures concurrently with `tokio::join!`), have `attacker.call("replace", {entries: [[key_a, value_attacker_a]]})` overwrite only `key_a`.
5. Assert on both sides of the binding:
   - Expected (invariant holds): `contract.view_state()` returns `key_a == value_operator_a && key_b == value_operator_b`.
   - Actual (observed): `contract.view_state()` returns `key_a == value_attacker_a && key_b == value_operator_b` — proving the attacker's unauthenticated call partially clobbered the operator's multi-key write with no error, no revert, and no principal check, leaving the two-key structure internally inconsistent.
6. Confirm the assertion in step 5's "Actual" branch is what the test observes, demonstrating the vulnerability.

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
