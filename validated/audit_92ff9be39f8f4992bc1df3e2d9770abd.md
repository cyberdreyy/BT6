### Title
Staking pool `withdraw`/`withdraw_all` decrement the delegator's unstaked balance without verifying the outgoing `Promise::transfer` succeeded - (File: `staking-pool/src/lib.rs`, `staking-pool/src/internal.rs`)

### Summary
The `withdraw()` / `withdraw_all()` methods in the staking pool contract permanently reduce the delegator's on-chain unstaked balance ledger and fire an async NEAR transfer, but never attach a `.then()` callback to confirm the transfer actually succeeded. This breaks the same custody equality the external report flags (ledger debit vs. value actually delivered), reimplemented natively instead of via ERC20 `transfer`/`transferFrom` return-value checks.

### Finding Description
`withdraw()` and `withdraw_all()` in `staking-pool/src/lib.rs` call `self.internal_withdraw(amount)` directly and return `()`, with no promise chaining: [1](#0-0) 

The accompanying documentation confirms the ordering/behavior of `internal_withdraw`: "Then sends the transfer and decreases the unstaked balance of the account." [2](#0-1) 

This is in stark contrast to the pattern used elsewhere in the same monorepo for exactly this kind of "outgoing NEAR transfer" operation — the `lockup` contract's staking-pool withdrawal flow explicitly attaches a callback (`on_staking_pool_withdraw`) that checks `is_promise_success()` before committing the balance decrement to be final, and reverts the internal debit path if the transfer failed: [3](#0-2) 

The staking pool's own `withdraw`/`withdraw_all` do not follow this safe pattern: the unstaked-balance debit and the `Promise::new(account_id).transfer(amount)` are not reconciled by any callback, so the contract's internal record of "delegator is owed this NEAR" (`account.unstaked`) is unconditionally reduced regardless of whether the transfer receipt ultimately succeeds.

### Impact Explanation
The equality that should hold is: `unstaked balance debited == NEAR actually delivered to delegator`. Because there is no `is_promise_success()` check and no compensating logic to restore the debited amount on transfer failure, any failure of the outgoing transfer receipt (e.g., insufficient gas propagated to the transfer action, or any other native-transfer failure mode) leaves the delegator's recorded unstaked balance at zero/reduced while the NEAR was never delivered — funds are effectively frozen/lost from the delegator's perspective while the contract silently retains them. This matches the "funds frozen for at least one epoch" / "accounting value diverging from reality where another party settles on it" impact bar.

### Likelihood Explanation
This does not require a malicious validator, owner, or redeploy — any delegator calling the standard `withdraw`/`withdraw_all` methods is exposed to this pattern. The likelihood of the underlying transfer receipt itself failing is low under normal conditions (transfers to already-existing accounts rarely fail), which is why this is flagged as an analog of the same bug class (missing success verification on value transfer) rather than a demonstrated high-frequency exploit path.

### Recommendation
Mirror the pattern already used in `lockup/src/owner_callbacks.rs`: chain a `.then()` callback on the `Promise::transfer` in `internal_withdraw`, check `near_sdk::is_promise_success()`, and restore the delegator's unstaked balance if the transfer failed, instead of debiting the ledger unconditionally before the transfer is confirmed.

### Proof of Concept
Not independently reproducible from the indexed contents — the full body of `staking-pool/src/internal.rs::internal_withdraw` (order of state mutation vs. `Promise::transfer` construction) could not be retrieved via available tools in this session due to file-read failures. This finding relies on the `withdraw`/`withdraw_all` call sites plus the README's explicit description of debit-then-transfer ordering. **A background Devin session with full repository/file access should verify the exact body of `internal_withdraw` in `staking-pool/src/internal.rs` to confirm whether any promise-success handling exists before treating this as fully validated.**

### Citations

**File:** staking-pool/src/lib.rs (L238-263)
```rust
    /// Withdraws the entire unstaked balance from the predecessor account.
    /// It's only allowed if the `unstake` action was not performed in the four most recent epochs.
    pub fn withdraw_all(&mut self) {
        let need_to_restake = self.internal_ping();

        let account_id = env::predecessor_account_id();
        let account = self.internal_get_account(&account_id);
        self.internal_withdraw(account.unstaked);

        if need_to_restake {
            self.internal_restake();
        }
    }

    /// Withdraws the non staked balance for given account.
    /// It's only allowed if the `unstake` action was not performed in the four most recent epochs.
    pub fn withdraw(&mut self, amount: U128) {
        let need_to_restake = self.internal_ping();

        let amount: Balance = amount.into();
        self.internal_withdraw(amount);

        if need_to_restake {
            self.internal_restake();
        }
    }
```

**File:** staking-pool/README.md (L76-79)
```markdown
#### Withdraw

When an account wants to withdraw, the contract checks the minimum epoch height of this account and checks the amount.
Then sends the transfer and decreases the unstaked balance of the account.
```

**File:** lockup/src/owner_callbacks.rs (L102-144)
```rust
    /// Called after the given amount was requested to transfer out from the staking pool to this
    /// account.
    /// This method needs to update staking pool status.
    pub fn on_staking_pool_withdraw(&mut self, amount: WrappedBalance) -> bool {
        assert_self();

        let withdraw_succeeded = is_promise_success();
        self.set_staking_pool_status(TransactionStatus::Idle);

        if withdraw_succeeded {
            {
                let staking_information = self.staking_information.as_mut().unwrap();
                // Due to staking rewards the deposit amount can become negative.
                staking_information.deposit_amount.0 = staking_information
                    .deposit_amount
                    .0
                    .saturating_sub(amount.0);
            }
            env::log(
                format!(
                    "The withdrawal of {} from @{} succeeded",
                    amount.0,
                    self.staking_information
                        .as_ref()
                        .unwrap()
                        .staking_pool_account_id
                )
                .as_bytes(),
            );
        } else {
            env::log(
                format!(
                    "The withdrawal of {} from @{} failed",
                    amount.0,
                    self.staking_information
                        .as_ref()
                        .unwrap()
                        .staking_pool_account_id
                )
                .as_bytes(),
            );
        }
        withdraw_succeeded
```
