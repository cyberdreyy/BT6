# No vulnerability found for this question.

**Why the premise doesn't hold:**

The claimed "window where a withdrawal's NEAR has left the accounting but not yet the account" does not exist in `internal_withdraw`. The promise creation and the `last_total_balance` decrement happen synchronously, in the same function call, with no cross-contract callback in between: [1](#0-0) 

Specifically, `account.unstaked -= amount` is saved, then `Promise::new(account_id).transfer(amount)` is scheduled, and `self.last_total_balance -= amount` executes immediately afterward — all within one synchronous function execution. In NEAR's receipt model, no other transaction (including a `ping()` from another account) can interleave in the middle of this function's execution; receipts are processed one at a time, and there's no `await`/callback point here (unlike `internal_restake`, which does use `on_stake_action` as a callback). So there is no observable intermediate state where the NEAR has "left the accounting" (`last_total_balance` reduced) while the transfer is still merely "in flight" from another caller's perspective — both changes commit together before the function returns and before any other receipt for this contract can execute.

Separately, even if such a window existed, `internal_ping` is guarded to run at most once per epoch: [2](#0-1) 

Any additional `ping()` calls within the same epoch return `false` immediately without touching `last_total_balance`, `total_staked_balance`, or any account. So the "repeat thousands of times with dust amounts inside one epoch" mechanism described in the question cannot amplify any effect — only the first ping (or ping-triggering call) per epoch does any accounting work at all.

Because both preconditions for the described exploit (a genuine reentrancy/interleaving window, and the ability for repeated pings within an epoch to matter) are false given the actual code, the binding `total_balance` (computed via `env::account_locked_balance() + env::account_balance() - env::attached_deposit()`) at ping time cannot diverge from reality in the way described — `env::account_balance()` and `self.last_total_balance` are always updated together, atomically, for every balance-affecting action (`internal_deposit`, `internal_withdraw`, `internal_stake` via total_staked_balance, `internal_ping` itself).

### Citations

**File:** staking-pool/src/internal.rs (L55-68)
```rust
        account.unstaked -= amount;
        self.internal_save_account(&account_id, &account);

        env::log(
            format!(
                "@{} withdrawing {}. New unstaked balance is {}",
                account_id, amount, account.unstaked
            )
            .as_bytes(),
        );

        Promise::new(account_id).transfer(amount);
        self.last_total_balance -= amount;
    }
```

**File:** staking-pool/src/internal.rs (L194-199)
```rust
    pub(crate) fn internal_ping(&mut self) -> bool {
        let epoch_height = env::epoch_height();
        if self.last_epoch_height == epoch_height {
            return false;
        }
        self.last_epoch_height = epoch_height;
```
