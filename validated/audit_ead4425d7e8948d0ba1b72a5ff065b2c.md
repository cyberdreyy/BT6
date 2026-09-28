### Title
Rewards streamed while `totalSupply == 0` are permanently forfeited by stakers - (File: contracts/StakingRewards.sol)

### Summary
`StakingRewards` (the Synthetix-style contract used by `IdleCDO`/`IdleCDOCreditVault`/`IdleCDOEpochVariant` to stream rewards to tranche stakers, including `stakeFor` fee deposits for `feeReceiver`) advances `lastUpdateTime` even when nothing is staked. `rewardPerToken()` correctly skips accrual when `_totalSupply == 0`, but `updateReward` still commits `lastUpdateTime = lastTimeRewardApplicable()`, so every second of reward stream that elapses with zero stakers is burned. Because `depositReward` starts the period immediately at `block.timestamp`, any gap before the next stake produces a guaranteed loss each reward period.

### Finding Description
- `rewardPerToken()` returns `rewardPerTokenStored` unchanged when `_totalSupply == 0` [1](#0-0) .
- However, `updateReward(address(0))` (invoked by `depositReward`) and every `updateReward(account)` call unconditionally store `lastUpdateTime = lastTimeRewardApplicable()` [2](#0-1) .
- `depositReward` sets `periodFinish = block.timestamp + rewardsDuration` and `rewardRate` immediately, regardless of whether anyone is staked [3](#0-2) .
- `earned(account)` only credits `balance * (rewardPerToken() - userRewardPerTokenPaid)`, so time elapsed while `_totalSupply == 0` accrues to nobody [4](#0-3) .

Sequence: (1) the honest `rewardsDistribution` (the IdleCDO contract) calls `depositReward`, starting a period; or an existing period is running and the last staker withdraws. (2) Time passes with `_totalSupply == 0`; any state-changing call (including the next `stake`, which runs `updateReward` before minting the stake) pushes `lastUpdateTime` forward. (3) The first new staker's `userRewardPerTokenPaid` is set to the unchanged `rewardPerTokenStored`, so rewards for the entire empty interval are unclaimable. Loss = `rewardRate * elapsedEmptyTime`, bounded above by `rewardRate * rewardsDuration` (i.e., the entire period's emission if it fully elapses empty). The same flaw exists verbatim in `DelegateStakingRewardsIDLE.sol` [5](#0-4) [6](#0-5) .

### Impact Explanation
Rewards tokens deposited via `depositReward` during empty-supply windows are permanently unearnable by stakers — they remain locked in the contract (recoverable only via the honest owner's `recoverERC20`, which cannot identify how much was "lost" vs pending). This is a permanent freezing/loss of unclaimed yield quantified as `rewardRate * (t_stake − t_emptyStart)`. Since `IdleCDO` production code calls both `stakeFor` (fee tranche tokens for `feeReceiver`) and `depositReward`, every reward notification that lands while no tranche tokens are staked — e.g., right after deployment, after a full exit, or when the CDO itself has not yet staked fee tokens — forfeits a deterministic fraction of the emission.

### Likelihood Explanation
No attacker action is required; the loss is triggered purely by honest sequencing (`depositReward` while supply is zero, or the final `withdraw` mid-period). A passive unprivileged staker merely determines *when* the timestamp next advances. Any period that starts or continues with zero staked supply loses rewards proportional to the empty duration; with `rewardsDuration = 1 days`, even a few empty hours forfeits a large share of that period's rewards.

### Recommendation
Do not advance `lastUpdateTime` when `_totalSupply == 0`. Concretely, in `updateReward`, set `lastUpdateTime = lastTimeRewardApplicable()` only when `_totalSupply != 0` (or equivalently freeze the clock at the moment supply hits zero and resume on the next stake). Alternatively, restart the vesting window or roll unvested rewards into `rewardRate` on the next `depositReward`. Apply the same fix to `DelegateStakingRewardsIDLE.sol`.

### Proof of Concept
Foundry test sketch against `StakingRewards` (initialized with `rewardsDistribution = address(this)`, `rewardsDuration = 1 days`):

```solidity
function test_rewardsLostWhileSupplyZero() public {
    // deploy + initialize StakingRewards with rewardToken R, stakingToken S
    deal(address(R), address(this), 1 days * 1e18); // rewardRate-compatible
    R.approve(address(sr), type(uint256).max);

    // 1. Rewards notified while nobody is staked
    sr.depositReward(address(0), 1e18); // rewardRate = 1e18 / 1 days

    // 2. Half the period elapses with totalSupply == 0
    skip(12 hours);

    // 3. Alice becomes the first staker; updateReward pushes lastUpdateTime to now
    deal(address(S), alice, 100e18);
    vm.startPrank(alice);
    S.approve(address(sr), 100e18);
    sr.stake(100e18);
    vm.stopPrank();

    // 4. Remaining half of the period elapses fully staked
    skip(12 hours);

    // Alice earns only ~0.5e18; the first 12h of emission (~0.5e18) is unclaimable by anyone
    uint256 earned = sr.earned(alice);
    assertApproxEqAbs(earned, 0.5e18, 1e6);
    assertLt(earned, sr.getRewardForDuration()); // ~half the deposit is lost
}
```

Variant for mid-period emptying: stake, let `depositReward` run, `withdraw` fully at T, skip Δ, re-stake — loss equals `rewardRate * Δ` and repeats on every full exit, so each period leaks deterministically whenever supply touches zero.

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

**File:** contracts/StakingRewards.sol (L129-150)
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

    // Ensure the provided reward amount is not more than the balance in the contract.
    // This keeps the reward rate in the right range, preventing overflows due to
    // very high values of rewardRate in the earned and rewardsPerToken functions;
    // Reward + leftover must be less than 2^256 / 10^18 to avoid overflow.
    uint balance = rewardsToken.balanceOf(address(this));
    require(rewardRate <= balance / rewardsDuration, "Provided reward too high");

    lastUpdateTime = block.timestamp;
    periodFinish = block.timestamp + rewardsDuration;
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

**File:** contracts/DelegateStakingRewardsIDLE.sol (L69-74)
```text
  function rewardPerToken() public view returns (uint256) {
    if (_totalSupply == 0) {
      return rewardPerTokenStored;
    }
    return rewardPerTokenStored + ((lastTimeRewardApplicable() - lastUpdateTime) * rewardRate * 1e18 / _totalSupply);
  }
```

**File:** contracts/DelegateStakingRewardsIDLE.sol (L206-214)
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
