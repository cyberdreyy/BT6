### No vulnerability found for this question.

**Analysis:** In `near_deposit()`, the registration-bound check happens *before* any state mutation: [1](#0-0) . The `assert!(amount >= self.ft.storage_balance_bounds().min.0, "ERR_DEPOSIT_TOO_SMALL")` panics before `internal_register_account` or `internal_deposit` is ever called if the account is unregistered and the attached deposit is one yoctoNEAR short of the bound. On NEAR, a panicking function call fully reverts the receipt's state changes and the attached deposit is automatically refunded to the predecessor by the runtime — there is no partial accounting, no retained NEAR, and no minted wNEAR. The claimed "accounting already ran before the check" ordering does not exist in this code; the guard strictly precedes the effect.

`near_withdraw` similarly enforces `assert_one_yocto()` [2](#0-1)  and calls `internal_withdraw` (which reduces the caller's ft balance) prior to the transfer Promise, but that path is unrelated to the deposit-registration-bound scenario described and does not permit unbacked minting.

Since the equality "attached deposit < bound ⇒ no state mutated, deposit refunded" holds by inspection of the code order and NEAR's panic/rollback semantics, the invariant is not broken.

### Citations

**File:** w-near/src/w_near.rs (L17-27)
```rust
        if !self.ft.accounts.contains_key(&account_id) {
            // Not registered, register if enough $NEAR has been attached.
            // Subtract registration amount from the account balance.
            assert!(
                amount >= self.ft.storage_balance_bounds().min.0,
                "ERR_DEPOSIT_TOO_SMALL"
            );
            self.ft.internal_register_account(&account_id);
            amount -= self.ft.storage_balance_bounds().min.0;
        }
        self.ft.internal_deposit(&account_id, amount);
```

**File:** w-near/src/w_near.rs (L38-46)
```rust
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
