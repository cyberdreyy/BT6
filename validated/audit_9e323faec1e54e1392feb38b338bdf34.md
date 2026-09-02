Confirmed by the code: `inner_unstake` at line 156 unconditionally sets `account.unstaked_available_epoch_height = env::epoch_height() + NUM_EPOCHS_TO_UNLOCK` regardless of any prior value, and this single field gates the entire `account.unstaked` balance (not a per-tranche gate), which is checked in `internal_withdraw` at line 52 (`assert!(account.unstaked_available_epoch_height <= env::epoch_height())`) against the whole `account.unstaked` amount at line 48 (`account.unstaked >= amount`).

### Title
Unstaking resets the unlock gate for the entire `account.unstaked` balance, re-freezing already-matured NEAR - (staking-pool/src/internal.rs)

### Summary
`inner_unstake` stores only a single scalar `unstaked_available_epoch_height` per account and overwrites it on every unstake call, without tracking per-tranche unlock times. A delegator with old, already-mature unstaked NEAR who calls `unstake` again for even a trivial new amount will have the gate reset forward, causing `withdraw`/`withdraw_all` to reject the previously-withdrawable balance until the new delay elapses.

### Finding Description
The binding claimed to hold is: NEAR withdrawable at time T == the portion of `account.unstaked` whose original unlock epoch (`unstaked_available_epoch_height` as set at the time that portion was unstaked) has passed at T.

Tracing the code: `Account` stores a single `unstaked: Balance` and a single `unstaked_available_epoch_height: EpochHeight` field (used in [1](#0-0) ). There is no per-deposit/per-unstake-batch tracking — all unstaked NEAR for an account shares one balance and one unlock height.

In `inner_unstake` (staking-pool/src/internal.rs:124-181), when a delegator unstakes any amount, the code does: [2](#0-1) 
This adds the new `receive_amount` to `account.unstaked` (which may already contain old, matured unstaked balance) and unconditionally overwrites `unstaked_available_epoch_height` to `env::epoch_height() + NUM_EPOCHS_TO_UNLOCK`, with no check of whether the existing gate had already passed and no separation of the old already-unlocked funds from the newly-locked funds.

`internal_withdraw` then gates the *entire* `account.unstaked` balance behind this single, now-advanced timestamp: [3](#0-2) 

Exploit flow: at epoch E1 the delegator calls `unstake(X)`, setting `unstaked_available_epoch_height = E1+4`. They do nothing until epoch E1+4, at which point `X` is fully mature and withdrawable. Instead of withdrawing, they call `unstake(1)` (or any small `amount`, min 1 yoctoNEAR-equivalent stake share) at epoch E1+4. `inner_unstake` executes assertions that don't prevent this (amount>0, `total_staked_balance>0`, `num_shares>0`, `account.stake_shares >= num_shares`, `receive_amount>0` — none of these check the existing unlock state), adds the tiny new receive amount to `account.unstaked`, and resets `unstaked_available_epoch_height` to `(E1+4)+4 = E1+8`. Calling `withdraw_all()` (or `withdraw(X)`) before E1+8 now hits the `assert!` in `internal_withdraw` (line 51-54) and panics/reverts, even though `X` had already matured at E1+4.

No existing guard prevents this: there's no per-tranche accounting, and none of the listed guards (`assert_owner`, `assert_self`, `is_promise_success`, `assert_one_yocto`, U256 rounding, etc.) address the single-gate-for-mixed-balances design flaw.

### Impact Explanation
The delegator's own already-mature NEAR (`X`) is frozen for up to `NUM_EPOCHS_TO_UNLOCK` (4) additional epochs, though it remains fully recoverable once the new gate passes — the total amount owed and the delegator entitled to it never change; no NEAR leaves the contract to an unauthorized party and no accounting value diverges permanently. This matches the "funds frozen for at least one epoch but recoverable" High-severity category. It is self-inflicted (the attacker freezes their own funds via a normal `unstake` call) rather than something one delegator can do to another delegator's balance, since `unstake` only ever operates on `env::predecessor_account_id()`'s own account.

### Likelihood Explanation
This is trivially reachable by any delegator through completely normal use of the public `unstake` method — no special privileges, deposits, or contract deployments are required. It requires only: (1) an existing unstake request whose lock has matured, and (2) any subsequent call to `unstake` for a nonzero amount before withdrawing. Given `unstake` is a routine action (e.g., partially unstaking again to unstake more), this can easily happen unintentionally, making it highly likely to occur in practice, though the "attacker" here mainly harms themselves (delayed liquidity) rather than gaining value at another's expense.

### Recommendation
Track unstaked balance per unlock-epoch (e.g., a small map/queue of `(epoch_height, amount)` entries, or split into "pending" and "available" balances), so a new `unstake` call cannot retroactively extend the lock on already-matured funds. At minimum, before overwriting `unstaked_available_epoch_height`, check whether the account's currently unstaked balance is already fully or partially available and preserve that portion as immediately withdrawable, only gating the newly added `receive_amount`.

### Proof of Concept
```rust
// staking-pool/src/internal.rs or a new tests module (illustrative; would be added under
// #[cfg(test)] using existing testing_env! helpers in staking-pool/src/lib.rs test setup)

#[test]
fn test_unstake_relocks_matured_balance() {
    // 1. Set up contract with delegator having staked balance, epoch = E1.
    // 2. Call unstake(X) -> account.unstaked_available_epoch_height == E1 + NUM_EPOCHS_TO_UNLOCK (E1+4)
    // 3. Advance epoch_height to E1+4 (testing_env! with new epoch).
    //    assert!(account.unstaked_available_epoch_height <= env::epoch_height()); // true, X is mature
    // 4. Call unstake(1) again at E1+4.
    //    -> account.unstaked_available_epoch_height overwritten to E1+8
    // 5. Still at epoch E1+4 (or any epoch < E1+8), call withdraw_all().
    //    assert should panic: "The unstaked balance is not yet available due to unstaking delay"
    //    even though X (the majority of account.unstaked) matured at E1+4.
    // 6. Advance to E1+8 and confirm withdraw_all() now succeeds, showing funds were frozen
    //    for 4 extra epochs but eventually recoverable (High, not Critical).
}
```

### Citations

**File:** staking-pool/src/internal.rs (L42-56)
```rust
    pub(crate) fn internal_withdraw(&mut self, amount: Balance) {
        assert!(amount > 0, "Withdrawal amount should be positive");

        let account_id = env::predecessor_account_id();
        let mut account = self.internal_get_account(&account_id);
        assert!(
            account.unstaked >= amount,
            "Not enough unstaked balance to withdraw"
        );
        assert!(
            account.unstaked_available_epoch_height <= env::epoch_height(),
            "The unstaked balance is not yet available due to unstaking delay"
        );
        account.unstaked -= amount;
        self.internal_save_account(&account_id, &account);
```

**File:** staking-pool/src/internal.rs (L154-157)
```rust
        account.stake_shares -= num_shares;
        account.unstaked += receive_amount;
        account.unstaked_available_epoch_height = env::epoch_height() + NUM_EPOCHS_TO_UNLOCK;
        self.internal_save_account(&account_id, &account);
```
