### Title
Undistributed rewards during zero-total-supply periods are permanently stuck — `depositReward` never rolls them over - (File: contracts/StakingRewards.sol)

### Summary
`StakingRewards` (and the identical `DelegateStakingRewardsIDLE`) is a fork of Synthetix `StakingRewards` used by `IdleCDO` to distribute rewards to tranche-token stakers. When `_totalSupply == 0`, `rewardPerToken()` returns early without accruing, so all `rewardRate` emissions during that period are earned by nobody. On the next `depositReward`, only `remaining * rewardRate` is carried over as `leftover`; the rewards that "dripped" into the void while supply was zero are never accounted for and remain stuck in the contract balance. [1](#0-0) [2](#0-1) 

### Finding Description
`depositReward` computes the new rate as:

```solidity
if (block.timestamp >= periodFinish) {
  rewardRate = reward / rewardsDuration;
} else {
  uint256 remaining = periodFinish - block.timestamp;
  uint256 leftover = remaining * rewardRate;
  rewardRate = (reward + leftover) / rewardsDuration;
}
```

The `leftover` term only counts rewards still scheduled for the *future* (`remaining * rewardRate`). Rewards that were emitted in the past portion of the current period but accrued to no one — because `rewardPerToken()` short-circuits when `_totalSupply == 0` — are excluded. The same applies when the previous period has fully elapsed (`block.timestamp >= periodFinish`): everything emitted during a zero-supply interval of that period is dropped from the rollover entirely.

Concretely in this codebase: `IdleCDO` calls `depositReward` on the staking contract to distribute incentive tokens, and `stakeFor` lets `rewardsDistribution` stake tranche tokens received as fees for `feeReceiver`. Between epochs — e.g., when all tranche holders have withdrawn, or before the first `stake` after rewards are notified — `_totalSupply` can legitimately be zero while `rewardRate` keeps "streaming" into nothing. [3](#0-2) [4](#0-3) 

The `rewardRate <= balance / rewardsDuration` sanity check even fails to catch this: it uses the *full* token balance (including the orphaned amount), so the stuck tokens silently pass the check and are double-counted in subsequent rate computations' capacity without ever becoming claimable. [5](#0-4) 

### Impact Explanation
Reward tokens equal to `(zeroSupplyDuration) * rewardRate` are locked in the contract forever with respect to stakers — no user can ever `earned()`/`getReward()` them because `rewardPerTokenStored` never advanced during the zero-supply window and `depositReward` never re-adds the gap to a future rate. Each successive `depositReward` compounds the loss. Only the owner's `recoverERC20` can rescue them, which is an off-band privileged sweep rather than distribution to the intended recipients — economically the rewards are lost to stakers. [6](#0-5) [7](#0-6) 

Quantification: with `rewardsDuration = 1 days` and any interval of `T` seconds of zero total supply, the stuck amount is `rewardRate * T`. If `depositReward` is called with `reward = R` while supply is zero for the whole period, the entire `R` is orphaned (stakers joining later only earn on the *new* rate from their stake time forward).

### Likelihood Explanation
High within the reward lifecycle. Staking is permissioned only by `rewardsDistribution` via `stakeFor`, or by direct `stake`; there is no guarantee of continuous non-zero supply. Realistic zero-supply windows occur: (a) before the first stake after the initial `depositReward`, (b) after all stakers `withdraw`/`exit` while a period is still live, (c) for `DelegateStakingRewardsIDLE`, whenever whitelisted stakers fully exit. No attacker action is needed — but an unprivileged staker can also *induce* it by being the sole staker, exiting mid-period, and re-entering next period, knowing the gap rewards are burned to the contract rather than rolled forward (denying other participants the yield). [8](#0-7) [9](#0-8) 

### Recommendation
Track rewards emitted but not accrued. Two options:

1. On `depositReward`, compute the actually-undistributed amount: `undistributed = (block.timestamp - lastUpdateTime) * rewardRate` when `rewardPerTokenStored` did not advance (i.e., supply was zero for part/all of the window), and fold it into the rollover: `rewardRate = (reward + leftover + undistributed) / rewardsDuration`.
2. Simpler: in `updateReward`/`rewardPerToken`, freeze `lastUpdateTime` advancement or track `lastNonZeroSupplyTime`, so time only counts against `rewardRate` when `_totalSupply > 0` — emissions effectively pause during zero-supply windows and resume on the first stake, making rollovers automatic.

Also consider excluding previously-stuck amounts from the `balance` used in the `rewardRate <= balance / rewardsDuration` check.

### Proof of Concept
Foundry fork PoC against `contracts/StakingRewards.sol` (identical for `DelegateStakingRewardsIDLE.sol`):

```solidity
// setup: stakingToken = tranche token, rewardsToken = e.g. IDLE,
// rewardsDistribution = IdleCDO, rewardsDuration = 1 days

// 1. rewardsDistribution calls depositReward(R) at t0.
//    rewardRate = R / 1 days; periodFinish = t0 + 1 days; _totalSupply == 0.
vm.prank(rewardsDistribution);
staking.depositReward(address(0), R);

// 2. Warp half the period with zero supply.
vm.warp(t0 + 12 hours);
assertEq(staking.rewardPerToken(), 0); // nothing accrued, but time elapsed

// 3. Alice stakes; she only earns from now on.
staking.stake(100e18);

// 4. rewardsDistribution tops up mid-period with R2.
vm.prank(rewardsDistribution);
staking.depositReward(address(0), R2);
// leftover = (12h) * rewardRate is rolled, but the first 12h of emissions
// (R/2) is NOT: rewardRate' = (R2 + R/2) / 1 days, and R/2 sits orphaned.

// 5. At periodFinish, sum of all claimable earned() < R + R2 by exactly R/2.
//    rewardsToken.balanceOf(staking) == R + R2, while total distributed == R + R2 - R/2.
//    The R/2 remainder is only exitable via owner.recoverERC20 -> lost to stakers.
```

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

**File:** contracts/StakingRewards.sol (L84-120)
```text
  function stakeFor(address _user, uint256 amount) external {
    require(msg.sender == rewardsDistribution, 'Only rewards distribution can stake for user');
    _stake(_user, msg.sender, amount);
  }

  function stake(uint256 amount) external {
    _stake(msg.sender, msg.sender, amount);
  }
  function _stake(address _user, address _payer, uint256 amount) internal nonReentrant whenNotPaused updateReward(_user) {
    require(amount > 0, "Cannot stake 0");
    _totalSupply += amount;
    _balances[_user] += amount;
    stakingToken.safeTransferFrom(_payer, address(this), amount);
    emit Staked(_user, amount);
  }

  function withdraw(uint256 amount) public nonReentrant updateReward(msg.sender) {
    require(amount > 0, "Cannot withdraw 0");
    _totalSupply -= amount;
    _balances[msg.sender] -= amount;
    stakingToken.safeTransfer(msg.sender, amount);
    emit Withdrawn(msg.sender, amount);
  }

  function getReward() public nonReentrant updateReward(msg.sender) {
    uint256 reward = rewards[msg.sender];
    if (reward > 0) {
      rewards[msg.sender] = 0;
      rewardsToken.safeTransfer(msg.sender, reward);
      emit RewardPaid(msg.sender, reward);
    }
  }

  function exit() external {
    withdraw(_balances[msg.sender]);
    getReward();
  }
```

**File:** contracts/StakingRewards.sol (L129-140)
```text
  function depositReward(address, uint256 reward) external onlyRewardsDistribution updateReward(address(0)) {
    if (shouldTransfer) {
      rewardsToken.safeTransferFrom(msg.sender, address(this), reward);
    }

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

**File:** contracts/StakingRewards.sol (L156-160)
```text
  function recoverERC20(address tokenAddress, uint256 tokenAmount) external onlyOwner {
    require(tokenAddress != address(stakingToken), "Cannot withdraw the staking token");
    IERC20(tokenAddress).safeTransfer(owner(), tokenAmount);
    emit Recovered(tokenAddress, tokenAmount);
  }
```

**File:** contracts/DelegateStakingRewardsIDLE.sol (L154-160)
```text
    if (block.timestamp >= periodFinish) {
      rewardRate = reward / rewardsDuration;
    } else {
      uint256 remaining = periodFinish - block.timestamp;
      uint256 leftover = remaining * rewardRate;
      rewardRate = (reward + leftover) / rewardsDuration;
    }
```
