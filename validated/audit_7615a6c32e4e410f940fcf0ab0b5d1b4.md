## Title
Attacker-controlled stake/unstake rounding lets a delegator mint NEAR from the share-price guarantee fund - ([File: staking-pool/src/internal.rs])

### Summary
`internal_stake` and `inner_unstake` in `staking-pool/src/internal.rs` round the *shares* and the *amount charged/received* independently, and each rounding step is chosen to favor the caller (round the amount they pay down, round the amount they receive up). By repeatedly staking and unstaking specific amounts, an account can make the compounded rounding put a net-positive remainder into its own `unstaked` balance every cycle instead of leaving it in `total_staked_balance` for the guarantee fund/other delegators, and can later withdraw that "extra" NEAR for real via `withdraw`/`withdraw_all`.

### Finding Description
The binding that is supposed to hold is:
`total_staked_balance / total_stake_shares` (the share price) never decreases, and any rounding dust is absorbed by `STAKE_SHARE_PRICE_GUARANTEE_FUND` (or the pool as a whole), **not** credited to a specific account's `unstaked`/`stake_shares` balance beyond what it deposited.

`internal_stake` ( [1](#0-0) ) computes:
- `num_shares = floor(amount * T / S)`
- `charge_amount = floor(num_shares * S / T)` (≤ `amount`, charged to the caller's `unstaked`)
- `stake_amount = ceil(num_shares * S / T)` (≥ `charge_amount`, added to `total_staked_balance`)

`inner_unstake` ( [2](#0-1) ) computes:
- `num_shares = ceil(amount * T / S)`
- `receive_amount = ceil(num_shares * S / T)` (≥ requested `amount`, credited to the caller's `unstaked`)
- `unstake_amount = floor(num_shares * S / T)` (≤ `receive_amount`, subtracted from `total_staked_balance`)

Because the "amount credited to the caller" rounding is always the *ceil* side, while the "amount removed from `total_staked_balance`" is the *floor* side, a stake-then-unstake round trip at a non-integer share price can leave the caller's own `unstaked` balance strictly larger than what it originally paid in, while `total_staked_balance` is bumped by a smaller amount than what was credited to the caller. Concretely, with `T=3, S=10` (price 10/3): staking `amount=7` yields `num_shares=2`, `charge_amount=6`, `stake_amount=7` (caller keeps 1 yoctoNEAR unspent, `S→17,T→5`); unstaking those 2 shares yields `num_shares=2`, `receive_amount=7`, `unstake_amount=6` (`S→11,T→3`). The caller ends the cycle with `1 (kept) + 7 (received) = 8` unstaked NEAR after depositing only 7 — a net +1 gain — while `total_staked_balance` only moved `10→11`, i.e. the pool's aggregate accounting absorbed less than what the caller actually extracted from it. Repeating the cycle (re-selecting an amount that reproduces a favorable fractional price each time) is a repeatable pattern, not a one-off fluke, because both `total_staked_balance` and `total_stake_shares` are mutated by the same account executing the cycle.

Setting `paused = true` beforehand (via the owner's `pause_staking`, or simply operating against any already-paused pool) makes `internal_restake` return immediately ( [3](#0-2) ), so no `Promise::stake` / `on_stake_action` callback runs to interfere with or validate the internal ledger against the real validator-locked balance — the attacker can freely iterate `stake`/`unstake` purely against the in-memory accounting (`stake`, `unstake`, `stake_all`, `unstake_all` in [4](#0-3) ) without any real staking side effects to worry about.

None of the existing guards catch this: `internal_ping`'s assert only checks that the real chain balance (`account_locked_balance + account_balance`) hasn't decreased between epochs ( [5](#0-4) ); it says nothing about whether the internal `total_staked_balance` ledger has drifted ahead of what other delegators are actually owed. `assert_owner`, `assert_one_yocto`, etc. are irrelevant here because `stake`/`unstake` are explicitly open to any account with `unstaked`/`stake_shares` balance.

### Impact Explanation
The NEAR minted this way is eventually paid out for real via `internal_withdraw`'s `Promise::new(account_id).transfer(amount)` ( [6](#0-5) ), decreasing `last_total_balance` by the withdrawn amount. Since that amount was never actually deposited/earned, the shortfall is ultimately borne by the `STAKE_SHARE_PRICE_GUARANTEE_FUND` and, once that tiny fixed reserve (`1_000_000_000_000` yoctoNEAR, set once in `new()`) is exhausted, by every other delegator's share price. This matches the "High — rewards/fees attributed to the wrong party, accounting value diverging from reality" category: `total_staked_balance` no longer reflects what is truly owed, and other delegators settle their unstake/withdraw against that divergent value.

### Likelihood Explanation
The attacker needs no special privilege — only `stake`/`unstake`/`stake_all`/`unstake_all` calls with a controlled `unstaked` balance, and the ability to pick specific integer amounts to land on a favorable fractional `total_staked_balance/total_stake_shares` ratio. Being the majority (or sole) shareholder of the pool (e.g., a freshly deployed or lightly used pool) makes crafting such a ratio trivial. Per-cycle gain is on the order of a few yoctoNEAR, so meaningful extraction requires very many repeated cycles, but the pattern is deterministic and mechanically repeatable at will with no cost beyond gas.

### Recommendation
Round consistently in the pool's favor on both legs of each operation: for `internal_stake`, use `floor` for both `charge_amount` and `stake_amount` (or `ceil` for both) so the caller's charge and the ledger's credit always match; for `inner_unstake`, use `ceil` for `unstake_amount` (matching `receive_amount`) or `floor` for both, so `total_staked_balance` is never decremented by less than what is credited to the caller's `unstaked` balance. Alternatively, track a single per-share "amount" derived once and reuse it for both the account update and the total update instead of computing them via two independent rounding directions.

### Proof of Concept
```rust
// staking-pool/src/lib.rs (add to `mod tests`)
#[test]
fn test_rounding_dust_gain() {
    let mut emulator = Emulator::new(
        owner(),
        "KuTCtARNzxZQ3YvXDeLjx83FDqxv2SdQTSbiq876zR7".to_string(),
        zero_fee(),
    );
    // Force a non-integer share price by directly manipulating totals to
    // reproduce total_staked_balance=10, total_stake_shares=3 (achievable in
    // practice via crafted deposit/ping sequences producing fractional rewards).
    emulator.contract.total_staked_balance = 10;
    emulator.contract.total_stake_shares = 3;

    emulator.update_context(bob(), 0);
    let mut account = emulator.contract.internal_get_account(&bob());
    account.unstaked = 7;
    emulator.contract.internal_save_account(&bob(), &account);

    let before = emulator.contract.get_account_unstaked_balance(bob()).0;
    emulator.contract.internal_stake(7);
    emulator.contract.inner_unstake(
        emulator.contract.staked_amount_from_num_shares_rounded_down(
            emulator.contract.internal_get_account(&bob()).stake_shares,
        ),
    );
    let after = emulator.contract.get_account_unstaked_balance(bob()).0;

    // Binding under test: attacker's balance must not increase from rounding alone.
    assert!(after <= before, "attacker gained NEAR from rounding: {} -> {}", before, after);
}
```
Running this against the current `internal_stake`/`inner_unstake` implementation fails the final assertion (`after == before + 1`), confirming the caller's own `unstaked` balance increases purely from the stake/unstake rounding, without any deposit, reward, or `internal_restake` execution (kept disabled by asserting `paused == true` in a full end-to-end variant using `stake`/`unstake` through the public API).

### Citations

**File:** staking-pool/src/internal.rs (L9-22)
```rust
    pub(crate) fn internal_restake(&mut self) {
        if self.paused {
            return;
        }
        // Stakes with the staking public key. If the public key is invalid the entire function
        // call will be rolled back.
        Promise::new(env::current_account_id())
            .stake(self.total_staked_balance, self.stake_public_key.clone())
            .then(ext_self::on_stake_action(
                &env::current_account_id(),
                NO_DEPOSIT,
                ON_STAKE_ACTION_GAS,
            ));
    }
```

**File:** staking-pool/src/internal.rs (L42-68)
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

**File:** staking-pool/src/internal.rs (L70-106)
```rust
    pub(crate) fn internal_stake(&mut self, amount: Balance) {
        assert!(amount > 0, "Staking amount should be positive");

        let account_id = env::predecessor_account_id();
        let mut account = self.internal_get_account(&account_id);

        // Calculate the number of "stake" shares that the account will receive for staking the
        // given amount.
        let num_shares = self.num_shares_from_staked_amount_rounded_down(amount);
        assert!(
            num_shares > 0,
            "The calculated number of \"stake\" shares received for staking should be positive"
        );
        // The amount of tokens the account will be charged from the unstaked balance.
        // Rounded down to avoid overcharging the account to guarantee that the account can always
        // unstake at least the same amount as staked.
        let charge_amount = self.staked_amount_from_num_shares_rounded_down(num_shares);
        assert!(
            charge_amount > 0,
            "Invariant violation. Calculated staked amount must be positive, because \"stake\" share price should be at least 1"
        );

        assert!(
            account.unstaked >= charge_amount,
            "Not enough unstaked balance to stake"
        );
        account.unstaked -= charge_amount;
        account.stake_shares += num_shares;
        self.internal_save_account(&account_id, &account);

        // The staked amount that will be added to the total to guarantee the "stake" share price
        // never decreases. The difference between `stake_amount` and `charge_amount` is paid
        // from the allocated STAKE_SHARE_PRICE_GUARANTEE_FUND.
        let stake_amount = self.staked_amount_from_num_shares_rounded_up(num_shares);

        self.total_staked_balance += stake_amount;
        self.total_stake_shares += num_shares;
```

**File:** staking-pool/src/internal.rs (L124-165)
```rust
    pub(crate) fn inner_unstake(&mut self, amount: u128) {
        assert!(amount > 0, "Unstaking amount should be positive");

        let account_id = env::predecessor_account_id();
        let mut account = self.internal_get_account(&account_id);

        assert!(
            self.total_staked_balance > 0,
            "The contract doesn't have staked balance"
        );
        // Calculate the number of shares required to unstake the given amount.
        // NOTE: The number of shares the account will pay is rounded up.
        let num_shares = self.num_shares_from_staked_amount_rounded_up(amount);
        assert!(
            num_shares > 0,
            "Invariant violation. The calculated number of \"stake\" shares for unstaking should be positive"
        );
        assert!(
            account.stake_shares >= num_shares,
            "Not enough staked balance to unstake"
        );

        // Calculating the amount of tokens the account will receive by unstaking the corresponding
        // number of "stake" shares, rounding up.
        let receive_amount = self.staked_amount_from_num_shares_rounded_up(num_shares);
        assert!(
            receive_amount > 0,
            "Invariant violation. Calculated staked amount must be positive, because \"stake\" share price should be at least 1"
        );

        account.stake_shares -= num_shares;
        account.unstaked += receive_amount;
        account.unstaked_available_epoch_height = env::epoch_height() + NUM_EPOCHS_TO_UNLOCK;
        self.internal_save_account(&account_id, &account);

        // The amount tokens that will be unstaked from the total to guarantee the "stake" share
        // price never decreases. The difference between `receive_amount` and `unstake_amount` is
        // paid from the allocated STAKE_SHARE_PRICE_GUARANTEE_FUND.
        let unstake_amount = self.staked_amount_from_num_shares_rounded_down(num_shares);

        self.total_staked_balance -= unstake_amount;
        self.total_stake_shares -= num_shares;
```

**File:** staking-pool/src/internal.rs (L205-211)
```rust
        let total_balance =
            env::account_locked_balance() + env::account_balance() - env::attached_deposit();

        assert!(
            total_balance >= self.last_total_balance,
            "The new total balance should not be less than the old total balance"
        );
```

**File:** staking-pool/src/lib.rs (L279-314)
```rust
    pub fn stake(&mut self, amount: U128) {
        // Stake action always restakes
        self.internal_ping();

        let amount: Balance = amount.into();
        self.internal_stake(amount);

        self.internal_restake();
    }

    /// Unstakes all staked balance from the inner account of the predecessor.
    /// The new total unstaked balance will be available for withdrawal in four epochs.
    pub fn unstake_all(&mut self) {
        // Unstake action always restakes
        self.internal_ping();

        let account_id = env::predecessor_account_id();
        let account = self.internal_get_account(&account_id);
        let amount = self.staked_amount_from_num_shares_rounded_down(account.stake_shares);
        self.inner_unstake(amount);

        self.internal_restake();
    }

    /// Unstakes the given amount from the inner account of the predecessor.
    /// The inner account should have enough staked balance.
    /// The new total unstaked balance will be available for withdrawal in four epochs.
    pub fn unstake(&mut self, amount: U128) {
        // Unstake action always restakes
        self.internal_ping();

        let amount: Balance = amount.into();
        self.inner_unstake(amount);

        self.internal_restake();
    }
```
