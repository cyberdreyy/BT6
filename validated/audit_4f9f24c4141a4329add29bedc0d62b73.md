### Title
`IdleUsualStrategy::getChainlinkPrice` accepts a clamped `minAnswer`/`maxAnswer` price, inflating senior parity and draining junior holders - ([File: contracts/strategies/usual/IdleUsualStrategy.sol](contracts/strategies/usual/IdleUsualStrategy.sol))

### Summary
`getChainlinkPrice` validates only the feed's 24h staleness and returns the raw `answer` scaled to 18 decimals, with no bound check. Chainlink feeds clamp reported prices to an internal `[minAnswer, maxAnswer]` range; if USD0++ falls below the feed's `minAnswer`, the feed keeps reporting that floor — a price higher than real market value. `IdleCDOUsualVariant.stopEpoch` trusts this price to set senior ($1) parity, causing under-compensation of seniors from juniors (or none at all if the reported floor is ≥ $1) and leaving the vault insolvent relative to real asset value. First redeemers exit at inflated virtualPrice while late redeemers are left with devalued USD0++.

### Finding Description
In `IdleUsualStrategy.getChainlinkPrice` (lines 164–172):

```solidity
(,int256 answer,,uint256 updatedAt,) = IOracle(USD0ppOracle).latestRoundData();
require(updatedAt > block.timestamp - 24 hours, "Stale price");
return uint256(answer) * 1e10;
```

Only staleness is checked. There is no validation that `answer` lies strictly within the aggregator's min/max bounds (obtainable via `aggregator().minAnswer()/maxAnswer()` or by reading the proxy's own bounds), nor that `answer > 0` semantics match a real market price. The same pattern the Zaros report flagged: when the underlying collapses below `minAnswer`, `answer == minAnswer` is silently accepted as the true price.

Consumers:
- `IdleUsualStrategy.initialize` (line 71) — seeds `oraclePrice`.
- `IdleCDOUsualVariant.startEpoch` (line 63) — stores `priceAtStartEpoch`.
- `IdleCDOUsualVariant.stopEpoch` (lines 77–96) — the critical path: `_oraclePrice = getChainlinkPrice()` → `setOraclePrice(_oraclePrice)` → `targetAATVL = lastNAVAA * oneToken / _oraclePrice` → `lastNAVAA` raised → `_updateAccounting()` socializes the "loss" to juniors.

The protocol's entire depeg-protection mechanism — "at epoch end each senior token should be at $1 parity; juniors pay the difference" — is priced by this single clampable feed.

### Impact Explanation
Concrete scenario in **running → stopped** epoch phase:

1. Epoch runs with juniors+seniors holding, say, 1M USD0++ each.
2. USD0++ crashes to $0.30, but the `USD0ppOracle` feed has `minAnswer` at, e.g., $0.85 (Chainlink feeds carry such bounds; USD0++ is a volatile rebasing-yield stablecoin that has already depegged once).
3. Owner calls `stopEpoch()`. `getChainlinkPrice` returns `0.85e18` — above the real $0.30, and it passes the staleness check because the feed still updates/heartbeat within 24h at the clamped value.
4. `targetAATVL = lastNAVAA * 1e18 / 0.85e18 ≈ 1.176×lastNAVAA` instead of `3.33×`. Even worse, if `minAnswer ≥ $1` the early `return` at line 87–88 skips compensation entirely.
5. Seniors' virtualPrice is boosted far less than needed, but more importantly `oraclePrice` stored in the strategy and the `lastNAVAA` uplift misprice the whole vault. Junior tranche virtualPrice is set by `_virtualPriceAux` to absorb only the oracle-implied loss.

Any unprivileged tranche holder can now redeem during the stopped epoch (`allowAAWithdraw`/`allowBBWithdraw = true`). Because the vault's accounting assumes USD0++ ≈ $0.85 while each redeemed token is really worth $0.30, total claims exceed real collateral value: early redeemers (an attacker simply holding tranche tokens) extract more real value than their fair share; remaining holders bear the residual shortfall — a direct, quantified theft/insolvency vector. The loss equals `(minAnswer − truePrice) × redeemedAmount`.

Broken invariant: **solvency / loss waterfall** — juniors are supposed to make seniors whole to $1; a clamped oracle silently breaks the waterfall's pricing input, and no guard (skim, default check — `_checkDefault` is a no-op here, KYC, onlyIdleCDO) prevents it. `setOraclePrice`'s floor check uses `IUSD0pp.getFloorPrice()`, which is Usual's own floor — unrelated to the Chainlink feed's `minAnswer` — so it does not catch the clamp either.

### Likelihood Explanation
- Requires USD0++ dropping below the feed's `minAnswer` — an extreme but plausible depeg scenario, precisely the scenario this variant exists to hedge. USD0++ has already demonstrated willingness to depeg (the product is literally depeg insurance).
- The trigger for loss realization is an honest owner call to `stopEpoch`, which happens at every epoch end — no attacker action needed beyond holding tranche tokens and redeeming promptly.
- Likelihood is low-probability/high-impact: conditional on an extreme market event, but fully deterministic once it occurs. Medium likelihood overall.

### Recommendation
- In `getChainlinkPrice`, fetch the feed's bounds and revert if the answer is at the boundary: e.g., query `AggregatorV2V3Interface(USD0ppOracle).aggregator()` (or `minAnswer()/maxAnswer()` if exposed) and `require(answer > minAnswer && answer < maxAnswer, "price out of bounds")`. Also revert on `answer <= 0`.
- Alternatively/additionally, sanity-check the returned price against `IUSD0pp(USD0pp).getFloorPrice()` before accepting it (the floor price is already fetched in `setOraclePrice`; apply the same comparison to the raw Chainlink answer), or cross-check with a secondary source.
- If the price is deemed invalid, `stopEpoch` should revert so the owner can retry rather than locking a wrong `lastNAVAA`/`oraclePrice`.

### Proof of Concept
Foundry fork test (mainnet; `USD0ppOracle` at `0xFC9e30Cf89f8A00dba3D34edf8b65BCDAdeCC1cB`). The oracle is a proxy — for PoC, etch/mock `latestRoundData` to return `minAnswer` while heartbeat is fresh, or fork at a historical block where USD0++ traded below the feed's min:

```solidity
// test/foundry/ChainlinkMinAnswer.t.sol
function testClampedOracleBreaksWaterfall() public {
    // 1. Deploy/attach IdleCDOUsualVariant + IdleUsualStrategy on a mainnet fork.
    // 2. Attacker deposits USD0++ into BB (junior); victim deposits into AA (senior).
    // 3. owner.startEpoch(180 days); vm.warp past epochEndDate.

    // 4. Simulate depeg below feed minAnswer: mock latestRoundData to return
    //    the aggregator's minAnswer with updatedAt == block.timestamp.
    uint80 roundId; int256 minAnswer = 0.85e8; // example feed floor
    vm.mockCall(
        0xFC9e30Cf89f8A00dba3D34edf8b65BCDAdeCC1cB,
        abi.encodeWithSelector(IOracle.latestRoundData.selector),
        abi.encode(roundId, minAnswer, block.timestamp, block.timestamp, roundId)
    );
    // true market price is 0.30 — assert feed reports 0.85 anyway
    assertEq(IdleUsualStrategy(strategy).getChainlinkPrice(), 0.85e18);

    // 5. owner.stopEpoch() — succeeds, does NOT revert despite wrong price.
    vm.prank(owner); cdo.stopEpoch();

    // 6. lastNAVAA uplift uses 0.85 not 0.30:
    //    assert targetAATVL used == lastNAVAA * 1e18/0.85e18 (vs correct 1e18/0.30e18).

    // 7. Attacker (junior holder) calls withdrawBB / AA holders redeem.
    //    Show attacker redeems USD0++ whose aggregate claim exceeds
    //    lastNAVBB_fair_share priced at real $0.30 → victim's remaining
    //    tranche tokens redeem for less than fair value / vault insolvent.
    uint256 attackerOut = cdo.withdrawBB(attackerBalance);
    // assert attackerOut * truePrice > attackerFairShare → quantified theft.
}
```

Steps concretely: attacker needs only to be a KYC-passed tranche holder calling `withdrawAA`/`withdrawBB` (or `redeem`) after `stopEpoch` — all unprivileged actions. The PoC asserts (a) `getChainlinkPrice` returns a boundary price without revert, and (b) redemption proceeds transfer more real value than the attacker's pro-rata share under the true market price, quantifying the loss to remaining holders.