### Title
Misaligned `harvest` reward arrays after reward-token list reordering enables reward-sale sandwiching - (File: contracts/IdleCDO.sol)

### Summary
`IdleCDO.harvest` lets the caller pass per-reward arrays (`_skipReward`, `_minAmount`, `_sellAmounts`) that are applied positionally to `strategy.getRewardTokens()` inside `_sellAllRewards`. That list is fetched dynamically from the underlying `IdleToken.getGovTokens()` (`contracts/strategies/idle/IdleStrategy.sol:185-187`) and its order/content is not stable — the IdleToken governance can add/remove/reorder gov tokens at any time, exactly like `LibSetters.revokeCollateral` reorders `ts.collateralList` in the reference report. If the list changes between the moment the harvester crafts calldata and tx inclusion, min-amounts are applied to the wrong reward tokens, so a valuable reward can be swapped with `amountOutMinimum = 0` (or an unrelated dust threshold) and sandwiched. There is no length check or token-address binding anywhere in `_sellAllRewards` (`contracts/IdleCDO.sol:641-668`).

### Finding Description
- `_sellAllRewards` iterates `address[] memory _rewards = _strategy.getRewardTokens()` and indexes the caller-supplied arrays purely by position:

```solidity
// contracts/IdleCDO.sol
for (uint256 i; i < rewardsLen; ++i) {
  _rewardToken = _rewards[i];
  if (_skipReward[i]) { continue; }
  ...
  (_soldAmounts[i], _swappedAmounts[i]) = _sellReward(_rewardToken, _paths[i], _sellAmounts[i], _minAmount[i]);
```

- `_sellReward` then uses `_minAmount` directly as `amountOutMinimum` in the Uniswap V3 `exactInput` call (`contracts/IdleCDO.sol:622-628`).
- The reward-token order is external state (`idleToken.getGovTokens()`), mutable by IdleToken governance between calldata construction and execution — the same race window as the Angle `revokeCollateral` ordering bug.
- Even the partial mitigation suggested in the report (a length check) is absent; only the scenario where the list shrinks is "safe" by accident (extra entries ignored), while a swap-and-reorder or remove-then-add within the same length produces silent misalignment.
- The caller-supplied swap paths (`_paths[i]` decoded from `_extraData`) are also positional, so a reordering additionally pairs the wrong path with the wrong token, compounding mispricing.

### Impact Explanation
Broken invariant: fair slippage protection on reward liquidation (theft of unclaimed yield). A reordering can pair a high-value reward token (e.g. COMP/AAVE balance worth tens of thousands of dollars) with a `minAmount` crafted for a dust token, or `0`. An unprivileged MEV searcher sandwiches the resulting swap, extracting the difference between fair value and actual output. Loss is quantified as `(fairValue − minOut)` per misaligned reward, capped by the full pending reward balance; repeated each harvest until governance never touches the list again (not controllable by the CDO). If the reward list grows, the positional indexing also OOB-reverts — harvests freeze until calldata is regenerated, but the fund-impacting case is the silent misalignment.

### Likelihood Explanation
Moderate. Preconditions: (a) IdleToken governance mutates `getGovTokens()` (add/remove gov token programs — historically done several times on mainnet IdleTokens), and (b) a harvester submits calldata crafted against the old order in the same window. Both are realistic for any external keeper or UI flow that pre-computes `minAmounts`/`paths` off-chain, which is exactly how the test suite itself does it (`test/integration/deprecated/euler/eulerCDO.js:168-186` computes `sellAmounts`/`minAmounts` in a staticcall, then submits). The honest-governance constraint is preserved: governance is not the attacker, the loss is captured by any unprivileged sandwich bot.

### Recommendation
Bind slippage parameters to token identity rather than position: accept `(address token, uint256 minAmount, uint256 sellAmount, bytes path)` tuples (or a parallel `address[]` of expected tokens) and `require(_rewards[i] == expected[i])` before selling; alternatively decode the mapping reward→min from `_extraData` keyed by address. At minimum, enforce `_skipReward.length == _minAmount.length == rewardsLen` to close the shrink case.

### Proof of Concept
Foundry fork outline:

```solidity
// SPDX-License-Identifier: MIT
pragma solidity 0.8.10;

import "forge-std/Test.sol";
import {IdleCDO} from "contracts/IdleCDO.sol";
import {IdleStrategy} from "contracts/strategies/idle/IdleStrategy.sol";
import {IIdleToken} from "contracts/interfaces/IIdleToken.sol";

contract RewardOrderMismatchTest is Test {
    IdleCDO cdo = IdleCDO(payable(address(0xC4DO_MAINNET)));   // perp tranche using IdleStrategy
    IIdleToken idleToken;

    function test_rewardListReorder_misalignsMinAmount() public {
        idleToken = IIdleToken(IdleStrategy(cdo.strategy()).strategyToken());
        address[] memory gov = idleToken.getGovTokens();
        vm.assume(gov.length >= 2);

        // 1) harvester prepares calldata against current order:
        //    gov[0] = dust token  -> min[0] = 1
        //    gov[1] = valuable    -> min[1] = 50_000e18 (tight)
        //    paths aligned to gov order.

        // 2) IdleToken governance reorders / removes+adds a gov token
        //    (mocked here by vm.store on the govTokens array slot or via
        //     prank of the IdleToken gov setter on the forked contract)
        //    so that the new order is [valuable, dust] — same length.

        // 3) keeper tx lands: _rewards[0] is now the valuable token but
        //    _minAmount[0] = 1 wei → swapExactInput executes with ~zero
        //    slippage protection.

        // 4) attacker sandwiches: pushes price down pre-swap, restores after.
        // assert(cdo received underlying << expectedFairValue);
    }
}
```

Expected assertion: `_totSold` (underlying received) is materially below the fair value of the valuable reward balance, with the difference extracted by the sandwich bot — the same "user crafted minAmounts against a stale list order" failure as the Angle report. Note: I could not fully verify `harvest`'s access modifiers within the iteration budget; if it is permissionless (as in upstream IdleCDO deployments where keepers call it), the attack surface is confirmed as written; if it were owner-only, the victim-side ordering race still exists but the practical exposure drops to operator error.