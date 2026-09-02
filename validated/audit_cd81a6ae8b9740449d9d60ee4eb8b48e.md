### Title
Front-running the multisig sub-account name lets an attacker deploy a multisig with attacker-chosen members and `num_confirmations` at the address the intended owner will later trust - (File: `multisig-factory/src/lib.rs`)

### Summary
`MultisigFactory::create` derives the deployed multisig's account ID solely from the caller-supplied `name` and deploys the multisig with a fully attacker-controlled `members` list and `num_confirmations`, with no reservation, ownership check, or binding to the caller's identity. Any unprivileged account can race the intended owner and claim `<name>.<factory>` first, permanently squatting that address with member keys/accounts the attacker controls.

### Finding Description
The broken binding: `deployed_members(name.factory) == intended_owner_chosen_members(name)` should hold for every sub-account, but the code enforces no such relationship.

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
                .to_string().as_bytes().to_vec(),
            0,
            env::prepaid_gas() - CREATE_CALL_GAS,
        )
}
``` [1](#0-0) 

`name`, `members`, and `num_confirmations` are all attacker-supplied parameters, and `create_account()` on NEAR is a race: whichever transaction executes first claims the account name; the second attempt's `CreateAccountAction` fails at the protocol level. There is no on-chain registry, reservation, deterministic name derivation tied to a specific owner identity, nor any `assert_self()`/callback verifying who is allowed to claim a given `name` — unlike `staking-pool-factory::create_staking_pool`, which at least records `staking_pool_account_ids` to reject duplicates (but still doesn't prevent the first-mover from being an attacker) [2](#0-1) , and unlike `lockup-factory::create`, which derives the sub-account name from `sha256(owner_account_id)` so at least the *address* is bound to the intended owner's account ID [3](#0-2) . `multisig-factory` has neither protection: the sub-account name is an arbitrary string chosen by whoever calls first, and the members/threshold are whatever that caller supplies.

Exploit flow: an attacker observes (or anticipates) that a project intends to deploy its treasury/governance multisig at `projectname.multisig-factory.near`. Before the project's transaction lands, the attacker submits `create("projectname", [attacker_key_or_account], 1)`. The attacker's transaction succeeds; the legitimate owner's later `create` call fails because the account already exists. The address `projectname.multisig-factory.near` now runs a multisig fully controlled by the attacker. If the ecosystem (whitelist contracts, DAOs, users sending funds, pool/poll contracts pointing to "the project's multisig") subsequently treats this address as trusted and routes NEAR/wNEAR/DAO funds or authority to it, the attacker can approve any request themselves (since they control 100% of `members` and set `num_confirmations` to match).

### Impact Explanation
Any NEAR or wNEAR later transferred to, or any privileged role granted to, the squatted address is fully controlled by the attacker's own keys and can be moved out at will — matching the Critical category "NEAR or wNEAR moved out of a...multisig...by a party not entitled to it" and "an account...deployed with parameters its rightful creator never chose." The attack is repeatable against any never-yet-created name across arbitrarily many prospective owners, with the only cost being the attacker's own account-creation deposit/gas.

### Likelihood Explanation
No privileged role is required — only the ability to submit a NEAR transaction attaching a deposit, which is explicitly within the unprivileged attacker's capability set. The only precondition is winning the ordering race against the legitimate creator's transaction (front-running is trivial when the intended name is guessable/observable, e.g., project names, `dao.multisig-factory`, `treasury.multisig-factory`). This is deterministic and reproducible in a local test harness by simulating two competing `create` calls with the same `name` from different predecessors.

### Recommendation
Bind the sub-account name to a value under the true owner's control that an attacker cannot pre-empt — e.g., derive it deterministically from a hash of the caller's own `predecessor_account_id` (as `lockup-factory` does with `owner_account_id`), or require the factory to record an explicit reservation/allowlist tied to a specific caller before permitting `create` for a given `name`. At minimum, document prominently that callers must verify the deployed multisig's `members`/`num_confirmations` via `get_members`/`get_num_confirmations` before funding or trusting `<name>.<factory>`, and never treat an unverified factory sub-account as authoritative.

### Proof of Concept
Using `near-sdk-sim`/`near-workspaces`:
1. Deploy `MultisigFactory` at `factory`.
2. From account `attacker`, call `create(name="dao", members=[attacker_key], num_confirmations=1)` with sufficient deposit — assert the call succeeds and `dao.factory` now exists.
3. From account `legit_owner`, call `create(name="dao", members=[legit_key1, legit_key2], num_confirmations=2)` with sufficient deposit — assert this `CreateAccountAction` fails (`AccountAlreadyExists`), and the deposit is refunded to `legit_owner`.
4. Query `dao.factory::get_members()` / `get_num_confirmations()` and assert the members equal `[attacker_key]` and confirmations `== 1`, i.e. `deployed_members("dao.factory") != legit_owner_chosen_members`, proving the invariant is broken.
5. (Optional) Show attacker can then submit and self-approve a `Transfer` `MultisigRequest` from `dao.factory` using only their own key, draining any balance sent to it, confirming Critical fund-loss impact.

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

**File:** staking-pool-factory/src/lib.rs (L160-170)
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

**File:** lockup-factory/src/lib.rs (L119-121)
```rust
        let byte_slice = env::sha256(owner_account_id.as_ref().as_bytes());
        let lockup_account_id =
            format!("{}.{}", hex::encode(&byte_slice[..20]), env::current_account_id());
```
