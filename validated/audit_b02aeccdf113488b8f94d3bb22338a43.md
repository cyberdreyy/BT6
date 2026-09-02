### Title
Front-runnable, unchecked multisig account name in `MultisigFactory::create` lets an attacker seize the intended multisig identity - (File: `multisig-factory/src/lib.rs`)

### Summary
`MultisigFactory::create` derives the new multisig's account ID deterministically from a caller-supplied `name` (`format!("{}.{}", name, env::current_account_id())`) and immediately issues `create_account().deploy_contract(...).transfer(...).function_call("new", {members, num_confirmations})` with no check that `name` is not already taken and no reservation/allow-list mechanism. [1](#0-0)  Anyone who observes a pending `create` call for a given `name` (mempool/simulation) can front-run it with the same `name` but attacker-chosen `members`/`num_confirmations` (e.g. themselves as sole member, `num_confirmations: 1`). This mirrors the reported Morpho Blue bug class: absence of an "already created" guard before parameter-dependent deployment lets an attacker seize a market/account identity ahead of the legitimate creator.

### Finding Description
The account name is entirely attacker-influenceable and there is no `assert`/state check analogous to `market[id].lastUpdate == 0` used in the reported bug, nor the collision-handling present in the sibling factories:
- `staking-pool-factory` tracks created pool IDs in an `UnorderedSet` and asserts uniqueness before creating (`self.staking_pool_account_ids.insert(&staking_pool_account_id)` must return `true`), and refunds on failure via a callback. [2](#0-1) 
- `lockup-factory` at least attaches an `on_lockup_create` callback that detects promise failure (e.g. `create_account` failing because the account already exists) and refunds the caller's deposit. [3](#0-2) 

`multisig-factory::create` has neither: no existence check before dispatching the creation actions, and no `.then()` callback at all to detect/react to failure. [4](#0-3)  The README's documented usage pattern confirms `name` is a simple, predictable, user-chosen label (e.g. `"test"`), and the resulting account `test.<factory>` is expected to be controlled by the `members`/`num_confirmations` the intended deployer supplies. [5](#0-4) 

Binding broken (as an equality that should hold but doesn't):
`members/num_confirmations that control account "<name>.<factory>"` == `members/num_confirmations submitted by the party the ecosystem intended to own that name`

Because `create` performs no existence check, whichever transaction executes `create_account` on `"<name>.<factory>"` first wins that identity — independent of who the "rightful" name owner was intended to be. An attacker who simulates/observes the target `name` can submit their own `create` call first with malicious `members: [{"account_id": "attacker"}]` and `num_confirmations: 1`, permanently taking over that account name. Since `AccountId`s on NEAR cannot be redeployed/reused by a different creator once created, this claim is irreversible.

### Impact Explanation
This is a "wrongly parameterised deployment" — the account address that a foundation/dApp/user expects to be the trusted multisig for `name` (with the intended member set and confirmation threshold) is instead controlled by an unauthorized attacker with a low or single-signer threshold. Any NEAR subsequently sent to `"<name>.<factory>"` by parties trusting the documented naming convention (e.g. treasury funding, lockup `owner_account_id` pointing at this multisig, or other on-chain systems that reference the account by its predictable name) is deposited into an account fully controlled by the attacker, who can withdraw or misuse it unilaterally. This crosses the authorization/identity boundary called out in scope: "an account trusted as a pool or whitelist versus the code and arguments that trust was granted for."

### Likelihood Explanation
Likelihood is medium: the attacker only needs to observe a `create` transaction (mempool or predictable naming convention, e.g. an org publicly announcing it will deploy `treasury.multisig-factory.near`) and submit their own `create` call for the same `name` with higher priority/gas before the legitimate transaction is included. No privileged access, oracle manipulation, or complex setup is required — only a plain, unprivileged function call to the factory with an attacker-chosen `name`.

### Recommendation
Add an existence/reservation check before issuing the creation actions, mirroring the fix recommended for the Morpho analog and the patterns already used in `staking-pool-factory`/`lockup-factory`:
- Maintain a persistent set of already-created (or reserved) multisig names/account IDs and `assert!` that `name` is not already present before dispatching `create_account`.
- Optionally support a two-phase "reserve then confirm" flow, or require `name` reservation tied to `predecessor_account_id`, so only the intended creator can claim a given name.
- Add an `on_create` callback (as `lockup-factory` does) to detect `create_account` failure and refund the deposit, and to release/roll back any reservation on failure.

### Proof of Concept
1. Alice intends to deploy a multisig at `treasury.multisig-factory.near` and prepares `create({"name": "treasury", "members": [alice, bob, carol], "num_confirmations": 2})`, attaching 50 NEAR.
2. Mallory observes this pending call (mempool, or simply anticipates the well-known name) and submits `create({"name": "treasury", "members": [mallory], "num_confirmations": 1})` with a higher gas price/priority.
3. Mallory's transaction executes first: `create_account()` on `treasury.multisig-factory.near` succeeds, `deploy_contract` installs the multisig code, and `function_call("new", {members: [mallory], num_confirmations: 1})` initializes it with Mallory as sole signer. [4](#0-3) 
4. Alice's transaction now executes: `create_account()` on the same account fails because the account already exists; since there is no `.then()` callback, there is no application-level detection or refund logic — the receipt simply fails.
5. Any NEAR later sent to `treasury.multisig-factory.near` (by convention, trusting the documented name) is now controlled solely by Mallory, who can withdraw it with a single confirmation.

### Citations

**File:** multisig-factory/src/lib.rs (L29-49)
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
                json!({ "members": members, "num_confirmations": num_confirmations })
                    .to_string()
                    .as_bytes()
                    .to_vec(),
                0,
                env::prepaid_gas() - CREATE_CALL_GAS,
            )
    }
```

**File:** staking-pool-factory/src/lib.rs (L160-171)
```rust
        assert!(
            env::is_valid_account_id(owner_id.as_bytes()),
            "The owner account ID is invalid"
        );
        reward_fee_fraction.assert_valid();

        assert!(
            self.staking_pool_account_ids
                .insert(&staking_pool_account_id),
            "The staking pool account ID already exists"
        );

```

**File:** lockup-factory/src/lib.rs (L168-198)
```rust
    /// Callback after a lockup was created.
    /// Returns the promise if the lockup creation succeeded.
    /// Otherwise refunds the attached deposit and returns `false`.
    pub fn on_lockup_create(
        &mut self,
        lockup_account_id: AccountId,
        attached_deposit: U128,
        predecessor_account_id: AccountId,
    ) -> bool {
        assert_self();

        let lockup_account_created = is_promise_success();

        if lockup_account_created {
            env::log(
                format!("The lockup contract {} was successfully created.", lockup_account_id)
                    .as_bytes(),
            );
            true
        } else {
            env::log(
                format!(
                    "The lockup {} creation has failed. Returning attached deposit of {} to {}",
                    lockup_account_id, attached_deposit.0, predecessor_account_id
                )
                    .as_bytes(),
            );
            Promise::new(predecessor_account_id).transfer(attached_deposit.0);
            false
        }
    }
```

**File:** multisig-factory/README.md (L26-29)
```markdown
Create a new multisig with the given parameters and attached amount (50N) passed to multisig contract:

```
near call $CONTRACT_ID create '{"name": "test", "members": [{"account_id": "illia"}, {"account_id": "testmewell.testnet"}, {"public_key": "ed25519:Eg2jtsiMrprn7zgKKUk79qM1hWhANsFyE6JSX4txLEuy"}], "num_confirmations": 1}'  --accountId $CONTRACT_ID --amount 50 --gas 100000000000000
```
