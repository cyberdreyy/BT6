This is a genuine, well-known design characteristic of this staking-pool contract: `internal_ping` cannot distinguish between validator-earned rewards and any bare NEAR transfer sent directly to the pool's account, since both increase `env::account_balance()` and thus `total_balance`. The code has no way to attribute a raw transfer's origin, so it folds it entirely into `total_reward` and distributes it via the existing owner-fee/delegator split.

### Title
Bare NEAR transfers to the staking pool account are misattributed as validator rewards and socialized to existing delegators/owner - (File: staking-pool/src/internal.rs)

### Summary
`internal_ping` computes `total_reward` as `total_balance - self.last_total_balance`, where `total_balance` includes `env::account_balance()` [1](#0-0) . Any bare NEAR transfer sent directly to the pool's account_id (no function call) increases `account_balance()` identically to a real validator reward, so on the next `ping`/`deposit`/`withdraw` call, the transferred amount is folded into `total_reward` and distributed as `remaining_reward` to `total_staked_balance` and `owners_fee` to the owner [2](#0-1) , while the sender's account row is never touched and receives no shares or credit.

### Finding Description
Binding claimed broken: `total_staked_balance_after - total_staked_balance_before == validator_reward_paid_this_epoch` (i.e., increases to `total_staked_balance` should originate only from staking rewards actually paid by the protocol to this pool's validator, not from arbitrary attacker-controlled deposits).

Code path: `internal_ping` (staking-pool/src/internal.rs:194-250) computes `total_balance = env::account_locked_balance() + env::account_balance() - env::attached_deposit()` and treats any increase over `self.last_total_balance` as `total_reward`, splitting it into `owners_fee` (credited as shares to `owner_id`) and `remaining_reward` (added directly to `self.total_staked_balance` for all existing shareholders, pro-rata via the share-price mechanism) [3](#0-2) . `env::account_balance()` is the actual NEAR balance held by the contract account, which increases identically whether NEAR arrives via `Promise::transfer` from validator rewards or via a plain `Transfer` action sent by any external account with no attached function call. The contract has no mechanism to differentiate the source, so `internal_ping` cannot exclude attacker-sent balance from the reward calculation.

Attacker's exact call: send a bare NEAR transfer (a `Transfer` action, not a `FunctionCall`) directly to the staking pool's `account_id`. No preconditions on the attacker are required beyond being able to send a NEAR transaction; existing delegators with `stake_shares > 0` are needed only so the misattributed reward has recipients (the pool owner always exists and receives the fee share). The next call to `ping()`, `deposit()`, `deposit_and_stake()`, `withdraw()`, or `unstake()` (all of which call `internal_ping` first) will read the inflated `account_balance()` and permanently move the attacker's NEAR into `total_staked_balance`/owner shares.

None of the existing guards prevent this: there is no `assert_owner`/`assert_self`/`is_promise_success` gate on `internal_ping`'s reward computation, no on-chain distinction between "reward transfer from the protocol" and "arbitrary transfer from any account" is possible in the NEAR runtime, and the only assertion present, `total_balance >= self.last_total_balance` (staking-pool/src/internal.rs:208-211), does not distinguish reward sources — it only prevents balance from ever appearing to decrease.

### Impact Explanation
The attacker's transferred NEAR is absorbed into `self.total_staked_balance` (benefiting existing delegators pro-rata via the share price) and into `owners_fee` shares credited to `self.owner_id`, while the sender's own `Account` row in `self.accounts` is never created or credited — the sender permanently loses the transferred funds with no corresponding claim. This is a real, repeatable misattribution of value to parties (existing delegators and the pool owner) who did not earn it, at the direct expense of the transfer sender, matching the "rewards ... attributed to the wrong party" High-severity category. However, note the practical incentive: the attacker is the only one harmed (they lose NEAR and gain nothing), so this is not something a rational unprivileged attacker would repeat for gain against themselves — it more closely resembles a griefing/self-harm action or an accidental-transfer misattribution issue rather than an attacker profiting at a victim's expense. Any "gain" accrues to third parties (existing delegators/owner), not to the attacker, and the attacker has no way to target which delegator benefits or extract value back out.

### Likelihood Explanation
Trivial to trigger: no special privileges, only a plain NEAR transfer transaction to a known, public staking pool account_id, and the pool must have at least one pending epoch transition (`last_epoch_height != epoch_height`) so the next `ping` picks it up — a condition that occurs naturally every epoch. However, since the attacker only loses funds and cannot capture the misattributed value for themselves, there is no attacker incentive to execute this, making real-world exploitation implausible despite technical feasibility.

### Recommendation
This is inherent to how NEAR staking-pool reference contracts compute rewards (comparing total account balance across epochs) and is a known/accepted characteristic of the design rather than a fixable code defect within this contract alone — the contract cannot cryptographically distinguish "validator reward transfer" from "arbitrary transfer" using only `env::account_balance()`. No practical in-contract fix exists without validator/runtime-level provenance for balance changes; this should be treated as a documented design limitation rather than a patchable bug, and no plan is provided since it requires no privileged actor and no code change would produce an attacker-favorable exploit path.

### Proof of Concept
A `cargo test` using `testing_env!`/`VMContextBuilder` can demonstrate the mechanics (not attacker gain):
1. Set up a pool with one delegator who deposits and stakes `ntoy(N)`, establishing `total_staked_balance` and `self.last_total_balance`.
2. Bump `account_balance` in the `VMContextBuilder` by `ntoy(5)` without calling `deposit` (simulating a bare transfer landing in the contract's balance) and advance `epoch_height`.
3. Call `ping()` and assert `total_staked_balance` increased by `ntoy(5)` split between `remaining_reward` (credited pro-rata to the existing delegator via share price) and `owners_fee` (credited as shares to `owner_id`), while asserting no `Account` row exists for "the sender" of the bare transfer (since the sender's identity is not even observable on-chain via a bare transfer — this must be modeled as an untracked balance bump, confirming the sender receives nothing).

This confirms the technical mechanism described, but per the Impact/Likelihood analysis above, it represents value transfer away from the attacker to third parties, not attacker profit — the write-up is provided per the rules' requirement to trace and validate, not as a finding recommending remediation to protect an attacker-exploitable path.

### Citations

**File:** staking-pool/src/internal.rs (L205-234)
```rust
        let total_balance =
            env::account_locked_balance() + env::account_balance() - env::attached_deposit();

        assert!(
            total_balance >= self.last_total_balance,
            "The new total balance should not be less than the old total balance"
        );
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
