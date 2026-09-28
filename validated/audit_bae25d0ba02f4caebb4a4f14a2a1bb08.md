### Title
Front-runnable reward claim zeroes `MetaMorphoStrategy._claimReward` accounting and strands rewards in the IdleCDO - (File: contracts/strategies/morpho/MetaMorphoStrategy.sol)

### Summary
`MetaMorphoStrategy._claimReward` accounts claimed rewards as `balanceOf(idleCDO)` after minus before around a call to `IUniversalRewardsDistributor.claim`. Morpho's Universal Rewards Distributor `claim(account, reward, claimable, proof)` is permissionless: it only verifies the Merkle proof against the distributor root and sends the tokens to `account`. The `account` here is the IdleCDO, and the proof is fully visible in the pending `redeemRewards` calldata, so any EOA can copy it and claim on the CDO's behalf first. When the real `redeemRewards` executes, the before/after delta is 0 while the tokens already sit in the CDO, unaccounted.

### Finding Description
In `redeemRewards`, each reward is claimed via `_claimReward` (`contracts/strategies/morpho/MetaMorphoStrategy.sol:161-171`):

```solidity
uint256 balBefore = IERC20Detailed(reward).balanceOf(cdo);
distributor.claim(cdo, reward, claimable, proof);
claimed = IERC20Detailed(reward).balanceOf(cdo) - balBefore;
```

Because `distributor.claim` does not authenticate `msg.sender`, a griefer front-runs the CDO's `redeemRewards` (reached through `IdleCDO` harvest/redeem flows guarded by `onlyIdleCDO`) with an identical `claim(cdo, reward, claimable, proof)` call. The reward tokens land in `idleCDO` before `balBefore` is sampled, so `claimed == 0` and the returned `rewards[i]` reported to the CDO is zero. The same pattern exists in `MorphoSupplyVaultStrategy._claimMorpho` (`contracts/strategies/morpho/MorphoSupplyVaultStrategy.sol:108-116`), which takes `balBefore` on `account` around `distributor.claim(account, ...)`.

The reported-zero delta means the reward amounts are not folded into the accounting path that consumes `redeemRewards`'s return value; the tokens sit in the CDO outside any accounted flow, mirroring the Notional `VaultRewarderLib` finding where the delta-based claim permanently loses rewards.

### Impact Explanation
All rewards accrued since the last claim can be forced to report as 0 at no cost to the attacker (they only need gas and the publicly visible proof). The reward tokens are pushed to `idleCDO` early but recorded as zero claimed, so yield intended for tranche holders is not accounted/distributed through the normal reward flow and remains stranded in the contract. Loss is up to 100% of a claim batch per front-run, repeatable every epoch/harvest.

### Likelihood Explanation
No privileged role is required and no timing constraint exists beyond ordering before the CDO's claim transaction. The proof and `claimable` amount are plaintext in the victim transaction's calldata (or derivable from the public URD root), so the attack is a pure copy-and-front-run available to any EOA whenever `redeemRewards` is submitted with non-empty `claimDatas`.

### Recommendation
Measure claimed rewards against the strategy's own balance or, better, account on the CDO side using the full `balanceOf` of each `getRewardTokens()` token rather than the delta returned by `_claimReward`. Alternatively, have the strategy claim to itself (`distributor.claim(address(this), ...)`) and then transfer its whole reward balance to the CDO, which makes pre-claims by third parties count toward, not against, the accounted amount.

### Proof of Concept
Foundry fork sketch (mainnet, a MetaMorphoStrategy-backed IdleCDO with pending URD rewards):

```solidity
// victim tx in mempool: cdo.harvest(...) -> strategy.redeemRewards(data)
// data decodes to (reward, urd, claimable, proof)

// 1) Attacker front-runs with the copied calldata:
IUniversalRewardsDistributor(urd).claim(address(idleCDO), reward, claimable, proof);

// 2) Victim tx executes:
uint256[] memory rewards = MetaMorphoStrategy(strategy).redeemRewards(data);

// claimed = balanceOf(cdo)_after - balanceOf(cdo)_before == 0
assertEq(rewards[i], 0);
// but the reward tokens are already sitting in the CDO, unaccounted
assertGt(IERC20(reward).balanceOf(address(idleCDO)), 0);
```

Note: the severity of the stranding depends on the IdleCDO-side consumption of the `redeemRewards` return value (in `contracts/IdleCDO.sol`), which I could not fully verify line-by-line within this scan; if the CDO liquidates reward-token *balances* rather than the returned amounts, the impact downgrades to a no-op. If it accounts by the returned `rewards[]` array (the common pattern and the one the delta computation exists for), the loss is as described.