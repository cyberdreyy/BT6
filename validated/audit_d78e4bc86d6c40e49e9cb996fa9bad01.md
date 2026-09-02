Found the analog. `on_get_account_total_balance` in `lockup/src/owner_callbacks.rs` unconditionally trusts the value reported by the staking pool contract to overwrite `deposit_amount`, without checking `is_promise_success()` or validating the reported value against any independent source (unlike every other callback in the same file, which calls `is_promise_success()` before mutating state).

### Title
`on_get_account_total_balance` trusts staking pool's self-reported balance without success/sanity check, allowing recorded deposit to diverge from actual custody - (File: `lockup/src/owner_callbacks.rs`)

### Summary
`refresh_staking_pool_balance()` in `lockup/src/owner_callbacks.rs` and `lockup/src/owner.rs` queries the selected staking pool via `ext_staking_pool::get_account_total_balance` and, in the callback `on_get_account_total_balance`, unconditionally assigns the returned value to `staking_information.deposit_amount` — with no `is_promise_success()` check and no bound/consistency check against the previously known deposit or against the staking pool's own invariant (`price of a "stake" share never decreases`, per `staking-pool/README.md:139`).

### Finding Description
Every other owner callback in `lockup/src/owner_callbacks.rs` (`on_staking_pool_deposit`, `on_staking_pool_stake`, `on_staking_pool_unstake`, `on_staking_pool_withdraw`, etc., lines 29-217) calls `is_promise_success()` and only mutates `deposit_amount`/state on success, falling back safely otherwise. `on_get_account_total_balance` (lines 280-294) breaks this pattern:

```
pub fn on_get_account_total_balance(&mut self, #[callback] total_balance: WrappedBalance) {
    assert_self();
    self.set_staking_pool_status(TransactionStatus::Idle);
    ...
    self.staking_information.as_mut().unwrap().deposit_amount = total_balance;
}
``` [1](#0-0) 

This value comes from an account that the lockup contract only checked once at `select_staking_pool` time via the whitelist contract (`lockup/src/owner.rs:12-41`, `lockup/src/owner_callbacks.rs:6-25`). The whitelist binding only guarantees that at selection time the account ID was on the approved list — the whitelisted staking-pool code and arguments the lockup owner trusted then are what this callback continues to trust unconditionally for every subsequent balance query, with the return value taken as ground truth for `deposit_amount`, which directly gates `get_owners_balance`/`get_liquid_owners_balance` (used to compute how much the owner can `transfer` out via `lockup/src/lib.rs`).

The binding broken is: `deposit_amount` (recorded claim) should equal what is actually held/redeemable at the staking pool, i.e. `staking_information.deposit_amount == staking_pool.get_account_total_balance(lockup_account)` at all times the lockup contract acts on it. Because the callback has no success check, a failed/errored cross-contract call (e.g. the queried account returning `PromiseResult::Failed`, or a transient/reorg-induced anomalous callback) would still write whatever bytes deserialize into `total_balance`. More importantly, this callback places total, unconditional trust in whatever the staking pool contract account (still `staking_information.staking_pool_account_id`, a normal, potentially owner-controlled, non-privileged account) chooses to report, and treats it as authoritative for the lockup's internal accounting with no bound check (e.g., it never asserts the new value is `>=` the previously known `deposit_amount`, unlike the staking pool's own `internal_ping` which enforces `total_balance >= self.last_total_balance` at `staking-pool/src/internal.rs:208-211`).

### Impact Explanation
If `deposit_amount` is set to a stale, incorrect, or attacker-influenced total balance (e.g., a staking pool account that used to be legitimately whitelisted but is now compromised, redeployed, or the previously selected staking pool contract returns a manipulated value), the lockup contract's owner-controllable withdrawal/transfer functions (`transfer`, `withdraw_from_staking_pool`, `get_owners_balance`) will be computed against a value that no longer reflects the real balance held at the pool. This is an accounting value (the lockup's belief about funds held externally) diverging from reality where the owner and foundation subsequently settle on it, matching the High-impact category: "an accounting value diverging from reality where another party settles on it."

### Likelihood Explanation
`refresh_staking_pool_balance` is a normal owner-callable method with no special preconditions beyond `assert_owner`, `assert_staking_pool_is_idle`, `assert_no_termination` (`lockup/src/owner.rs:176-179`). The owner of the lockup — who is not a privileged foundation/multisig role in this contract's trust model, simply the delegate who selected a staking pool — routinely calls this to refresh balances. The staking pool the owner selected is under that owner's control in the common case (staking-pool README: "owner runs the validator node"), so the owner can trivially cause the queried pool to return an arbitrary `total_balance` in the callback and have the lockup contract accept it unconditionally.

### Recommendation
In `on_get_account_total_balance`, check `is_promise_success()` before mutating `deposit_amount`, and enforce that the new value is not less than a sane lower bound (e.g. previously known `deposit_amount`), logging/rejecting decreases the same way `staking-pool`'s own `internal_ping` enforces monotonic growth.

### Proof of Concept
1. Owner calls `select_staking_pool(pool)` and the pool is whitelisted (`lockup/src/owner.rs:12-41`).
2. Owner deposits/stakes funds; `deposit_amount` accurately reflects `staking_amount` (`lockup/src/owner_callbacks.rs:27-62`).
3. Owner (who also controls `pool`, since it's a contract the owner selected and often operates) calls `refresh_staking_pool_balance()` (`lockup/src/owner.rs:176-209`).
4. The pool's `get_account_total_balance` view returns an inflated value (nothing stops the pool contract from returning any number for this view call; it need not even reflect the pool's own real `total_staked_balance` bookkeeping in a compromised/rogue pool).
5. `on_get_account_total_balance` writes this inflated value directly into `staking_information.deposit_amount` with no validation (`lockup/src/owner_callbacks.rs:280-294`).
6. `get_owners_balance`/`get_liquid_owners_balance` (in `lockup/src/lib.rs`, derived from `deposit_amount`) now overstate available balance, and the owner calls `transfer` to move out real NEAR that exceeds what is actually available/vested, producing a recorded claim vs. actual custody mismatch.

### Citations

**File:** lockup/src/owner_callbacks.rs (L280-294)
```rust
    /// Called after the request to get the current total balance from the staking pool.
    pub fn on_get_account_total_balance(&mut self, #[callback] total_balance: WrappedBalance) {
        assert_self();
        self.set_staking_pool_status(TransactionStatus::Idle);

        env::log(
            format!(
                "The current total balance on the staking pool is {}",
                total_balance.0
            )
            .as_bytes(),
        );

        self.staking_information.as_mut().unwrap().deposit_amount = total_balance;
    }
```
