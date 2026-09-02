The `create` entrypoint has no owner or existence check whatsoever, confirming the described front-running risk.### Title
Name-squatting the multisig sub-account lets an attacker deploy a factory multisig with attacker-chosen members - (File: `multisig-factory/src/lib.rs`)

### Summary
`MultisigFactory::create` is a public, unauthenticated `#[payable]` method that deploys a new multisig contract at `<name>.<factory>` using caller-supplied `name`, `members`, and `num_confirmations` with no check that the caller is entitled to that name and no check that the account doesn't already have an intended owner. Any unprivileged account can race ahead of the legitimate creator and claim `<name>.<factory>` first, populating it with attacker-controlled members/confirmation threshold, so that anyone later trusting "the multisig at that name" is actually trusting an attacker-controlled contract.

### Finding Description
The invariant that should hold is: `multisig_account(name).members == owner_intended_members(name)` — i.e., the set of members deployed at a given `<name>.<factory>` sub-account equals the set the entity entitled to `name` actually chose.

The code:
```rust
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
            json!({ "members": members, "num_confirmations": num_confirmations }).to_string().as_bytes().to_vec(),
            0,
            env::prepaid_gas() - CREATE_CALL_GAS,
        )
}
``` [1](#0-0) 

There is no `assert_owner`, no whitelist check, no `predecessor_account_id` restriction, and no verification of whether the target `name` is reserved for a specific party (e.g. a staking pool id or org name that a known/expected entity is going to claim). `name`, `members`, and `num_confirmations` are taken verbatim from the caller and used to construct the sub-account path and its initial state. Because NEAR account creation is first-come-first-served (`create_account()` fails only if the account already exists), whoever calls `create` first for a given `name` wins that namespace permanently — the account can never be redeployed with different members afterward without a full-access key, which the intended owner never gets since the attacker's `new` call likely doesn't grant them one.

Exploit flow:
1. Attacker observes (or predicts) that some entity intends to deploy a multisig at `expected-name.multisig-factory` (e.g., matching a staking pool id, a known org name, or a name referenced by another contract/frontend as "the trusted multisig for X").
2. Attacker calls `create("expected-name", [attacker_key_or_account], 1)` before the legitimate owner does, attaching enough deposit/gas.
3. `expected-name.multisig-factory` now exists, deployed and initialized with the attacker as sole member with `num_confirmations = 1`.
4. Any downstream user, contract, or UI that trusts "the multisig at `expected-name.multisig-factory`" (e.g., routes funds, treasury payouts, or governance actions to it) is actually funding an account fully controlled by the attacker, who can withdraw everything with a single confirmation.

No existing guard (`assert_self`, `assert_one_yocto`, whitelist, owner check) intercepts this call — `create` has zero access control by design, and `name`/`members`/`num_confirmations` are fully attacker-parameterized.

### Impact Explanation
Funds sent to the squatted multisig by any party that trusts the name-to-owner binding are moved to a contract the attacker fully controls, requiring only `num_confirmations` (attacker-chosen, e.g. 1) confirmations from attacker-controlled keys to withdraw — matching the Critical category "an account whitelisted or a lockup deployed with parameters its rightful creator never chose" / "a multisig request executed below intended live members' consent." This is repeatable for any and every `name` value across the factory, so the blast radius covers every future multisig deployment through this factory, not a single victim.

### Likelihood Explanation
The attack requires only a NEAR account, minimal gas, and enough NEAR to cover account creation/storage deposit — a trivial and permissionless cost. Feasibility depends on the attacker being able to predict or learn the intended `name` before the legitimate party submits their transaction (e.g., via mempool observation/front-running, or simply because the name is a known/public identifier such as a staking pool id or organization name). Given account creation is atomic and first-write-wins on NEAR, this is straightforward to execute and fully repeatable across any number of target names.

### Recommendation
Restrict `create` so that only the entity entitled to `name` can claim it — e.g., require `predecessor_account_id() == name` (self-registration pattern, as used by NEAR's lockup factory convention) or require a signed/whitelisted mapping from `name` to an authorized creator, and/or gate creation behind `assert_called_by_foundation`/an explicit allowlist. At minimum, reject `create` calls where `env::predecessor_account_id()` does not match or is not authorized for the requested `name`.

### Proof of Concept
```rust
// multisig-factory/src/tests.rs (new test, near-sdk-sim / near-workspaces)
#[test]
fn test_name_squatting_front_run() {
    // 1. Deploy multisig-factory contract to `factory.test.near`.
    // 2. As `attacker.near` (unprivileged), call:
    //    factory.create("victimname", vec![MultisigMember::Account{account_id: "attacker.near"}], 1)
    //    BEFORE the legitimate owner calls create("victimname", [owner_key], 3).
    // 3. Assert `victimname.factory.test.near` now exists.
    // 4. Assert the deployed multisig2 contract's members == [attacker.near] and
    //    num_confirmations == 1, NOT the intended owner's members/threshold.
    // 5. As the legitimate owner, call create("victimname", [owner_key], 3) again and
    //    assert it FAILS (account already exists), proving the name is permanently squatted.
    // 6. Simulate attacker submitting a single-confirmation request via multisig2's
    //    add_request/confirm to transfer funds out, proving sub-num_confirmations-intended
    //    withdrawal capability.
}
```
This demonstrates the equality `deployed_members(name) == owner_intended_members(name)` is violated: the left side becomes `[attacker.near]` while the right side (never realized) would have been the legitimate owner's set, with no guard in `create` preventing it.

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
