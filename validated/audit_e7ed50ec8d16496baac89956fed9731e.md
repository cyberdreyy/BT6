### Title
Harvest reward sell can dump the CDO's entire underlying balance when a reward token equals the vault asset - ([File: contracts/IdleCDO.sol])

### Summary
`IdleCDO.harvest` → `_sellAllRewards` → `_sellReward` market-sells reward tokens listed by `IIdleCDOStrategy.getRewardTokens()` on Uniswap V3. When `_sellAmounts[i] == 0` (the documented default), `_sellReward` swaps the **entire contract balance** of that token (`_contractTokenBalance(_rewardToken)`), with no guard preventing the sold token from being the vault's own `token` (underlying) or `strategyToken`. This is the same bug class as the Notional report: there is no `_isInvalidRewardToken`-equivalent protecting core vault holdings from being sold during reinvestment.

### Finding Description
In `_sellAllRewards` (IdleCDO.sol:641-669) each entry of `strategy.getRewardTokens()` is sold via `_sellReward`. In `_sellReward` (IdleCDO.sol:607-631) a zero `_amount` resolves to `IERC20Detailed(_rewardToken).balanceOf(address(this))` — the CDO's full balance of that token, which includes the unlent reserve, freshly deposited user funds awaiting deployment, and prior swap proceeds — not just the rewards redeemed in the same `harvest` call.

Strategies such as `MetaMorphoStrategy` return an owner-configured `rewardTokens` array (MetaMorphoStrategy.sol:29, 76-78, 120-122, 176-178). MetaMorpho/URD campaigns legitimately distribute rewards denominated in the vault's underlying asset (e.g., USDC incentives on a USDC MetaMorpho vault), so listing `token` as a reward token is a plausible, honest configuration needed to claim real yield. Once listed, every `harvest` with `_sellAmounts[i] == 0` routes the CDO's *whole* underlying balance through UniV3 with a `_minAmount` sized by the keeper off-chain for the *claimed reward amount only*.

### Impact Explanation
Any unprivileged user can deposit underlying into the CDO in the same block/epoch phase before the keeper's `harvest`. The keeper's `_minAmount[i]` is computed from the merkle `claimable` amount, while the swap dumps `claimable + reserve + pending deposits`. The excess is sold at a min-out that only covers the reward portion, so arbitrageurs sandwich the oversized swap and extract the difference — a direct, quantified loss of user funds (≈ swap size − minAmount − LP fees on the excess). Even with an accurate minAmount, the unlent reserve is needlessly pushed through a DEX each harvest.

### Likelihood Explanation
Requires `rewardTokens` to include `token` (or `strategyToken`), which happens exactly when the underlying itself is a distributed reward — a real MetaMorpho/URD pattern. The trigger `harvest` is owner/rebalancer-gated (honest roles per the threat model), but the bug requires no privileged misbehavior beyond a legitimate config; the fund loss is caused by third-party sandwichers and depositor timing, matching the original finding's "bot doing unintended things" rationale.

### Recommendation
In `_sellAllRewards` (or `_sellReward`), skip or revert when `_rewardToken == token || _rewardToken == strategy || _rewardToken == AATranche || _rewardToken == BBTranche`, i.e., add an `_isInvalidRewardToken`-style guard. Alternatively, sell only the amount returned by `redeemRewards` rather than the full contract balance.

### Proof of Concept
Foundry fork (mainnet, MetaMorpho USDC vault CDO):

```solidity
// Setup: idleCDO with MetaMorphoStrategy; owner adds USDC to rewardTokens
// (required to claim a real URD campaign that pays USDC).
vm.prank(owner);
strategy.setRewardTokens([USDC]);

// 1. Attacker deposits underlying, inflating CDO's USDC balance.
usdc.approve(address(cdo), DEPOSIT);
cdo.depositAA(DEPOSIT); // funds sit unlent pre-harvest

// 2. Keeper calls harvest with _sellAmounts = [0] and _minAmount
//    sized off-chain only for the merkle-claimable reward R.
bytes[] memory extra = _claimDataForUsdc(R); // URD proof
cdo.harvest(skipFlags, new bool[](1), minForR, sellZero, extra);

// 3. _sellReward swaps balance = R + DEPOSIT + reserve through UniV3
//    at minOut sized for R. Assert:
assertLt(usdc.balanceOf(univ3pool) ... // CDO sold >> R
assertLt(afterTotalValue, beforeTotalValue + R - EPS); // value extracted
```

If the listing is instead `strategyToken` (MetaMorpho shares), step 2 dumps the CDO's staked vault shares directly — the exact analog of the BPT sale in the original report.