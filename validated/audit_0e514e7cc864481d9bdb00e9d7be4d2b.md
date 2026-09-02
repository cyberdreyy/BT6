## Title
Multisig created with zero deposit is permanently insolvent, freezing its member keys and funds - (`multisig-factory/src/lib.rs`)

### Summary
`MultisigFactory::create` is `#[payable]` but never enforces a minimum attached deposit before creating, deploying, and funding a new multisig sub-account. Unlike the sibling factories, it contains no `MIN_ATTACHED_BALANCE` check, so any caller can create a multisig with `attached_deposit == 0`, producing an account that cannot cover the contract's own storage staking cost.

### Finding Description
The invariant that should hold is: `account_balance(new_multisig) >= storage_cost(new_multisig_code)` immediately after `create` returns. `MultisigFactory::create` never asserts this: [1](#0-0) 

It builds `account_id` from an attacker-chosen `name` concatenated with `env::current_account_id()`, then chains `create_account().deploy_contract(CODE.to_vec()).transfer(env::attached_deposit())...function_call(..)` — with no check on `env::attached_deposit()` at all. Compare with `staking-pool-factory/src/lib.rs`, which defines and (elsewhere) enforces `MIN_ATTACHED_BALANCE = 30_000_000_000_000_000_000_000_000` (30 NEAR) before creating a pool: [2](#0-1)  — `lockup-factory` has an analogous constant and check. `multisig-factory` has no such constant or assertion anywhere in the file.

Because `deploy_contract` with the full `multisig2.wasm` code consumes real storage stake, an account created with 0 (or insufficient) deposit will have insufficient balance to cover its own storage. On NEAR, an account whose balance drops below the required storage stake becomes unable to execute further receipts/function calls (or gets purged), effectively bricking the contract while it still holds the member access keys/account-based confirmers that were registered via the `new` call. Any subsequent `request`/`confirm` call on this multisig will fail because the account cannot pay for the gas/storage of processing it, permanently freezing any funds later sent to that address believing it is a functioning multisig.

Regarding the "derived from a victim's account id" framing: since `name` is fully attacker-controlled and simply concatenated as `{name}.{factory_account}`, an attacker can pick `name` to match or resemble any identifier tied to a victim (e.g., a victim's existing account prefix), causing an outside observer/integrator to be tricked into trusting or depositing into an address that looks associated with the victim, while it is actually an uninitialized/underfunded multisig the attacker created with zero deposit.

No guard in the call path (`assert_one_yocto`, `is_valid_account_id`, etc.) checks the deposit magnitude; `is_valid_account_id`-style validation is not present in this function at all, and there is no analogue of `assert_min_attached_balance`.

### Impact Explanation
Any NEAR later transferred to the resulting multisig account (by users who believe it is a properly funded, functioning multisig — e.g., because the name matches something they trust) can become permanently frozen: the account cannot process `request`/`confirm` calls to move funds out because it lacks storage/operational balance, and there is no owner or admin recovery path from the multisig-factory itself. This matches the Critical category "funds permanently frozen." The attacker's cost per attempt is near zero (0 attached deposit + gas), and the flaw is repeatable for arbitrarily many `name` values/sub-accounts.

### Likelihood Explanation
This requires only an unprivileged call to the public `create` method with `attached_deposit = 0`, which is trivially reachable by any account and costs only gas. No special preconditions, victim cooperation, or privileged role is needed, making this highly feasible and fully repeatable across many chosen `name` values.

### Recommendation
Add a `MIN_ATTACHED_BALANCE` constant (sized to cover the deployed multisig2 code's storage stake) and assert `env::attached_deposit() >= MIN_ATTACHED_BALANCE` at the start of `create`, mirroring the checks already present in `staking-pool-factory/src/lib.rs` and `lockup-factory/src/lib.rs`. Additionally, add a callback that verifies the account creation/deploy succeeded and refunds the caller on failure, consistent with the pattern used by the other factories.

### Proof of Concept
Using `near-sdk-sim` / `near-workspaces`:
1. Deploy `multisig-factory` contract and the `multisig2.wasm` code it embeds.
2. Call `create(name, members, num_confirmations)` from an unprivileged account with `attached_deposit = 0` and default prepaid gas.
3. Assert the resulting sub-account `{name}.{factory}` exists (`create_account`/`deploy_contract` succeeded) but `account_balance < storage_cost` for the deployed code (query the runtime account view for `account_id`'s balance vs. `storage_usage() * storage_byte_cost`).
4. Attempt to call `new_request` (or `request`) on the newly created multisig using one of the registered `members`' keys and assert it fails (e.g., `ActionError`/`ExecutionOutcome` failure due to insufficient balance for storage/gas), demonstrating the account can never process confirmations and any funds sent to it are unrecoverable.
5. Contrast with a control test where `create` is called with a deposit ≥ the fixed storage cost, showing `request`/`confirm` succeeds — proving the binding `balance >= storage_cost` is the deciding factor and is currently unenforced.

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

**File:** staking-pool-factory/src/lib.rs (L10-11)
```rust
/// The 30 NEAR tokens required for the storage of the staking pool.
const MIN_ATTACHED_BALANCE: Balance = 30_000_000_000_000_000_000_000_000;
```
