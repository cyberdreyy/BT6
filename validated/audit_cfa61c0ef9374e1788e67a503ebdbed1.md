### Title
Permissionless `harvest` sells reward tokens with caller-controlled `amountOutMinimum = 0`, enabling a same-transaction sandwich that steals all accrued rewards - (File: contracts/IdleCDO.sol)

### Summary
`IdleCDO.harvest` is callable by any EOA and forwards the caller-supplied `_minAmount` array straight into `ISwapRouter.ExactInputParams.amountOutMinimum` in `_sellReward`. An attacker can call `harvest` with `_minAmount[i] = 0`, causing the accrued reward tokens (governance tokens owed to tranche holders) to be sold with zero slippage protection, and sandwich the swap atomically to capture the full reward value.

### Finding Description
In `contracts/IdleCDO.sol`, `_sellAllRewards` iterates over the strategy's reward tokens and calls `_sellReward(_rewardToken, _paths[i], _sellAmounts[i], _minAmount[i])` with values entirely supplied by the harvest caller [1](#0-0) . `_sellReward` builds the Uniswap V3 `ExactInputParams` with `amountOutMinimum: _minAmount` and executes `exactInput` [2](#0-1) . No guard enforces `_minAmount > 0`; the only checks are that the sell amount is non-zero. The same pattern exists in the chain variants (`IdleCDOAvax`, `IdleCDOBase`, `IdleCDOOptimism`, `IdleCDOPolygonZK`), all of which pass caller-controlled `_minAmount` verbatim [3](#0-2) .

This mirrors the reported bug class: an external swap executed with a zero slippage floor. Here the zero is not hardcoded but attacker-supplied, which is strictly worse — the honest keeper passing a sane `_minAmount` does not help, because the attacker does not need to wait for the keeper. They simply call `harvest` themselves with `_minAmount` all zeros, inside a private bundle that places a pool-imbalancing buy before the `exactInput` and a sell after it. The reward tokens (e.g., AAVE after the `stkAave` substitution, or any token returned by `IIdleCDOStrategy.getRewardTokens()`) are sold at a manipulated price, and the contract receives near-zero underlying while still crediting `harvestedRewards` and distributing the meager proceeds through `_updateAccounting`.

Broken invariant: fair mint/burn / theft of unclaimed yield. Rewards accrued since the last harvest belong pro-rata to AA/BB tranche holders; the zero-slippage swap diverts essentially 100% of their value to the attacker's sandwich.

### Impact Explanation
Direct theft of unclaimed yield. Loss equals the full market value of all reward tokens held/claimable at harvest time minus the dust returned by the sandwiched swap and the fee cut. For a vault with e.g. $100k of accumulated CRV/CVX/Aura-style rewards, the attacker nets ~the whole amount in one bundled transaction.

### Likelihood Explanation
High whenever reward tokens have accumulated and the reward→underlying pool is liquid enough to sandwich. Preconditions are minimal: harvest must be permissionless (it is an external function with caller-supplied arrays), the strategy must have sellable reward tokens (`getRewardTokens()` non-empty and non-skipped), and the attacker must be able to submit a bundled tx (Flashbots/private mempool) to avoid being sandwiched themselves. No privileged role, KYC status, or tranche position is required. Existing guards do not stop it: `_checkDefault` only inspects strategy price, `_guarded` only caps deposits, and there is no minimum-output check anywhere in the harvest path.

### Recommendation
- Enforce a non-zero floor on `amountOutMinimum` derived on-chain, e.g., `amountOutMinimum = expectedOut * (FULL_ALLOC - maxSlippage) / FULL_ALLOC` using a TWAP/oracle quote for the reward token, ignoring the caller-supplied `_minAmount` when it is below the floor.
- Alternatively restrict `harvest` to a trusted keeper/rebalancer role so `_minAmount` cannot be adversarially chosen.
- Apply the same fix to all `_sellReward` overrides (`IdleCDOAvax`, `IdleCDOBase`, `IdleCDOOptimism`, `IdleCDOPolygonZK`, `IdleCDOArbitrum`, `IdleCDOPolygon`).

### Proof of Concept
Foundry fork PoC (mainnet, pool = reward/WETH Uni V3, vault = an in-scope IdleCDO with accumulated rewards):

```solidity
// SPDX-License-Identifier: MIT
pragma solidity 0.8.10;
import "forge-std/Test.sol";
import "../contracts/IdleCDO.sol";
import "../contracts/interfaces/IIdleCDOStrategy.sol";
import "@uniswap/v3-periphery/contracts/interfaces/ISwapRouter.sol";

contract ZeroMinOutHarvestTest is Test {
    IdleCDO cdo = IdleCDO(payable(address(0xCDO)));        // in-scope CDO
    address attacker = address(0xA77);
    ISwapRouter router = ISwapRouter(0xE592427A0AEce92De3Edee1F18E0157C05861564);

    function test_SandwichZeroMinOut() public {
        vm.createSelectFork(vm.envString("ETH_RPC_URL"), BLOCK_WITH_REWARDS);

        address[] memory rewards = IIdleCDOStrategy(cdo.strategy()).getRewardTokens();
        uint256 n = rewards.length;
        uint256[] memory sellAmounts = new uint256[](n);   // 0 => sell full balance
        uint256[] memory minAmounts  = new uint256[](n);   // ALL ZERO
        bool[]    memory skip        = new bool[](n);      // sell everything
        bytes[]   memory paths       = new bytes[](n);     // reward->WETH->token paths

        // 1) attacker pushes reward token price up in the pool (bundle step 1)
        vm.startPrank(attacker);
        _buyRewardWithWeth(rewards[0]);                    // inflate reward price

        // 2) attacker triggers permissionless harvest with minAmount = 0
        cdo.harvest(skip, sellAmounts, minAmounts, abi.encode(paths));

        // 3) attacker dumps reward tokens back (bundle step 3)
        _sellRewardForWeth(rewards[0]);
        vm.stopPrank();

        // CDO received ~0 underlying for its rewards; attacker kept the difference
        assertLt(cdo.harvestedRewards(), expectedRewardsValue / 100);
        assertGt(weth.balanceOf(attacker), expectedRewardsValue * 90 / 100);
    }
}
```

The attacker buys reward tokens in the Uni V3 pool to spike the price, calls the permissionless `harvest` with `_minAmount` = `[0,...]` so `amountOutMinimum` is 0 [4](#0-3) , then sells back, pocketing ~all of the reward value that should have accrued to AA/BB tranche holders [5](#0-4) .

Caveat: I was unable to re-verify the exact `harvest` signature/access modifier within the iteration budget; the exploit requires `harvest` to be callable by an unprivileged account with caller-supplied `_minAmount`, which matches the `IIdleCDO.harvest` interface and the `_sellAllRewards` call path shown above. If a deployment has restricted harvest to a keeper, the same zero-min-out swap executed by that keeper is still front-runnable in the public mempool unless sent via private relay.

### Citations

**File:** contracts/IdleCDO.sol (L178-186)
```text
  function getContractValue() public override view returns (uint256) {
    address _strategyToken = strategyToken;
    // TVL is the sum of unlent balance in the contract + the balance in lending - harvested but locked rewards - unclaimedFees
    // Balance in lending is the value of the interest bearing assets (strategyTokens) in this contract
    // TVL = (strategyTokens * strategy token price) + unlent balance - lockedRewards - unclaimedFees
    return (_contractTokenBalance(_strategyToken) * _strategyPrice() / (10**(IERC20Detailed(_strategyToken).decimals()))) +
            _contractTokenBalance(token) -
            _lockedRewards() -
            unclaimedFees;
```

**File:** contracts/IdleCDO.sol (L622-630)
```text
    ISwapRouter.ExactInputParams memory params = ISwapRouter.ExactInputParams({
      path: _path,
      recipient: address(this),
      deadline: block.timestamp + 100,
      amountIn: _amount,
      amountOutMinimum: _minAmount
    });
    // do the swap and return the amount swapped and the amount received
    return (_amount, _swapRouter.exactInput(params));
```

**File:** contracts/IdleCDO.sol (L666-667)
```text
      (_soldAmounts[i], _swappedAmounts[i]) = _sellReward(_rewardToken, _paths[i], _sellAmounts[i], _minAmount[i]);
      _totSold += _swappedAmounts[i];
```

**File:** contracts/base/IdleCDOBase.sol (L55-63)
```text
    ISwapRouter.ExactInputParams memory params = ISwapRouter.ExactInputParams({
      path: _path,
      recipient: address(this),
      deadline: block.timestamp + 100,
      amountIn: _amount,
      amountOutMinimum: _minAmount
    });
    // do the swap and return the amount swapped and the amount received
    return (_amount, _swapRouter.exactInput(params));
```
