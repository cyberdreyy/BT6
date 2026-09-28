### Title
Unhandled Chainlink revert in `getChainlinkPrice` bricks `startEpoch`/`stopEpoch`, freezing all tranche withdrawals - (File: contracts/strategies/usual/IdleUsualStrategy.sol)

### Summary
`IdleUsualStrategy.getChainlinkPrice()` calls `IOracle(USD0ppOracle).latestRoundData()` with no try/catch or fallback [1](#0-0) . This view is on the critical path of `IdleCDOUsualVariant.startEpoch()` [2](#0-1)  and `stopEpoch()` [3](#0-2) . If Chainlink disables or pauses the USD0++ feed (as it did for UST/ETH), the feed reverts, both epoch transitions revert, and the pool remains stuck in whatever phase it was in.

### Finding Description
During a running epoch, `isEpochRunning == true`, deposits are paused (`_pause()`), and `allowAAWithdraw`/`allowBBWithdraw` are both `false` [4](#0-3) . The only path to re-enable withdrawals is `stopEpoch()`, which must call `getChainlinkPrice()` to set `oraclePrice` [5](#0-4) . `setOraclePrice` is `onlyOwner`/CDO-gated and itself does not call Chainlink, but the CDO's own stop-epoch flow unconditionally reads the feed. A reverting feed therefore makes `stopEpoch` uncallable while `epochEndDate` has passed, permanently freezing withdrawer funds. Similarly, a reverting feed blocks `startEpoch`, preventing the vault from ever leaving its buffer phase — user deposits sit idle and the strategy's `deposit()` of underlying into USD0++ never executes.

There is no secondary path: `oraclePrice` can only be updated by the owner via `setOraclePrice` [6](#0-5) , but `stopEpoch` always reads the live feed first and reverts before owner intervention could help. No guard (KYC, skim, pause flags) mitigates this — the revert happens inside the honest owner's own transaction.

### Impact Explanation
Temporary-to-permanent freezing of all tranche holder funds. While the feed is down: (a) a running epoch can never be stopped → `allowAAWithdraw`/`allowBBWithdraw` stay `false` → no AA or BB redemptions; (b) a stopped/buffer epoch can never be started → deposits paused indefinitely if paused, and NAV is never deployed to the strategy. Loss equals the full vault TVL for the duration of the feed outage, or permanently if Chainlink never re-enables the feed. No funds are stolen, but the invariant "users can eventually exit" is broken.

### Likelihood Explanation
Chainlink has precedent for taking feeds offline during extreme volatility (UST collapse). USD0++ is a newer, lower-liquidity asset, making feed deprecation or deviation-threshold freezes plausible. No attacker action is required — the condition is environmental and entirely outside protocol control.

### Recommendation
Wrap the `latestRoundData()` call in `getChainlinkPrice` in a try/catch. On failure, fall back to the last stored `oraclePrice` or to `IUSD0pp(USD0pp).getFloorPrice()` so `stopEpoch`/`startEpoch` remain callable. Alternatively, add an owner escape path in `stopEpoch` that accepts a manually supplied price when the feed is unavailable.

### Proof of Concept
Foundry fork test sketch (mainnet, `USD0ppOracle = 0xFC9e...c1cB`):

```solidity
// mock the Chainlink feed to revert
vm.mockCallRevert(
    address(0xFC9e30Cf89f8A00dba3D34edf8b65BCDAdeCC1cB),
    abi.encodeWithSelector(IOracle.latestRoundData.selector),
    "Feed disabled"
);

// after an epoch has started and epochEndDate passed:
vm.prank(owner);
vm.expectRevert();
IdleCDOUsualVariant(address(idleCDO)).stopEpoch(); // reverts inside getChainlinkPrice

// allowAAWithdraw/allowBBWithdraw remain false -> all withdrawAA/withdrawBB revert
vm.expectRevert();
idleCDO.withdrawAA(1);
```

Run against a block where the epoch is running: `startEpoch` succeeds with a live feed, warp past `epochEndDate`, then revert-mock the feed and observe `stopEpoch` and all withdrawals permanently fail while the mock persists.

### Citations

**File:** contracts/strategies/usual/IdleUsualStrategy.sol (L155-161)
```text
  function setOraclePrice(uint256 _price) external {
    require(msg.sender == owner() || msg.sender == idleCDO, "!AUTH");
    // check that submitted price is not less than the floor price
    // and set oraclePrice
    uint256 floorPrice = IUSD0pp(USD0pp).getFloorPrice();
    oraclePrice = _price >= floorPrice ? _price : floorPrice;
  }
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

**File:** contracts/IdleCDOUsualVariant.sol (L51-59)
```text
    // we pause deposits
    _pause();
    // prevent withdrawals
    allowAAWithdraw = false;
    allowBBWithdraw = false;
    // set epoch as running
    isEpochRunning = true;
    // set epoch end date
    epochEndDate = block.timestamp + _epochDuration;
```

**File:** contracts/IdleCDOUsualVariant.sol (L62-66)
```text
    address _strategy = strategy;
    priceAtStartEpoch = IdleUsualStrategy(_strategy).getChainlinkPrice();

    // deposit all usd0++ in the strategy and mint strategyTokens 1:1
    IIdleCDOStrategy(_strategy).deposit(IERC20Detailed(token).balanceOf(address(this)));
```

**File:** contracts/IdleCDOUsualVariant.sol (L75-84)
```text
    IdleUsualStrategy _strategy = IdleUsualStrategy(strategy);
    // we fetch and set oracle price for reference
    uint256 _oraclePrice = _strategy.getChainlinkPrice();
    _strategy.setOraclePrice(_oraclePrice);

    // we unpause redeems only
    allowAAWithdraw = true;
    allowBBWithdraw = true;
    // set epoch as not running
    isEpochRunning = false;
```
