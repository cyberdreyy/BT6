### Title
Removed Convex reward tokens cannot be harvested, permanently freezing accrued rewards - ([File: contracts/strategies/convex/ConvexBaseStrategy.sol])

### Summary
`removeReward` deletes a reward token and its router/path configuration without first claiming or transferring its accrued balance. Subsequent `redeemRewards` calls still claim all Convex rewards from `rewardPool`, but only iterate the current `convexRewards` array, so the removed token remains stranded in the strategy. [1](#0-0) [2](#0-1) 

### Finding Description
`ConvexBaseStrategy.redeemRewards` calls `IBaseRewardPool(rewardPool).getReward()`, which transfers all currently accrued Convex reward tokens to the strategy. It then iterates `convexRewards`, reads each token's strategy balance, approves its configured router, and swaps the balance through the configured WETH path. [3](#0-2) 

`removeReward` replaces `convexRewards` with a new array omitting the supplied token and deletes both `rewardRouter[_reward]` and `reward2WethPath[_reward]`. [1](#0-0) 

There is no claim, transfer, claim-only state, or residual-balance check before removal. After removal, future calls to `redeemRewards` continue to pull newly accrued amounts of that token through `getReward()`, but never select the token for conversion because it is absent from `convexRewards`. [4](#0-3) 

The IdleCDO harvest path relies on the strategy's `getRewardTokens()` result to determine which claimed assets to sell for the underlying token. Since `ConvexBaseStrategy.getRewardTokens()` returns an empty array, the CDO-level reward-selling loop has no route through which a removed reward can be processed either. [5](#0-4) [6](#0-5) 

### Impact Explanation
All unclaimed or newly claimed balances of a removed reward token remain inside the strategy and are excluded from the normal harvest and yield-distribution flow. Tranche holders therefore permanently lose the value of those accrued rewards unless the owner manually recovers the token through the emergency `transferToken` function. [7](#0-6) 

The loss equals the removed token's accrued balance at removal, plus any additional amounts of that reward received by later `getReward()` calls. This breaks the yield-accounting invariant that rewards earned by strategy deposits are either distributed to tranche holders or remain claimable through an owner-independent path. [2](#0-1) [8](#0-7) 

### Likelihood Explanation
Reward removal is an intentional administrative operation, but no malicious or compromised privileged actor is required. An operator can reasonably remove a reward token when its liquidity path becomes unavailable or the token is deprecated while accrued rewards still exist in Convex's reward pool. `removeReward` contains no zero-balance check or warning condition, so a routine removal can strand the full accrued balance. [1](#0-0) 

The issue is reachable during normal operation: `harvest` calls `redeemRewards`, while `redeemRewards` calls `getReward()` before filtering rewards through the mutable `convexRewards` list. [9](#0-8) [10](#0-9) 

### Recommendation
Before removing a reward, call the external reward claim and revert if `IERC20Detailed(_reward).balanceOf(address(this)) != 0`. Alternatively, retain removed rewards in a separate claim-only list so `getReward()` proceeds can still be swept and converted, while preventing the token from participating in APR calculations or new reward configuration. [4](#0-3) [1](#0-0) 

### Proof of Concept

```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.10;

import "forge-std/Test.sol";
import "../contracts/strategies/convex/ConvexBaseStrategy.sol";
import "../contracts/interfaces/IERC20Detailed.sol";
import "../contracts/interfaces/convex/IBaseRewardPool.sol";

contract RemovedConvexRewardTest is Test {
    ConvexBaseStrategy internal strategy;
    address internal owner;
    address internal cdo;
    address internal rewardPool;
    address internal removedReward;

    function setUp() public {
        // Fork mainnet and initialize one concrete Convex strategy.
        // owner is the configured strategy owner.
        // cdo is the strategy's whitelistedCDO.
        // rewardPool is the strategy's Convex reward pool.
        // removedReward is initially present in strategy.convexRewards().
    }

    function test_RemovedRewardIsClaimedButNeverConverted() public {
        uint256 claimable = IBaseRewardPool(rewardPool).earned(address(strategy));
        assertGt(claimable, 0);

        // Honest administrative removal. No harvest or sweep occurs first.
        vm.prank(owner);
        strategy.removeReward(removedReward);
        assertEq(strategy.rewardRouter(removedReward), address(0));
        assertEq(strategy.reward2WethPath(removedReward).length, 0);

        bytes memory data = abi.encode(
            new uint256[](strategy.convexRewards().length),
            new bool[](strategy.convexRewards().length),
            uint256(0),
            uint256(0)
        );

        // getReward() transfers the removed reward to the strategy, but the
        // loop only visits the post-removal convexRewards array.
        vm.prank(cdo);
        strategy.redeemRewards(data);

        assertGt(
            IERC20Detailed(removedReward).balanceOf(address(strategy)),
            0
        );
    }
}
```

### Citations

**File:** contracts/strategies/convex/ConvexBaseStrategy.sol (L278-325)
```text
        address[] memory _convexRewards = convexRewards;
        // +2 for converting rewards to depositToken and then Curve LP Token
        _balances = new uint256[](_convexRewards.length + 2); 
        // decode params from _extraData to get the min amount for each convexRewards
        uint256[] memory _minAmountsWETH = new uint256[](_convexRewards.length);
        bool[] memory _skipSell = new bool[](_convexRewards.length);
        uint256 _minDepositToken;
        uint256 _minLpToken;
        (_minAmountsWETH, _skipSell, _minDepositToken, _minLpToken) = abi.decode(_extraData, (uint256[], bool[], uint256, uint256));

        IBaseRewardPool(rewardPool).getReward();

        address _reward;
        IERC20Detailed _rewardToken;
        uint256 _rewardBalance;
        IUniswapV2Router02 _router;

        for (uint256 i = 0; i < _convexRewards.length; i++) {
            if (_skipSell[i]) continue;

            _reward = _convexRewards[i];

            // get reward balance and safety check
            _rewardToken = IERC20Detailed(_reward);
            _rewardBalance = _rewardToken.balanceOf(address(this));

            if (_rewardBalance == 0) continue;

            _router = IUniswapV2Router02(
                rewardRouter[_reward]
            );

            // approve to v2 router
            _rewardToken.safeApprove(address(_router), 0);
            _rewardToken.safeApprove(address(_router), _rewardBalance);

            address[] memory _reward2WethPath = reward2WethPath[_reward];
            uint256[] memory _res = new uint256[](_reward2WethPath.length);
            _res = _router.swapExactTokensForTokens(
                _rewardBalance,
                _minAmountsWETH[i],
                _reward2WethPath,
                address(this),
                block.timestamp
            );
            // save in returned value the amount of weth receive to use off-chain
            _balances[i] = _res[_res.length - 1];
        }
```

**File:** contracts/strategies/convex/ConvexBaseStrategy.sol (L401-406)
```text
    /// @return rewardTokens tokens array of reward token addresses
    function getRewardTokens()
        external
        view
        override
        returns (address[] memory rewardTokens) {}
```

**File:** contracts/strategies/convex/ConvexBaseStrategy.sol (L423-429)
```text
    function transferToken(
        address _token,
        uint256 value,
        address _to
    ) external onlyOwner nonReentrant {
        IERC20Detailed(_token).safeTransfer(_to, value);
    }
```

**File:** contracts/strategies/convex/ConvexBaseStrategy.sol (L479-495)
```text
    function removeReward(address _reward) external onlyOwner {
        address[] memory _newConvexRewards = new address[](
            convexRewards.length - 1
        );

        uint256 currentI = 0;
        for (uint256 i = 0; i < convexRewards.length; i++) {
            if (convexRewards[i] == _reward) continue;
            _newConvexRewards[currentI] = convexRewards[i];
            currentI += 1;
        }

        convexRewards = _newConvexRewards;

        delete rewardRouter[_reward];
        delete reward2WethPath[_reward];
    }
```

**File:** contracts/IdleCDO.sol (L641-668)
```text
  function _sellAllRewards(IIdleCDOStrategy _strategy, uint256[] memory _sellAmounts, uint256[] memory _minAmount, bool[] memory _skipReward, bytes memory _extraData)
    internal virtual
    returns (uint256[] memory _soldAmounts, uint256[] memory _swappedAmounts, uint256 _totSold) {
    // Fetch state variables once to save gas
    // get all rewards addresses
    address[] memory _rewards = _strategy.getRewardTokens();
    address _rewardToken;
    bytes[] memory _paths = new bytes[](_rewards.length);
    if (_extraData.length > 0) {
      _paths = abi.decode(_extraData, (bytes[]));
    }
    uint256 rewardsLen = _rewards.length;
    // Initialize the return array, containing the amounts received after swapping reward tokens
    _soldAmounts = new uint256[](rewardsLen);
    _swappedAmounts = new uint256[](rewardsLen);
    // loop through all reward tokens
    for (uint256 i; i < rewardsLen; ++i) {
      _rewardToken = _rewards[i];
      // check if it should be sold or not
      if (_skipReward[i]) { continue; }
      // do not sell stkAAVE but only AAVE if present
      if (_rewardToken == stkAave) {
        _rewardToken = AAVE;
      }
      // Market sell _rewardToken in this contract for _token
      (_soldAmounts[i], _swappedAmounts[i]) = _sellReward(_rewardToken, _paths[i], _sellAmounts[i], _minAmount[i]);
      _totSold += _swappedAmounts[i];
    }
```

**File:** contracts/IdleCDO.sol (L770-783)
```text
      if (!_skipFlags[0]) {
        // Redeem all rewards associated with the strategy
        _res[2] = _strategy.redeemRewards(_extraData[0]);
        // Sell rewards
        (_res[0], _res[1], _totSold) = _sellAllRewards(_strategy, _sellAmounts, _minAmount, _skipReward, _extraData[1]);
      }
      // update last saved harvest block number
      latestHarvestBlock = block.number;
      // update harvested rewards value (avoid setting it to 0 to save some gas)
      harvestedRewards = _totSold == 0 ? 1 : _totSold;

      // split converted rewards if any and update tranche prices
      // NOTE: harvested rewards won't be counted directly but released over time
      _updateAccounting();
```
