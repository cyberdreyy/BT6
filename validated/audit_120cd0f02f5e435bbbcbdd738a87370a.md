### Title
`StakingRewards.depositReward()` undercounts leftover rewards when `_totalSupply == 0`, permanently stranding emitted rewards — (File: contracts/StakingRewards.sol)

### Summary
`StakingRewards` (and its `DelegateStakingRewardsIDLE` twin) is a Synthetix-style rewards contract that `IdleCDO` uses to distribute incentive tokens to tranche-token stakers (`depositReward` is callable by `rewardsDistribution`, which is the CDO). Its `depositReward()` recomputes `rewardRate` as `(reward + leftover) / rewardsDuration` where `leftover = (periodFinish - block.timestamp) * rewardRate`. Because `rewardPerToken()` returns the stored value when `_totalSupply == 0` while `updateReward` still advances `lastUpdateTime`, any rewards emitted during a zero-supply window are silently discarded from the accrual but are also *not* captured in `leftover`. The tokens remain in the contract balance with no distribution path to stakers.

### Finding Description
In `updateReward`, `lastUpdateTime` is always advanced to `lastTimeRewardApplicable()` [1](#0-0) , but `rewardPerToken()` adds nothing while `_totalSupply == 0` [2](#0-1) . So time that elapses with zero stakers consumes the reward schedule without crediting anyone.

When `depositReward` is later called mid-period, it computes the rollover as `leftover = (periodFinish - block.timestamp) * rewardRate` [3](#0-2) . This `leftover` counts *all* undistributed emissions since `periodFinish - rewardsDuration`, but the portion emitted while `_totalSupply == 0` was never accrued to any user. The balance check (`rewardRate <= balance / rewardsDuration`) still passes because the tokens physically sit in the contract [4](#0-3) . The stranded amount then just decays: future stakers earn at the diluted `rewardRate`, and the gap-period tokens are never claimable via `earned()`/`getReward()` [5](#0-4) .

The same logic exists in `DelegateStakingRewardsIDLE.depositReward` [6](#0-5) .

Attacker trace (unprivileged tranche-token holder): during an active reward period, the sole staker calls `withdraw(_balances[msg.sender])` making `_totalSupply == 0`; time passes; IdleCDO (honest `rewardsDistribution`) calls `depositReward`; the attacker re-stakes. Rewards emitted during the gap are excluded from both accrual and `leftover`, yet remain locked in the contract — and because `rewardRate` was reset lower, they are also not redistributed later. Even without an attacker, any natural period of zero staking (e.g., before the first `stakeFor` after deployment, or after `exit()`) produces the same loss.

### Impact Explanation
Rewards equal to `rewardRate * (zero-supply seconds)` per rolled-over period are permanently undistributable to stakers. For a 7-day period (`rewardsDuration = 1 days` here / 7 days in the delegate variant), even a few hours of zero supply strands a proportional share of the reward budget. The tokens can only be rescued via `recoverERC20` (owner action) and are never delivered to the intended recipients — a theft/permanent freezing of unclaimed yield class issue.

### Likelihood Explanation
High. `_totalSupply == 0` occurs routinely: at deployment before the first stake, after the last staker exits, and whenever a single-staker pool is withdrawn. Any `depositReward` (or even just reward emission time passing) during such a window strands value. No privileged misbehavior is required, and any tranche holder can force the condition.

### Recommendation
Track undistributed-but-emitted rewards explicitly, or prevent emission during zero supply:
- Record the exact leftover as `leftover = rewardRate * (periodFinish - block.timestamp)` **plus** a `unaccruedRewards` accumulator updated in `updateReward` (`unaccruedRewards += rewardRate * (now - lastUpdateTime)` when `_totalSupply == 0`), then set `rewardRate = (reward + leftover + unaccruedRewards) / rewardsDuration` and credit it when supply returns, or
- Simply roll the full unaccrued amount into the new rate: `rewardRate = (reward + rewardRate * (periodFinish - lastUpdateTime_effective)) / rewardsDuration` using the timestamp when supply last became zero, or
- Reject `depositReward` while `_totalSupply == 0` and require rewards to be deposited only when stakers exist.

### Proof of Concept
Foundry-style scenario against `contracts/StakingRewards.sol` (fork or unit):

```solidity
// Setup: stakingToken = tranche token, rewardsToken = ERC20 reward,
// rewardsDistribution = mock CDO, rewardsDuration = 1 days.
StakingRewards sr; MockERC20 reward; MockERC20 tranche;

// t0: CDO starts a period with 100 reward tokens
reward.mint(address(cdo), 100e18);
cdo.depositReward(sr, 100e18);          // rewardRate = 100e18 / 1 days

// t1 (+12h): the only staker exits -> _totalSupply == 0
vm.warp(block.timestamp + 12 hours);
sr.withdraw(bal);                        // by sole staker; supply now 0

// t2 (+12h): supply still 0; 50 reward tokens emitted in this gap accrue to nobody
vm.warp(block.timestamp + 12 hours);     // gap emission = 50e18

// t3: CDO rolls over another 100 reward tokens mid-period
vm.prank(cdo); sr.depositReward(address(0), 100e18);
// leftover computed = (periodFinish - now) * oldRate = 12h * rate = 50e18
// but accrual shows zero distributed; new rewardRate = 150e18/1days
// contract holds ~200e18 reward tokens; stakers can ever earn only 150e18
// => 50e18 stranded, claimable only via owner.recoverERC20
assertGt(reward.balanceOf(address(sr)) - sr.getRewardForDuration(), 49e18);
```

The `balance - getRewardForDuration()` surplus grows on every rollover that follows a zero-supply window, demonstrating permanently stranded rewards.

### Citations

**File:** contracts/StakingRewards.sol (L66-71)
```text
  function rewardPerToken() public view returns (uint256) {
    if (_totalSupply == 0) {
      return rewardPerTokenStored;
    }
    return rewardPerTokenStored + ((lastTimeRewardApplicable() - lastUpdateTime) * rewardRate * 1e18 / _totalSupply);
  }
```

**File:** contracts/StakingRewards.sol (L73-75)
```text
  function earned(address account) public view returns (uint256) {
    return (_balances[account] * (rewardPerToken() - userRewardPerTokenPaid[account]) / 1e18) + rewards[account];
  }
```

**File:** contracts/StakingRewards.sol (L134-140)
```text
    if (block.timestamp >= periodFinish) {
      rewardRate = reward / rewardsDuration;
    } else {
      uint256 remaining = periodFinish - block.timestamp;
      uint256 leftover = remaining * rewardRate;
      rewardRate = (reward + leftover) / rewardsDuration;
    }
```

**File:** contracts/StakingRewards.sol (L146-147)
```text
    uint balance = rewardsToken.balanceOf(address(this));
    require(rewardRate <= balance / rewardsDuration, "Provided reward too high");
```

**File:** contracts/StakingRewards.sol (L186-194)
```text
  modifier updateReward(address account) {
    rewardPerTokenStored = rewardPerToken();
    lastUpdateTime = lastTimeRewardApplicable();
    if (account != address(0)) {
        rewards[account] = earned(account);
        userRewardPerTokenPaid[account] = rewardPerTokenStored;
    }
    _;
  }
```

**File:** contracts/DelegateStakingRewardsIDLE.sol (L151-173)
```text
  function depositReward(address, uint256 reward) external onlyRewardsDistribution updateReward(address(0)) {
    rewardsToken.safeTransferFrom(msg.sender, address(this), reward);

    if (block.timestamp >= periodFinish) {
      rewardRate = reward / rewardsDuration;
    } else {
      uint256 remaining = periodFinish - block.timestamp;
      uint256 leftover = remaining * rewardRate;
      rewardRate = (reward + leftover) / rewardsDuration;
    }

    // Ensure the provided reward amount is not more than the balance in the contract.
    // This keeps the reward rate in the right range, preventing overflows due to
    // very high values of rewardRate in the earned and rewardsPerToken functions;
    // Reward + leftover must be less than 2^256 / 10^18 to avoid overflow.
    uint balance = rewardsToken.balanceOf(address(this));
    require(rewardRate <= balance / rewardsDuration, "Provided reward too high");

    lastUpdateTime = block.timestamp;
    periodFinish = block.timestamp + rewardsDuration;

    emit RewardAdded(reward);
  }
```
