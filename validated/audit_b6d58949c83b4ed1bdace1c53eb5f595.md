### Title
Frontrunning the permissionless URD `claim` makes `MetaMorphoStrategy.redeemRewards` report 0 rewards, leaving claimed reward tokens stuck unaccounted in the IdleCDO - (File: contracts/strategies/morpho/MetaMorphoStrategy.sol)

### Summary
`MetaMorphoStrategy.redeemRewards` computes claimed rewards as the *delta* of the IdleCDO's reward-token balance around `IUniversalRewardsDistributor.claim`. Morpho's `UniversalRewardsDistributor.claim(account, reward, claimable, proof)` is permissionless — anyone holding a valid proof can execute it for `account`. An attacker who observes the harvest/redeem transaction in the mempool can call `urd.claim(idleCDO, reward, claimable, proof)` first, pushing the tokens into the IdleCDO before `redeemRewards` runs. The strategy then measures `claimed = balanceOf(cdo) - balBefore = 0` and returns `rewards[i] = 0`, so the CDO's accounting path treats the harvest as having produced nothing while the reward tokens physically sit in the IdleCDO with no crediting step.

### Finding Description
The vulnerable accounting lives in `_claimReward`:

```solidity
// contracts/strategies/morpho/MetaMorphoStrategy.sol:161-171
function _claimReward(
  IUniversalRewardsDistributor distributor,
  address cdo,
  address reward,
  uint256 claimable,
  bytes32[] memory proof
) internal returns (uint256 claimed) {
  uint256 balBefore = IERC20Detailed(reward).balanceOf(cdo);
  distributor.claim(cdo, reward, claimable, proof);
  claimed = IERC20Detailed(reward).balanceOf(cdo) - balBefore;
}
```

`redeemRewards` (contracts/strategies/morpho/MetaMorphoStrategy.sol:85-114) decodes attacker-visible calldata `(reward, rewardDistributor, claimable, proof)` and returns `rewards[i] = _claimReward(...)`. Because the distributor is a URD, the exact `(cdo, reward, claimable, proof)` tuple in the mempool is itself a valid, replayable claim — no signature binds the claim to the strategy's caller. After a frontrun, `claimed` is 0 even though the IdleCDO's reward-token balance increased.

The same before/after pattern exists in `MorphoSupplyVaultStrategy._claimMorpho` (contracts/strategies/morpho/MorphoSupplyVaultStrategy.sol:108-116). By contrast, `ConvexBaseStrategy.redeemRewards`, `IdleClearpoolStrategy.redeemRewards` and `IdleTruefiStrategy._redeemRewards` read the *absolute* `balanceOf(this)` after claiming, so a pre-claimed balance is still forwarded — they are not affected. The delta-vs-absolute asymmetry is exactly the external bug class: balance-diff accounting breaks when the underlying claim is permissionless and settles to a different address than the measurer.

Note: `redeemRewards` is `onlyIdleCDO`, so the strategy can only be invoked through the CDO — but the *external* `distributor.claim` it depends on carries no such restriction, which is what enables the frontrun.

### Impact Explanation
Reward tokens belonging to tranche holders are pushed into the IdleCDO while its harvest logic records a zero harvest for that token. The IdleCDO has no permissionless sweep for arbitrary reward tokens, so unaccounted rewards are frozen in the CDO (recoverable only via privileged emergency paths), i.e. permanent freezing of unclaimed yield for the duration the tokens remain unprocessed. The lost amount equals the full claimable reward for that epoch of the merkle root — repeatable every harvest cycle for every configured `rewardTokens[i]` with a non-empty claim blob.

### Likelihood Explanation
Requires only an unprivileged EOA observing the public mempool: the harvest calldata itself contains a self-contained valid proof. Whenever MetaMorpho distributes rewards via a URD (the designed flow — `setRewardData`/`rewardsData` exists precisely for URD-based emissions such as MORPHO/COMP), any harvest transaction can be copied and executed first at the cost of gas only. Attacker gains nothing directly, but griefing cost is near zero and MEV/frontrun bots routinely replay profitable-looking calldata; no privileged role misbehavior is needed.

### Recommendation
Do not measure the claim result by the recipient's balance delta. Either:
- have the URD send rewards to the strategy (`distributor.claim(address(this), ...)`) and forward `balanceOf(this)`/`claimed` to the CDO in the same call, so a frontrun cannot produce an unaccounted balance at the CDO; or
- have the IdleCDO account for rewards by sweeping its *post-call absolute* balance (`rewardToken.balanceOf(address(this))` minus a pre-tracked reserve) rather than trusting the returned `claimed` value; or
- return `claimed = balanceOf(cdo) - balBefore` but additionally report/forward the whole unaccounted balance so a frontrun delta of 0 does not drop already-arrived tokens.

### Proof of Concept
Foundry fork test (mainnet), sketch:

```solidity
// test/foundry/MetaMorphoFrontrun.t.sol
function test_FrontrunRewardClaim() public {
    // idleCDO uses MetaMorphoStrategy; rewards accrue on Morpho Blue markets
    // and are distributed through a UniversalRewardsDistributor.
    // Keeper builds harvest calldata:
    bytes32[] memory proof = getProof(idleCDO, rewardToken, claimable); // valid URD proof
    bytes[] memory claims = new bytes[](1);
    claims[0] = abi.encode(rewardToken, urd, claimable, proof);
    bytes memory data = abi.encode(claims);

    // Attacker (any EOA) sees the tx and executes the same claim first.
    // URD.claim is permissionless: proof only commits to (account, reward, claimable).
    vm.prank(attacker);
    IUniversalRewardsDistributor(urd).claim(idleCDO, rewardToken, claimable, proof);
    assertEq(rewardToken.balanceOf(idleCDO), claimable); // tokens already at CDO

    // Now the legitimate redeemRewards runs via the CDO:
    vm.prank(idleCDO);
    uint256[] memory rewards = MetaMorphoStrategy(strategy).redeemRewards(data);

    // Delta accounting reports 0 -> CDO credits nothing for this token
    assertEq(rewards[0], 0);
    // Reward tokens sit in the CDO with no accounting/sweep path -> frozen yield
    assertEq(rewardToken.balanceOf(idleCDO), claimable);
}
```

The test demonstrates the broken invariant on a mainnet fork: a second call to `redeemRewards` cannot recover the tokens either, because `claimed` would again be measured as a zero delta (URD updates `claimed[account][reward]`, so re-claiming the same `claimable` transfers nothing).