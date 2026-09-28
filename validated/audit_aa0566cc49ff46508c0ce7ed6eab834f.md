### Title
Unprotected Chainlink call in `getChainlinkPrice()` permanently bricks `stopEpoch`, freezing all tranche funds - ([File: contracts/strategies/usual/IdleUsualStrategy.sol](contracts/strategies/usual/IdleUsualStrategy.sol))

### Summary
`IdleUsualStrategy.getChainlinkPrice()` performs a raw `latestRoundData()` call on the USD0++ Chainlink feed without any defensive handling. This call sits on the only code path that re-enables withdrawals in `IdleCDOUsualVariant.stopEpoch()`. If the feed reverts (Chainlink multisigs can block access to a feed at will, or the aggregator is deprecated/replaced), `stopEpoch()` can never execute, `allowAAWithdraw`/`allowBBWithdraw` remain `false` and the contract stays paused, permanently freezing every deposit in both tranches.

### Finding Description
`getChainlinkPrice()` at `contracts/strategies/usual/IdleUsualStrategy.sol:164-172` does:

```solidity
(,int256 answer,,uint256 updatedAt,) = IOracle(USD0ppOracle).latestRoundData();
require(updatedAt > block.timestamp - 24 hours, "Stale price");
```

Two revert conditions exist: the external call itself can revert (feed access revoked by Chainlink multisigs, aggregator upgrade/deprecation), and the staleness `require` reverts if the feed stops updating.

This function is invoked in exactly two places, both on the critical epoch path in `contracts/IdleCDOUsualVariant.sol`:

- `startEpoch()` at line 63: `priceAtStartEpoch = IdleUsualStrategy(_strategy).getChainlinkPrice();` — only used to store a reference price, but a revert here also blocks epoch start.
- `stopEpoch()` at line 77: `uint256 _oraclePrice = _strategy.getChainlinkPrice();` — this is the fatal one. `stopEpoch()` is the only function that sets `allowAAWithdraw = true` / `allowBBWithdraw = true` (lines 81-82) after `startEpoch()` has called `_pause()` and disabled withdrawals (lines 52-55).

During the epoch the vault is paused and both withdraw flags are false, so `withdrawAA`/`withdrawBB` revert. The vault is "disposable" (single epoch, ~6 months per the docstring), so there is no next epoch to recover into. If `latestRoundData()` reverts, the honest owner cannot complete `stopEpoch()` no matter how many times they call it — the revert happens before any state is updated — and senior+junior principal and all harvested USUAL yield stay locked in the contract/strategy indefinitely.

### Impact Explanation
Permanent freezing of 100% of user funds (AA + BB tranche underlying and accrued USUAL rewards). Unlike the original JOJO report where the DoS only blocked a price read, here the oracle call gates the sole withdrawal-enable path of a paused vault, upgrading the impact from a bricked view to locked principal. The loss waterfall itself (`_virtualPriceAux` loss redirection at lines 152-169) can never even be reached, because `_updateAccounting()` at line 99 is never executed.

### Likelihood Explanation
Not attacker-triggerable by an unprivileged user — it depends on an external Chainlink condition (feed blocked by multisig, deprecation, or >24h heartbeat gap). This is a low-probability/high-impact conditional failure, matching the Medium rating of the source finding. Worth noting an unverified mitigant: the strategy owner can pull underlying out via `transferToken()` (line 191) and IdleCDO may expose an emergency-shutdown path, so governance could potentially recover funds out-of-band — but the in-protocol withdrawal path remains permanently bricked and any emergency path would bypass the senior-parity accounting, which is itself a loss of the intended payout invariant.

### Recommendation
Wrap the Chainlink call defensively and provide a fallback so `stopEpoch` cannot be bricked:

```solidity
function getChainlinkPrice() public view returns (uint256) {
    try IOracle(USD0ppOracle).latestRoundData() returns (
        uint80, int256 answer, uint256, uint256 updatedAt, uint80
    ) {
        require(updatedAt > block.timestamp - 24 hours, "Stale price");
        return uint256(answer) * 1e10;
    } catch {
        revert("ORACLE_UNAVAILABLE"); // or fall back to floor price / last stored price
    }
}
```

Better: in `stopEpoch()`, catch the oracle failure and fall back to `IUSD0pp(USD0pp).getFloorPrice()` (already used in `setOraclePrice`) or a governance-set price, so withdrawals can always be re-enabled even under oracle failure.

### Proof of Concept
Foundry fork test (mainnet, since `USD0ppOracle = 0xFC9e30Cf89f8A00dba3D34edf8b65BCDAdeCC1cB` is a mainnet feed):

```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.10;

import "forge-std/Test.sol";
import {IdleCDOUsualVariant} from "../contracts/IdleCDOUsualVariant.sol";

contract ChainlinkDoSPoC is Test {
    IdleCDOUsualVariant cdo = IdleCDOUsualVariant(<deployed CDO>);
    address owner = <cdo.owner()>;

    function testStopEpochBricked() public {
        // 1. Epoch running, past epochEndDate
        vm.warp(cdo.epochEndDate() + 1);

        // 2. Simulate Chainlink blocking the feed: make latestRoundData revert
        address feed = 0xFC9e30Cf89f8A00dba3D34edf8b65BCDAdeCC1cB;
        vm.mockCallRevert(
            feed,
            abi.encodeWithSignature("latestRoundData()"),
            "access blocked"
        );

        // 3. Honest owner tries to stop the epoch -> always reverts
        vm.prank(owner);
        vm.expectRevert();
        cdo.stopEpoch();

        // 4. Withdrawals stay disabled; user funds frozen
        assertFalse(cdo.allowAAWithdraw());
        assertFalse(cdo.allowBBWithdraw());
        assertTrue(cdo.paused());
        // Any withdrawAA/withdrawBB call reverts -> permanent freeze of deposits
    }
}
```

Alternatively, without mocking, warp past `updatedAt + 24 hours` on a stale fork block to trigger the `"Stale price"` revert on the same path.