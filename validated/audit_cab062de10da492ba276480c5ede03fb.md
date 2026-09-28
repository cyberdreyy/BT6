### Title
Incomplete Chainlink rounds can distort Usual epoch-end tranche redistribution - (File: contracts/strategies/usual/IdleUsualStrategy.sol)

### Summary
`IdleUsualStrategy.getChainlinkPrice()` rejects an old `updatedAt` timestamp, but it does not verify that `answeredInRound >= roundId`, that `updatedAt != 0`, or that `answer > 0`. `IdleCDOUsualVariant.stopEpoch()` consumes the returned price directly when calculating the senior tranche’s target TVL. A recently started but incomplete Chainlink round can therefore carry an old answer forward and redistribute too much value to one tranche when the honest owner stops the epoch.

### Finding Description
The oracle wrapper discards `roundId` and `answeredInRound` and only checks `updatedAt > block.timestamp - 24 hours`. [1](#0-0)  Chainlink’s completeness invariant is `answeredInRound >= roundId`; without it, a round started within the heartbeat window can expose the previous round’s answer while still passing the freshness check.

The unchecked value is then passed to `setOraclePrice()` and also used independently as `_oraclePrice` to compute `targetAATVL = lastNAVAA * oneToken / _oraclePrice`. [2](#0-1)  Because `_oraclePrice` is the raw feed value rather than the floor-adjusted value stored by `setOraclePrice()`, the floor check in `setOraclePrice()` does not constrain the redistribution calculation. [3](#0-2) 

A stale low answer increases `targetAATVL`, artificially transferring value from junior to senior tranche holders. A stale high answer can prevent compensation owed to senior holders. Withdrawals are enabled immediately after this accounting update. [4](#0-3) 

### Impact Explanation
An unprivileged tranche-token holder can withdraw at the distorted tranche price after the honest owner calls `stopEpoch()`.

For example, if senior NAV is 1,000,000 USD0++ and an incomplete round returns a stale USD0++ price of `$0.50` while the current correct price is `$1.00`, the vault calculates a senior target TVL of 2,000,000 USD0++ instead of 1,000,000. Up to 1,000,000 USD0++ of junior value is therefore reassigned to senior holders. If juniors lack sufficient NAV to satisfy the inflated target, the loss waterfall can leave them with zero while seniors withdraw the available funds.

Conversely, a stale price at or above `1e18` causes the function to return without senior compensation, letting junior holders withdraw before seniors while retaining value that should have covered senior parity.

### Likelihood Explanation
The sequence requires a Chainlink round for USD0++ to remain incomplete when the owner stops the epoch. This is an uncommon feed state, but it is realistic because a new round can start near the end of the six-month epoch while `updatedAt` still reflects the recently answered previous round. No privileged misbehavior is required: the owner calls the intended `stopEpoch()` function, and an ordinary tranche holder subsequently withdraws at the resulting price.

### Recommendation
Validate all relevant Chainlink fields and use the floor-adjusted price consistently:

```solidity
function getChainlinkPrice() public view returns (uint256 price) {
    (
        uint80 roundId,
        int256 answer,
        ,
        uint256 updatedAt,
        uint80 answeredInRound
    ) = IOracle(USD0ppOracle).latestRoundData();

    require(answer > 0, "Invalid price");
    require(updatedAt != 0, "Invalid update");
    require(updatedAt > block.timestamp - 24 hours, "Stale price");
    require(answeredInRound >= roundId, "Incomplete round");

    price = uint256(answer) * 1e10;
    uint256 floorPrice = IUSD0pp(USD0pp).getFloorPrice();
    if (price < floorPrice) {
        price = floorPrice;
    }
}
```

Alternatively, after `_strategy.setOraclePrice(_oraclePrice)`, read back `_strategy.oraclePrice()` and use that value for `targetAATVL` so the floor-price clamp cannot be bypassed.

### Proof of Concept
The following Foundry test simulates a recently started incomplete Chainlink round on a mainnet fork. `updatedAt` is fresh, but `answeredInRound < roundId`; the implementation nevertheless returns the carried-forward `$0.50` answer.

```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.10;

import "forge-std/Test.sol";
import {IdleUsualStrategy} from "../contracts/strategies/usual/IdleUsualStrategy.sol";

contract IncompleteRoundOracle {
    function latestRoundData()
        external
        view
        returns (
            uint80 roundId,
            int256 answer,
            uint256 startedAt,
            uint256 updatedAt,
            uint80 answeredInRound
        )
    {
        // USD0++ answer has 8 decimals. The answer is from round 1,
        // while round 2 has started but has not been answered.
        roundId = 2;
        answer = 50_000_000; // $0.50
        startedAt = block.timestamp;
        updatedAt = block.timestamp;
        answeredInRound = 1;
    }
}

contract UsualIncompleteRoundPoC is Test {
    address internal constant USD0PP_ORACLE =
        0xFC9e30Cf89f8A00dba3D34edf8b65BCDAdeCC1cB;

    function testIncompleteRoundAccepted() external {
        vm.createSelectFork(vm.envString("MAINNET_RPC_URL"));

        IncompleteRoundOracle oracle = new IncompleteRoundOracle();
        vm.etch(USD0PP_ORACLE, address(oracle).code);

        IdleUsualStrategy strategy = new IdleUsualStrategy();

        uint256 stalePrice = strategy.getChainlinkPrice();
        assertEq(stalePrice, 0.5e18);

        uint256 lastNAVAA = 1_000_000e18;
        uint256 targetAATVL = lastNAVAA * 1e18 / stalePrice;

        // The epoch-end calculation doubles senior TVL from a stale
        // carried-forward answer, assigning junior value to seniors.
        assertEq(targetAATVL, 2_000_000e18);
    }
}
```

A complete validation would revert before the redistribution because `answeredInRound` is less than `roundId`.

### Citations

**File:** contracts/strategies/usual/IdleUsualStrategy.sol (L153-160)
```text
  /// @notice allow to update the oracle price
  /// @param _price new oracle price (18 decimals -> ie 1$ = 1e18)
  function setOraclePrice(uint256 _price) external {
    require(msg.sender == owner() || msg.sender == idleCDO, "!AUTH");
    // check that submitted price is not less than the floor price
    // and set oraclePrice
    uint256 floorPrice = IUSD0pp(USD0pp).getFloorPrice();
    oraclePrice = _price >= floorPrice ? _price : floorPrice;
```

**File:** contracts/strategies/usual/IdleUsualStrategy.sol (L163-171)
```text
  /// @notice get the chainlink price of usd0++ and scale it to 18 decimals
  function getChainlinkPrice() public view returns (uint256) {
    // Oracle it is not updated frequently but only if there is a deviation of 50 bips from 
    // the last reported value.There is also a 24 hours hearbeat. So we check that the last round 
    // is not older than 24 hours
    (,int256 answer,,uint256 updatedAt,) = IOracle(USD0ppOracle).latestRoundData();
    require(updatedAt > block.timestamp - 24 hours, "Stale price");
    // scale the answer to 18 decimals
    return uint256(answer) * 1e10;
```

**File:** contracts/IdleCDOUsualVariant.sol (L75-99)
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

    // if usd0++ price is 1$ or more then junior doesn't owe anything to senior
    if (_oraclePrice >= oneToken) {
      return;
    }
    // if price is less than 1$ we calculate how much junior should give to senior considering 1 usd0++ = 1$
    // eg price is 0.9$, lastNAVAA is 100 usd0++ (ie 90$) and targetAATVL should be 100$ so 100 / 0.9 = 111.11 usd0++
    // so we do lastNAVAA * (1 / oraclePrice) = 100 * (1 / 0.9) = 111.11
    uint256 targetAATVL = lastNAVAA * oneToken / _oraclePrice;

    // we increase the lastNAVAA so that the senior tranche is at par with 1$
    lastNAVAA += targetAATVL - lastNAVAA;

    // this will cause the next updateAccounting call to account a loss which will be absorbed by the junior tranche
    _updateAccounting();    
```
