### Title
Clearpool Polygon zkEVM strategy accrues CPOOL rewards that cannot be claimed or distributed - (File: `contracts/polygon-zk/strategies/clearpool/IdleClearpoolStrategyPolygonZK.sol`)

### Summary
`IdleClearpoolStrategyPolygonZK` includes CPOOL emissions in its reported APR, but its `redeemRewards` implementation is effectively empty and the only Clearpool reward-claim call is commented out. [1](#0-0)  Consequently, CPOOL rewards earned by the strategy remain in Clearpool's reward system and are never transferred to the IdleCDO for conversion and distribution to tranche holders. [2](#0-1) 

### Finding Description
The corresponding mainnet Clearpool strategy claims rewards by calling `IPoolMaster(cpToken).factory().withdrawReward(pools)` and forwarding the resulting `govToken` balance to the IdleCDO. [3](#0-2) 

In the Polygon zkEVM implementation, that logic is commented out, so `redeemRewards` returns an empty array without interacting with Clearpool. [1](#0-0)  The strategy nevertheless reads `rewardPerSecond()` and adds the resulting CPOOL-denominated return to `getApr`, indicating that reward emissions are part of the strategy's expected yield. [2](#0-1) 

`getRewardTokens()` returns only MATIC, so `IdleCDO.harvest` has no opportunity to sell CPOOL even if the CDO already knows the token address separately. [4](#0-3)  `redeemRewards` is restricted by `onlyIdleCDO`, meaning tranche holders cannot invoke it directly, and even the authorized CDO receives an empty result when it does. [5](#0-4)  Unlike the Optimism variant, this contract has no reward-token rescue function. [6](#0-5) 

### Impact Explanation
All CPOOL incentives attributable to the strategy's Clearpool position are uncollectable under the deployed implementation. [1](#0-0)  The loss is the strategy's pro-rata share of pool emissions, approximately:

```text
CPOOL_loss ≈ rewardPerSecond * elapsedSeconds * strategyCpTokens / poolTotalSupply
```

That amount grows with time and deposited principal while `rewardPerSecond > 0`. [2](#0-1)  Because the rewards cannot reach the IdleCDO, they cannot be converted into the underlying and reflected in tranche prices or distributed to AA and BB holders. [7](#0-6) 

### Likelihood Explanation
The condition is deterministic whenever the selected Clearpool pool has a nonzero `rewardPerSecond` and the strategy has a nonzero share of that pool. [8](#0-7)  No attacker manipulation is required: ordinary deposits accrue incentives, while every authorized harvest leaves those incentives unclaimed. [9](#0-8)  If a deployment uses a pool with `rewardPerSecond == 0`, the quantified loss is zero for that deployment. [10](#0-9) 

### Recommendation
Implement CPOOL claiming in `redeemRewards` using the same pattern as the mainnet Clearpool strategy: construct the pool array, call `factory().withdrawReward(pools)`, measure the resulting CPOOL balance, and transfer it to `idleCDO`. [3](#0-2)  Return both CPOOL and any separately distributed MATIC balance, and expose both tokens through `getRewardTokens()` so `IdleCDO.harvest` can sell them according to the supplied swap parameters. [4](#0-3) 

### Proof of Concept
The following fork test demonstrates the broken path against a deployed Polygon zkEVM strategy. It requires a pool where `rewardPerSecond > 0`.

```solidity
// SPDX-License-Identifier: MIT
pragma solidity 0.8.10;

import "forge-std/Test.sol";
import "../../contracts/polygon-zk/strategies/clearpool/IdleClearpoolStrategyPolygonZK.sol";
import "../../contracts/interfaces/clearpool/IPoolMaster.sol";
import "../../contracts/interfaces/IERC20Detailed.sol";

contract ClearpoolZkUnclaimableRewardsTest is Test {
    address internal constant CPOOL =
        0xc3630b805F10E91c2de084Ac26C66bCD91F3D3fE;

    IdleClearpoolStrategyPolygonZK internal strategy;

    function setUp() public {
        vm.createSelectFork(vm.envString("POLYGON_ZK_RPC_URL"));

        // Address of the deployed upgradeable strategy being tested.
        strategy = IdleClearpoolStrategyPolygonZK(vm.envAddress("STRATEGY"));
    }

    function testCpoolRewardsCannotBeClaimed() public {
        IPoolMaster pool = IPoolMaster(strategy.cpToken());
        uint256 rewardSpeed = pool.rewardPerSecond();

        // This finding is active only for incentivized pools.
        vm.assume(rewardSpeed > 0);

        address cdo = strategy.idleCDO();
        uint256 cpoolBefore = IERC20Detailed(CPOOL).balanceOf(cdo);
        uint256 strategyCpoolBefore =
            IERC20Detailed(CPOOL).balanceOf(address(strategy));

        uint256 poolApr =
            pool.getSupplyRate() * strategy.YEAR() * 100;
        uint256 advertisedApr = strategy.getApr();

        // The strategy advertises CPOOL rewards as yield.
        assertGt(advertisedApr, poolApr);

        // `harvest` calls the strategy as `idleCDO`.
        vm.prank(cdo);
        uint256[] memory rewards = strategy.redeemRewards("");

        // The implementation contains no call and returns an empty array.
        assertEq(rewards.length, 0);
        assertEq(
            IERC20Detailed(CPOOL).balanceOf(cdo),
            cpoolBefore
        );
        assertEq(
            IERC20Detailed(CPOOL).balanceOf(address(strategy)),
            strategyCpoolBefore
        );

        // CPOOL is not exposed as a reward token for the CDO sale path.
        address[] memory rewardTokens = strategy.getRewardTokens();
        for (uint256 i = 0; i < rewardTokens.length; ++i) {
            assertTrue(rewardTokens[i] != CPOOL);
        }
    }
}
```

### Citations

**File:** contracts/polygon-zk/strategies/clearpool/IdleClearpoolStrategyPolygonZK.sol (L106-124)
```text
    /// @notice redeem the rewards. Claims reward as per the _extraData
    /// @return rewards amount of reward that is deposited to vault
    function redeemRewards(bytes calldata)
        external
        override
        onlyIdleCDO
        returns (uint256[] memory rewards)
    {
        // address pool = cpToken;
        // address[] memory pools = new address[](1);
        // pools[0] = pool;
        // IPoolMaster(pool).factory().withdrawReward(pools);

        // rewards = new uint256[](2); // 2 rewards CPOOL and MATIC (manually sent to CDO)
        // rewards[0] = IERC20Detailed(CPOOL).balanceOf(address(this));
        // if (rewards[0] != 0) {
        //     IERC20Detailed(CPOOL).safeTransfer(msg.sender, rewards[0]);
        // }
    }
```

**File:** contracts/polygon-zk/strategies/clearpool/IdleClearpoolStrategyPolygonZK.sol (L138-149)
```text
    /// @notice Get the reward token
    /// @return array of reward token
    function getRewardTokens()
        external
        view
        override
        returns (address[] memory)
    {
        address[] memory govTokens = new address[](1);
        govTokens[0] = MATIC;
        return govTokens;
    }
```

**File:** contracts/polygon-zk/strategies/clearpool/IdleClearpoolStrategyPolygonZK.sol (L151-171)
```text
    function getApr() external view returns (uint256) {
        // CPOOL per second (clearpool's contract has typo)
        IPoolMaster _cpToken = IPoolMaster(cpToken);
        uint256 rewardSpeed = _cpToken.rewardPerSecond();
        uint256 rewardRate;
        if (rewardSpeed > 0) {
            // Underlying tokens equivalent of rewards
            uint256 annualRewards = (rewardSpeed *
                YEAR *
                _tokenToUnderlyingRate()) / 10**18;
            // Pool's TVL as underlying tokens
            uint256 poolTVL = (IERC20Detailed(address(_cpToken)).totalSupply() *
                _cpToken.getCurrentExchangeRate()) / 10**18;
            // Annual rewards rate (as clearpool's 18-precision decimal)
            rewardRate = (annualRewards * 10**18) / poolTVL;
        }

        // Pool's annual interest rate
        uint256 poolRate = _cpToken.getSupplyRate() * YEAR;

        return (poolRate + rewardRate) * 100;
```

**File:** contracts/polygon-zk/strategies/clearpool/IdleClearpoolStrategyPolygonZK.sol (L293-304)
```text
    /// @notice allow to update whitelisted address
    function setWhitelistedCDO(address _cdo) external onlyOwner {
        require(_cdo != address(0), "IS_0");
        idleCDO = _cdo;
    }

    /// @notice Modifier to make sure that caller os only the idleCDO contract
    modifier onlyIdleCDO() {
        require(idleCDO == msg.sender, "Only IdleCDO can call");
        _;
    }
}
```

**File:** contracts/strategies/clearpool/IdleClearpoolStrategy.sol (L100-116)
```text
    /// @notice redeem the rewards. Claims reward as per the _extraData
    /// @return rewards amount of reward that is deposited to vault
    function redeemRewards(bytes calldata)
        external
        override
        onlyIdleCDO
        returns (uint256[] memory rewards)
    {
        address pool = cpToken;
        address[] memory pools = new address[](1);
        pools[0] = pool;
        IPoolMaster(pool).factory().withdrawReward(pools);

        rewards = new uint256[](1);
        rewards[0] = IERC20Detailed(govToken).balanceOf(address(this));
        IERC20Detailed(govToken).safeTransfer(msg.sender, rewards[0]);
    }
```

**File:** contracts/IdleCDO.sol (L770-784)
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
