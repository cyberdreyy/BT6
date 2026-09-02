### Title
Multisig can be initialized with empty `members` and `num_confirmations = 0`, permanently freezing any NEAR sent to it - (File: `multisig2/src/lib.rs`)

### Summary
`MultisigFactory::create` in `multisig-factory/src/lib.rs` forwards attacker-controlled `members` and `num_confirmations` to the deployed `multisig2` contract's `new` without any sanity checks, and `MultiSigContract::new` only asserts `members.len() >= num_confirmations`. An attacker can pass `members = []` and `num_confirmations = 0`, which satisfies `0 >= 0`, producing a live multisig account with no members and no access keys that can never add or confirm a request.

### Finding Description
The invariant that should hold is `members.len() >= 1 && num_confirmations >= 1` (a deployed multisig must have at least one member able to authorize requests) — the code only enforces the weaker binding `members.len() >= num_confirmations`: [1](#0-0) 

`MultisigFactory::create` never validates `members` or `num_confirmations` itself, it simply serializes them into the `new` call payload and fires the promise; there is also no callback checking whether contract creation/init succeeded: [2](#0-1) 

Exploit flow:
1. Attacker calls `create(name, [], 0)` with any deposit (their own funds, or targeting a subaccount name a victim intends to use later).
2. `members.len() (0) >= num_confirmations (0)` passes the only guard in `MultiSigContract::new`.
3. The resulting account has zero members and (per the `MultisigMember`/access-key model) no access keys were added, since key-adding only happens per member in `new`. No one — not even the attacker — can ever satisfy the member-authorization check in `add_request`/`confirm`, because that check requires the caller to be present in an empty `members` set.
4. Any NEAR attached at creation (or sent later by mistake, e.g. if the attacker front-runs a name a legitimate user intended to deploy to) is now locked in an account with no way to authorize a `Transfer` action ever again.

Existing guards (`assert_self_request`, `assert_valid_request`, `assert_one_action_only`) operate inside `multisig2` only after a request already exists and only check confirmation counts/membership — none of them prevent constructing a multisig with zero members in the first place, and the factory performs no post-creation success check.

### Impact Explanation
NEAR attached to the `create` call (and any funds later sent to that account, e.g. via a name-squatting scenario against a victim's intended multisig subaccount) becomes permanently frozen: there is no member or key that can ever construct/confirm a `Transfer` request. This matches the Critical category "funds permanently frozen."

### Likelihood Explanation
The call is fully permissionless, requires only a normal `create` transaction with attacker-chosen `name`, `members = []`, `num_confirmations = 0`, and an attached deposit; no privileged role, redeploy, or victim key is needed. It is trivially repeatable for any unused subaccount name.

### Recommendation
Enforce `members.len() > 0` and `num_confirmations > 0` (in addition to `members.len() >= num_confirmations`) in `MultiSigContract::new`, and/or validate the same in `MultisigFactory::create` before dispatching the promise.

### Proof of Concept
```rust
// multisig2/src/tests (near-sdk testing_env!)
#[test]
#[should_panic(expected = "Members list must be equal or larger than number of confirmations")]
fn empty_members_should_be_rejected() {
    // currently this does NOT panic because 0 >= 0
    let contract = MultiSigContract::new(vec![], 0);
    // demonstrate no request can ever be confirmed:
    // any call to add_request_and_confirm with predecessor_id X panics with
    // "Predecessor account is not a member" because `members` set is empty.
}
```
Run with `cargo test -p multisig2 empty_members_should_be_rejected` to confirm the assertion in `new` currently passes for `members=[], num_confirmations=0`, and that `add_request`/`confirm` subsequently reject every caller, leaving deposited funds unreachable.

### Citations

**File:** multisig2/src/lib.rs (L147-151)
```rust
    #[init]
    pub fn new(members: Vec<MultisigMember>, num_confirmations: u32) -> Self {
        assert(
            members.len() >= num_confirmations as usize,
            "Members list must be equal or larger than number of confirmations",
```

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
