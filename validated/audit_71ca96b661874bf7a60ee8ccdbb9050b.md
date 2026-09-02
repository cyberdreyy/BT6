Based on my research, I found a legitimate analog of the reported truncation-based fee-loss bug in `staking-pool/src/internal.rs`, in the `internal_ping` reward-distribution logic.

### Title
Owner's reward fee can be silently redistributed to delegators due to share-rounding truncation - (File: staking-pool/src/internal.rs)

### Summary
`internal_ping` computes the pool owner's fee (`owners_fee`) from `total_reward`, then converts that fee into "stake" shares via `num_shares_from_staked_amount_rounded_down`, which performs integer division that rounds toward zero. Regardless of whether that division produces `0` shares, the contract unconditionally adds the full `owners_fee` to `total_staked_balance`. When the computed number of shares is `0`, the owner receives no shares for the fee they were entitled to, while the value is still folded into the global staked balance — silently increasing the price of every other delegator's share instead of being attributed to the owner.

### Finding Description
In `internal_ping` [1](#0-0) , the owner's fee is computed as `self.reward_fee_fraction.multiply(total_reward)`, and then:
```
let num_shares = self.num_shares_from_staked_amount_rounded_down(owners_fee);
if num_shares > 0 {
    // credit owner_id with num_shares
    self.total_stake_shares += num_shares;
}
self.total_staked_balance += owners_fee;
```
`num_shares_from_staked_amount_rounded_down` computes `total_stake_shares * amount / total_staked_balance` using truncating integer division [2](#0-1) . Since the invariant of the pool guarantees share price `>= 1` (number of shares is always `<=` staked tokens, per the README's stated invariant) [3](#0-2) , whenever `owners_fee` is smaller than the current price of a single share, `num_shares` truncates to `0`. In that case the `if num_shares > 0` block is skipped — the owner's internal account is never credited with shares, and `total_stake_shares` is not incremented — yet `total_staked_balance` is still increased by the full `owners_fee` on the next line. The invariant equality that should hold is:
`owner_shares_value == owners_fee`, but instead `owner_shares_value == 0` while `total_staked_balance` (i.e., the denominator that determines every share's redemption value) is inflated by `owners_fee` anyway.

The practical effect is that the fee amount that should have gone exclusively to the owner is instead spread proportionally across all existing delegator shares (since the share price `total_staked_balance / total_stake_shares` increases without the owner's share count increasing to match). The owner's `get_account_total_balance` and `get_account_staked_balance` views [4](#0-3)  will not reflect the fee they were due for that epoch.

### Impact Explanation
This falls under "rewards or fees mis-attributed" (High): the accounting record of the owner's earned fee diverges from the actual value distributed, and delegators unknowingly settle on an inflated share price that includes value that was supposed to be the owner's exclusive fee.

### Likelihood Explanation
Likelihood is low in practice: it requires `owners_fee` (a fraction of the reward for a single epoch) to be smaller than the current price of one "stake" share, which is close to `1` yoctoNEAR under normal circumstances (near the `STAKE_SHARE_PRICE_GUARANTEE_FUND` size) [5](#0-4) . This mirrors the acknowledged uncommon-outcome nature of the original C02 report, but is not impossible — pools with a very small reward fee fraction, low total rewards in an epoch, or a pool with a very high total_staked_balance-to-total_stake_shares ratio could trigger it.

### Recommendation
Round up when converting the owner's fee into shares (mirroring `num_shares_from_staked_amount_rounded_up`, already used elsewhere for unstaking) so the owner is never under-credited, or skip adding `owners_fee` to `total_staked_balance` when `num_shares == 0` so unattributed fee amounts are not silently absorbed into the delegator pool.

### Proof of Concept
1. Set `reward_fee_fraction` to a very small fraction (or have a pool with a very large `total_staked_balance` relative to `total_stake_shares`, i.e., share price much higher than 1).
2. Trigger `ping()`/`internal_ping()` with a `total_reward` such that `owners_fee = reward_fee_fraction.multiply(total_reward)` is nonzero but smaller than the current share price.
3. Observe: `num_shares_from_staked_amount_rounded_down(owners_fee)` returns `0`; the `if num_shares > 0` branch is skipped so the owner's account is not credited with shares, but `self.total_staked_balance += owners_fee` still executes at [6](#0-5) .
4. Confirm via `get_account_total_balance(owner)` that the owner's balance did not increase by `owners_fee`, while `get_total_staked_balance()` did increase by it — the fee value has been redistributed to all delegators through the increased share price instead of paid to the owner.

### Citations

**File:** staking-pool/src/internal.rs (L212-234)
```rust
        let total_reward = total_balance - self.last_total_balance;
        if total_reward > 0 {
            // The validation fee that the contract owner takes.
            let owners_fee = self.reward_fee_fraction.multiply(total_reward);

            // Distributing the remaining reward to the delegators first.
            let remaining_reward = total_reward - owners_fee;
            self.total_staked_balance += remaining_reward;

            // Now buying "stake" shares for the contract owner at the new share price.
            let num_shares = self.num_shares_from_staked_amount_rounded_down(owners_fee);
            if num_shares > 0 {
                // Updating owner's inner account
                let owner_id = self.owner_id.clone();
                let mut account = self.internal_get_account(&owner_id);
                account.stake_shares += num_shares;
                self.internal_save_account(&owner_id, &account);
                // Increasing the total amount of "stake" shares.
                self.total_stake_shares += num_shares;
            }
            // Increasing the total staked balance by the owners fee, no matter whether the owner
            // received any shares or not.
            self.total_staked_balance += owners_fee;
```

**File:** staking-pool/src/internal.rs (L261-272)
```rust
    pub(crate) fn num_shares_from_staked_amount_rounded_down(
        &self,
        amount: Balance,
    ) -> NumStakeShares {
        assert!(
            self.total_staked_balance > 0,
            "The total staked balance can't be 0"
        );
        (U256::from(self.total_stake_shares) * U256::from(amount)
            / U256::from(self.total_staked_balance))
        .as_u128()
    }
```

**File:** staking-pool/README.md (L137-140)
```markdown

- The staking pool contract is secure if it doesn't have any access keys.
- The price of a "stake" is always at least `1`.
- The price of a "stake" share never decreases.
```

**File:** staking-pool/README.md (L331-343)
```markdown
pub fn get_account_total_balance(&self, account_id: AccountId) -> U128;

/// Returns `true` if the given account can withdraw tokens in the current epoch.
pub fn is_account_unstaked_balance_available(&self, account_id: AccountId) -> bool;

/// Returns the total staking balance.
pub fn get_total_staked_balance(&self) -> U128;

/// Returns account ID of the staking pool owner.
pub fn get_owner_id(&self) -> AccountId;

/// Returns the current reward fee as a fraction.
pub fn get_reward_fee_fraction(&self) -> RewardFeeFraction;
```

**File:** staking-pool/src/lib.rs (L21-23)
```rust
/// The amount of yocto NEAR the contract dedicates to guarantee that the "share" price never
/// decreases. It's used during rounding errors for share -> amount conversions.
const STAKE_SHARE_PRICE_GUARANTEE_FUND: Balance = 1_000_000_000_000;
```
