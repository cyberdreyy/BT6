### Title
Sequential `unstake` calls reset the account-wide `unstaked_available_epoch_height`, freezing already-matured funds - (File: staking-pool/src/internal.rs)

### Summary
`inner_unstake` stores a single `unstaked_available_epoch_height` per account that is overwritten on every call, and `internal_withdraw` gates the entire `account.unstaked` balance on this one scalar rather than per-tranche. A delegator who unstakes twice at different epochs will have their first tranche's 4-epoch wait extended to match the second, later unstake.

### Finding Description
The broken binding: `withdrawable(tranche_i) == (unstaked_available_epoch_height(tranche_i) <= epoch_height())`, i.e. each unstake tranche should unlock independently at `unstake_epoch(tranche_i) + NUM_EPOCHS_TO_UNLOCK`. Instead the contract implements `withdrawable(any_tranche) == (account.unstaked_available_epoch_height <= epoch_height())` where `account.unstaked_available_epoch_height` is a single field overwritten by the most recent `unstake` call: [1](#0-0) 

and enforced against the whole `account.unstaked` pool in `internal_withdraw`: [2](#0-1) 

Exploit flow exactly as described: `unstake(large_amount)`@E sets `unstaked_available_epoch_height = E+4`; `unstake(small_amount)`@E+3 (before withdrawal) overwrites it to `E+7`; at E+4, `withdraw(large_amount)` fails the assert at line 51-54 because the account-wide gate is now `E+7`, even though `large_amount`'s own 4-epoch wait (E→E+4) has already elapsed. None of the existing guards (`assert_owner`, `assert_self`, `is_promise_success`, etc.) address this because the issue is purely in the accounting granularity of `internal_withdraw`/`inner_unstake` — there is no per-tranche tracking, only one scalar per account.

### Impact Explanation
No funds leave the contract improperly and no third party gains anything; the delegator's own already-unlocked NEAR (`large_amount`) becomes temporarily unwithdrawable until the later gate (`E+7`) passes, after which it becomes withdrawable again. This matches the "funds frozen for at least one epoch but recoverable" category (High) rather than permanent loss. The affected funds are recoverable — the delegator can simply wait until `E+7` and withdraw the full `unstaked` balance (both tranches) in one call. Blast radius: any delegator who performs staggered `unstake` calls against any deployed instance of this staking-pool contract; it is self-inflicted (only affects the caller's own funds) and repeatable across accounts/epochs, but does not allow the attacker to extract value or affect other delegators' balances, `total_staked_balance`, `total_stake_shares`, or `last_total_balance`.

### Likelihood Explanation
Trivial to trigger: any delegator (unprivileged, no special preconditions) who calls `unstake` twice at different epochs before withdrawing will experience this. No attached deposit tricks, no cross-account interaction, and no third party is required — it happens purely from the caller's own sequential actions. This is highly likely to occur in normal usage patterns (delegators unstaking incrementally over time) rather than requiring an adversarial setup.

### Recommendation
Track unstaked balance per-tranche (e.g., a per-account list/map of `(amount, unlock_epoch)` entries, or restructure so a new `unstake` call does not extend the wait for previously-unstaked-and-not-yet-withdrawn amounts) so that `withdraw` can release each tranche independently once its own `unlock_epoch` has passed, rather than gating the whole `unstaked` balance on the most recent unstake's epoch height.

### Proof of Concept
```rust
// cargo test using testing_env! (near-sdk unit test harness)
#[test]
fn test_staggered_unstake_freezes_earlier_tranche() {
    let mut contract = /* init StakingContract, deposit_and_stake some large amount from ALICE */;

    // Epoch E: unstake large_amount
    testing_env!(get_context(alice.clone(), ..., epoch_height = E));
    contract.unstake(large_amount.into());
    let acc = contract.internal_get_account(&alice);
    assert_eq!(acc.unstaked_available_epoch_height, E + NUM_EPOCHS_TO_UNLOCK); // E+4

    // Epoch E+3: unstake small_amount (before withdrawing large_amount)
    testing_env!(get_context(alice.clone(), ..., epoch_height = E + 3));
    contract.unstake(small_amount.into());
    let acc = contract.internal_get_account(&alice);
    assert_eq!(acc.unstaked_available_epoch_height, E + 3 + NUM_EPOCHS_TO_UNLOCK); // E+7

    // Epoch E+4: large_amount's own 4-epoch wait (E -> E+4) has elapsed,
    // but withdraw is blocked by the account-wide gate (E+7 > E+4)
    testing_env!(get_context(alice.clone(), ..., epoch_height = E + 4));
    let result = std::panic::catch_unwind(|| contract.withdraw(large_amount.into()));
    assert!(result.is_err()); // panics: "The unstaked balance is not yet available due to unstaking delay"

    // Binding check: withdrawable(large_amount tranche) should be true at E+4
    // but internal_withdraw's actual check uses account.unstaked_available_epoch_height == E+7,
    // demonstrating tranche_i's own schedule (E+4) != account-wide gate (E+7).
}
```

### Citations

**File:** staking-pool/src/internal.rs (L42-54)
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
```

**File:** staking-pool/src/internal.rs (L154-157)
```rust
        account.stake_shares -= num_shares;
        account.unstaked += receive_amount;
        account.unstaked_available_epoch_height = env::epoch_height() + NUM_EPOCHS_TO_UNLOCK;
        self.internal_save_account(&account_id, &account);
```
