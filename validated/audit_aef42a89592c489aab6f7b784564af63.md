### Binding
Claimed invariant: `ft.total_supply_before_call == ft.total_supply_after_failed_call` (and no `Promise` scheduled) when `near_withdraw` is called with attached deposit ≠ 1 yoctoNEAR.

### Trace
In `near_withdraw`, `assert_one_yocto()` is called at line 39, strictly before `self.ft.internal_withdraw(&account_id, amount)` at line 42, which is the only statement that mutates `ft.total_supply` and account balances, and before the `Promise::new(account_id).transfer(...)` at line 45 is constructed/returned. [1](#0-0) 

`assert_one_yocto()` (from `near_sdk`) panics whenever `env::attached_deposit() != 1`. Since NEAR contract execution is atomic — a panic anywhere in a function call causes the entire receipt/transaction execution to be rolled back with no state changes persisted and no promises/receipts created — a panic at line 39 guarantees `internal_withdraw` never executes and no `Promise` is scheduled, for both the 0-yoctoNEAR and 2-yoctoNEAR attack attempts.

Both sides of the binding hold: `ft.total_supply` and the attacker's account balance are unchanged before and after the failed call, matching the claim in the prompt.

No vulnerability found for this question.

### Citations

**File:** w-near/src/w_near.rs (L37-46)
```rust
    #[payable]
    pub fn near_withdraw(&mut self, amount: U128) -> Promise {
        assert_one_yocto();
        let account_id = env::predecessor_account_id();
        let amount = amount.into();
        self.ft.internal_withdraw(&account_id, amount);
        log!("Withdraw {} yoctoNEAR from {}", amount, account_id);
        // Transferring NEAR and refunding 1 yoctoNEAR.
        Promise::new(account_id).transfer(amount + 1)
    }
```
