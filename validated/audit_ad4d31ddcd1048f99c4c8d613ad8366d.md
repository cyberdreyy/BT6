### Title
If the USD0++ Chainlink oracle reverts or goes stale, `stopEpoch` will revert and all tranche withdrawals remain frozen — (File: contracts/IdleCDOUsualVariant.sol)

### Summary
`IdleCDOUsualVariant` is a single-epoch "disposable" vault for USD0++ depeg protection. During the epoch all deposits and withdrawals are disabled; they can only be re-enabled by `stopEpoch`, which unconditionally calls `IdleUsualStrategy.getChainlinkPrice()`. If the USD0++ Chainlink feed is taken offline, reverts, or stops updating, `stopEpoch` reverts forever and user funds are frozen with no fallback path.

### Finding Description
When the owner calls `stopEpoch` after `epochEndDate`, the function fetches the USD0++ price before re-enabling withdrawals: [1](#0-0) 

`getChainlinkPrice` performs an unprotected `latestRoundData` call plus a strict 24h staleness check: [2](#0-1) 

Chainlink has historically paused feeds in extreme conditions (e.g., UST/ETH during the UST collapse). Two failure modes freeze this vault:

1. `latestRoundData()` reverts (feed disabled/access-revoked) → `stopEpoch` reverts.
2. `updatedAt` stops updating (heartbeat missed, feed abandoned) → `require(updatedAt > block.timestamp - 24 hours, "Stale price")` permanently reverts once the feed is >24h stale. Since the epoch duration is ~6 months and `stopEpoch` is only callable after `epochEndDate`, a feed that dies near the end of the epoch can never recover.

There is no alternative path: `startEpoch` sets `allowAAWithdraw = false`, `allowBBWithdraw = false` and `_pause()`, and only `stopEpoch` resets these flags. `setOraclePrice` cannot help because `stopEpoch` overwrites the stored price with `getChainlinkPrice()` before using it, and `harvest`/`redeemRewards` do not restore withdrawals either.

### Impact Explanation
Permanent freezing of user funds. All AA and BB tranche holders' USD0++ is locked in the strategy; `withdrawAA`/`withdrawBB` revert because `allowAAWithdraw`/`allowBBWithdraw` remain false and the contract is paused. The only recovery would be a contract upgrade. This is precisely the class from the external report — a single unguarded oracle dependency freezing a critical protocol exit path exactly when it is needed (end-of-epoch settlement, potentially during a USD0++ depeg event, which is also when an oracle is most likely to misbehave).

### Likelihood Explanation
Medium-low. It requires the USD0ppOracle feed to be disabled or to miss its 24h heartbeat around the single `stopEpoch` window. However, the failure is not attacker-dependent, the vault is disposable with a one-shot epoch, and USD0++ is exactly the kind of asset (hard-pegged floor mechanism) whose oracle could be deprecated. No privileged misbehavior needed.

### Recommendation
Wrap the oracle call in a try/catch or add an owner-controlled fallback so `stopEpoch` cannot be permanently bricked, e.g.:

```solidity
uint256 _oraclePrice;
try _strategy.getChainlinkPrice() returns (uint256 p) {
    _oraclePrice = p;
} catch {
    _oraclePrice = _strategy.oraclePrice(); // last cached price or owner-set fallback
}
```

Alternatively, let `setOraclePrice` supply a manual price that `stopEpoch` uses when the Chainlink call fails or is stale, and/or add a separate owner function to re-enable withdrawals without requiring a fresh oracle read.

### Proof of Concept
Foundry fork test (mainnet), concept:

```solidity
// 1. Deploy IdleCDOUsualVariant + IdleUsualStrategy, users deposit USD0++ into AA/BB.
// 2. owner.startEpoch(180 days) -> deposits/withdrawals paused.
// 3. vm.warp(epochEndDate).
// 4. Mock USD0ppOracle (0xFC9e30Cf89f8A00dba3D34edf8b65BCDAdeCC1cB) to revert on latestRoundData,
//    or set its updatedAt to block.timestamp - 25 hours via vm.mockCall.
// 5. vm.prank(owner); vault.stopEpoch(); -> reverts ("Stale price" or oracle revert).
// 6. user.withdrawAA(amount) / withdrawBB(amount) -> reverts (allowAAWithdraw == false, paused).
// 7. Repeat step 5 at any later timestamp -> still reverts: freeze is permanent.
```

### Citations

**File:** contracts/IdleCDOUsualVariant.sol (L70-88)
```text
  function stopEpoch() external {
    _checkOnlyOwner();
    // if the epoch is not running we revert
    require(isEpochRunning && block.timestamp >= epochEndDate, "9");

    IdleUsualStrategy _strategy = IdleUsualStrategy(strategy);
    // we fetch and set oracle price for reference
    uint256 _oraclePrice = _strategy.getChainlinkPrice();
    _strategy.setOraclePrice(_oraclePrice);

    // we unpause redeems only
    allowAAWithdraw = true;
    allowBBWithdraw = true;
    // set epoch as not running
    isEpochRunning = false;

    // if usd0++ price is 1$ or more then junior doesn't owe anything to senior
    if (_oraclePrice >= oneToken) {
      return;
```

**File:** contracts/strategies/usual/IdleUsualStrategy.sol (L164-172)
```text
  function getChainlinkPrice() public view returns (uint256) {
    // Oracle it is not updated frequently but only if there is a deviation of 50 bips from 
    // the last reported value.There is also a 24 hours hearbeat. So we check that the last round 
    // is not older than 24 hours
    (,int256 answer,,uint256 updatedAt,) = IOracle(USD0ppOracle).latestRoundData();
    require(updatedAt > block.timestamp - 24 hours, "Stale price");
    // scale the answer to 18 decimals
    return uint256(answer) * 1e10;
  }
```
