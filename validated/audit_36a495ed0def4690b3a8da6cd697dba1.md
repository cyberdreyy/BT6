### Title
Unprivileged name squatting of `<name>.<multisig-factory>` via `MultisigFactory::create` - ([File: multisig-factory/src/lib.rs])

### Summary
`MultisigFactory::create` lets any caller deploy a multisig at an arbitrary `<name>.<factory>` sub-account with attacker-chosen `members` and `num_confirmations`, with no owner/permission check and no callback verifying the `new` initialization call succeeded. Because NEAR account names are first-come-first-served, an attacker can pre-create the sub-account an intended owner plans to use, and because the promise chain has no `#[private]` callback to detect a failed `new` and roll back/delete the account, a failed or attacker-controlled initialization can leave the sub-account permanently squatted or in an inconsistent state that downstream users/protocol still treat as the legitimate multisig.

### Finding Description
The invariant that should hold is: for a multisig deployed at `name.multisig-factory`, `deployed.members == owner_intended.members` for the party who is supposed to own `name`. `MultisigFactory::create` breaks this binding because it performs no reservation, no ownership check on `name`, and no verification that the chained `new` call actually succeeded: [1](#0-0) 

The function builds a single batched `Promise`: `create_account()` → `deploy_contract(CODE)` → `transfer(attached_deposit)` → `function_call("new", {members, num_confirmations})`, and returns that promise directly to the caller with no `.then(callback)`. There is no `#[private]` function anywhere in this file to inspect `is_promise_success()` and react (e.g., delete the newly created account) if `new` panics/fails.

Exploit flow:
1. Attacker observes/anticipates that a specific `name` will be used for a legitimate multisig deployment (e.g., a known org or user identifier).
2. Attacker calls `create(name, attacker_members, attacker_num_confirmations)` first. Since `create` has zero access control (`callable by ANY account`) and NEAR sub-account creation is first-come-first-served, `name.multisig-factory` now exists, deployed with the attacker's chosen member set.
3. When the intended owner later calls `create(name, real_members, real_num_confirmations)`, the `create_account()` action in their batched promise fails because the account already exists, causing their entire receipt/promise chain to fail; the legitimate owner never gets a multisig at `name`, while the attacker's version persists with attacker-controlled members at the address everyone (protocol UI, other users, wallets) will trust as "the multisig for `name`".
4. Separately/additionally, because there is no callback verifying `new`'s success, if the attacker (or even a legitimate creator) triggers a scenario where `create_account`/`deploy_contract`/`transfer` succeed but `new` panics, the sub-account is left created and funded but uninitialized/inconsistent, with no factory-side detection or cleanup — the factory has no way to reclaim or retry that name, and third parties can still send funds to that address believing it is a properly governed multisig.

No existing guard in this file (`assert_self()`, `is_promise_success()`, owner checks, or an existing-account check) intercepts any of this, because none of those guards are present in `create` at all.

### Impact Explanation
NEAR (and any wNEAR/funds later directed to `name.multisig-factory` by users, dApps, or the protocol trusting the naming convention) can be routed to a contract instance whose `members`/`num_confirmations` were chosen entirely by an unprivileged attacker rather than the intended owner. This matches the Critical category: "an account whitelisted or a lockup/multisig deployed with parameters its rightful creator never chose." The attack is repeatable against any name not yet claimed and costs the attacker only the gas/deposit for one `create` call; the blast radius is every future user who trusts `<name>.<factory>` as the canonical multisig for that name.

### Likelihood Explanation
No special privileges are required — this is exactly the unprivileged capability set granted in the rules ("call any open method ... create their own ... multisig through the public factories with chosen arguments"). The only precondition is that the attacker names the sub-account before the legitimate owner does, which is purely a timing/front-running race with no cost beyond a standard `create` transaction and deposit. This is highly feasible and repeatable across any number of names.

### Recommendation
Add owner/authorization gating to `create` (e.g., require the deposit/caller to match an allow-listed pattern, or require `predecessor_account_id` to be a prefix-consistent parent of `name`), and add a `#[private]` callback attached via `.then(...)` that checks `is_promise_success()` after the `new` call; on failure, delete the newly created account and refund the attached deposit so a failed initialization cannot leave a squatted, uninitialized sub-account. Additionally, consider requiring a commit-reveal or fee/stake mechanism on `name` reservation to reduce front-running incentives, since NEAR account names are inherently first-come-first-served and cannot be fully protected against squatting by any factory contract logic alone.

### Proof of Concept
Using `near-sdk-sim` / `near-workspaces`:
```rust
#[test]
fn test_name_squatting_before_owner() {
    // 1. Deploy multisig-factory contract to `factory` account.
    // 2. Attacker account calls:
    //    factory.create("victim_org", vec![MultisigMember::Account{account_id: "attacker".into()}], 1)
    //    with sufficient attached deposit and gas.
    // 3. Assert the sub-account "victim_org.factory" now exists and its multisig state
    //    (via a view call, e.g. get_request or similar) shows members == [attacker],
    //    i.e. deployed.members != owner_intended.members ([victim1, victim2, victim3]).
    // 4. Legitimate owner then calls:
    //    factory.create("victim_org", vec![victim1, victim2, victim3], 2)
    // 5. Assert this second call's outer promise/receipt fails
    //    (create_account fails because "victim_org.factory" already exists),
    //    proving the owner can never obtain the multisig at their intended name
    //    once squatted, and the on-chain state at "victim_org.factory" still equals
    //    the attacker-chosen member set from step 3 — violating the equality
    //    deployed.members == owner_intended.members.
}
```
This demonstrates the binding failure directly: the account at `name.factory` ends up governed by member sets the rightful name owner never chose, with no callback or access check in `multisig-factory/src/lib.rs::create` preventing it.

### Citations

**File:** multisig-factory/src/lib.rs (L28-49)
```rust
    #[payable]
    pub fn create(
        &mut self,
        name: AccountId,
        members: Vec<MultisigMember>,
        num_confirmations: u64,
    ) -> Promise {
        let account_id = format!("{}.{}", name, env::current_account_id());
        Promise::new(account_id)
            .create_account()
            .deploy_contract(CODE.to_vec())
            .transfer(env::attached_deposit())
            .function_call(
                b"new".to_vec(),
                json!({ "members": members, "num_confirmations": num_confirmations })
                    .to_string()
                    .as_bytes()
                    .to_vec(),
                0,
                env::prepaid_gas() - CREATE_CALL_GAS,
            )
    }
```
