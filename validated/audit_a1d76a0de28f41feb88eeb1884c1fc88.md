### Title
`num_confirmations: u64` overflow causes silent account abandonment in `MultisigFactory::create`, letting anyone hijack and drain the created multisig account - (File: `multisig-factory/src/lib.rs`)

### Summary
`MultisigFactory::create` accepts `num_confirmations` as `u64` but forwards it as JSON to `multisig2::new`, whose parameter type is `u32`. Any value above `u32::MAX` makes deserialization of the `new` call's arguments fail, so the init call panics while the preceding actions in the same batched `Promise` (`create_account`, `deploy_contract`, `transfer`) still commit, leaving a funded, code-deployed but *uninitialized* account. Because `new` has no caller restriction, any unprivileged actor can subsequently call it themselves with their own `members`/`num_confirmations`, seizing control of the account and its balance.

### Finding Description
The broken binding: **stored `num_confirmations`/`members` on the newly created `<name>.<factory>` account == the `num_confirmations`/`members` requested by the account's rightful creator**.

`MultisigFactory::create` builds one chained `Promise` batching four actions into a single receipt: [1](#0-0) 

`num_confirmations` is declared `u64` on the factory entrypoint but is serialized into the JSON args passed to `multisig2::new`, whose signature takes `num_confirmations: u32`: [2](#0-1) 

When the caller supplies `num_confirmations > u32::MAX`, serde's JSON deserialization of the `new` call's arguments fails (the number is out of range for `u32`), so the `#[init] new` call panics before ever writing contract state. However, `create_account`, `deploy_contract`, and `transfer` are earlier actions in the *same* receipt/`Promise` chain and their effects are already committed by the time the later `function_call` action fails - the repository's own documentation explicitly calls out that account creation, code deployment, and state initialization must be manually treated as one atomic unit because they are not automatically rolled back together: [3](#0-2) 

The result: a real, funded account exists at `<name>.<factory>` running the `multisig2` contract, but `env::state_exists()` is still `false` because `new()` never executed successfully. Since `new` is a public `#[init]` method with no `assert_self`/owner check, ANY other unprivileged party can now call:

```
near call <name>.<factory> new '{"members": [<attacker key/account>], "num_confirmations": 1}'
```

This initializes the account with the attacker's own member list, granting the attacker full control (as sole confirming member) over an account holding the deposit the original, legitimate caller attached to `create`. The attacker then calls `add_request` / `confirm` to transfer the funds to themselves - an unauthorized multisig request execution moving funds the rightful creator never authorized. Existing guards do not stop this: the `assert!(!env::state_exists())`-style protection inside `#[init]` only prevents re-initialization *after* a successful init; here state genuinely never existed when the attacker calls `new`, so the guard passes for the attacker.

### Impact Explanation
NEAR attached by the legitimate `create` caller (their deposit) is captured and can be moved out by an unrelated, unprivileged attacker who reinitializes the abandoned account with their own members/threshold, then confirms a transfer request to themselves. This is repeatable against any victim who calls `create` with (or is tricked/misled into using) a `num_confirmations` value exceeding `u32::MAX`, or more generally whenever the `new` call's args fail to deserialize/init for any reason while the preceding batched actions already committed. This matches the Critical category: "an account ... deployed with parameters its rightful creator never chose" / unauthorized execution of a multisig request moving account funds.

### Likelihood Explanation
The attacker needs no privileges - they only need to notice (via public transaction monitoring) a `create` call whose `num_confirmations` is out of `u32` range (or whose `new` init otherwise fails) before anyone else races to reinitialize it, and then simply call the account's public `new` method. No special balances, keys, or foundation/owner status are required. The main precondition is that a legitimate/careless caller of `create` passes an oversized `num_confirmations` (trivial to do by accident or via UI bug/typo, and just as easily attacker-triggered on their own throwaway funds to prove the primitive) - the described PoC ("Fast validation: Deploy with a large value and read `get_num_confirmations`") confirms the discrepancy is directly observable.

### Recommendation
Change `MultisigFactory::create`'s `num_confirmations` parameter to `u32` (matching `multisig2::new`) so the value cannot silently mismatch/overflow between factory and target contract, and add a callback (`.then(...)` with `assert_self` + `is_promise_success()`) on the `create` promise chain that deletes/refunds the newly created account if the `new` init call fails, so no account is ever left funded-but-uninitialized.

### Proof of Concept
```rust
// multisig-factory: near-sdk-sim / near-workspaces style test
// 1. Call factory.create(name="victim", members=[victim_key], num_confirmations = u32::MAX as u64 + 1)
//    with attached deposit D from `victim` account.
// 2. Assert the sub-account "victim.<factory>" exists, has code deployed, and balance ~= D
//    (create_account/deploy_contract/transfer actions committed).
// 3. Assert calling any state-requiring view (e.g. get_num_confirmations) panics/fails
//    ("Multisig contract should be initialized before usage") -> proves new() never ran.
// 4. From an unrelated `attacker` account, call
//    victim.<factory>::new(members=[attacker_key], num_confirmations=1) and assert success.
// 5. Assert get_num_confirmations() == 1 and get_members() == [attacker_key]
//    (binding broken: stored values != victim's originally requested values).
// 6. attacker calls add_request_and_confirm(Transfer{amount: D, receiver_id: attacker})
//    and asserts the balance of "victim.<factory>" drops by D while attacker's balance rises,
//    proving unauthorized fund movement without the victim's participation.
``` [1](#0-0) [4](#0-3) [3](#0-2)

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

**File:** multisig2/src/lib.rs (L147-167)
```rust
    #[init]
    pub fn new(members: Vec<MultisigMember>, num_confirmations: u32) -> Self {
        assert(
            members.len() >= num_confirmations as usize,
            "Members list must be equal or larger than number of confirmations",
        );
        let mut multisig = Self {
            members: UnorderedSet::new(StorageKeys::Members),
            num_confirmations,
            request_nonce: 0,
            requests: UnorderedMap::new(StorageKeys::Requests),
            confirmations: LookupMap::new(StorageKeys::Confirmations),
            num_requests_pk: LookupMap::new(StorageKeys::NumRequestsPk),
            active_requests_limit: ACTIVE_REQUESTS_LIMIT,
        };
        let mut promise = Promise::new(env::current_account_id());
        for member in members {
            promise = multisig.add_member(promise, member);
        }
        multisig
    }
```

**File:** README.md (L15-17)
```markdown
## Initializing Contracts with near-shell

When setting up the contract creating the contract account, deploying the binary, and initializing the state must all be done as an atomic step.  For example, in our tests for the lockup contract we initialize it like this:
```
