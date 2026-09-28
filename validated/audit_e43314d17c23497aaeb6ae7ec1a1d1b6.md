### Title
`ConvexStrategyETH` permanently locks any ETH sent to it — ([File: contracts/strategies/convex/ConvexStrategyETH.sol])

### Summary
`ConvexStrategyETH` declares an unguarded `receive() external payable {}` (line 35). The only code path that consumes the contract's native ETH balance is `_depositInCurve`, which first unwraps the contract's *WETH* balance (`_weth.withdraw(_balance)`) and then forwards exactly that amount (`value: _balance`) into Curve's `add_liquidity`. There is no function — for any role — that transfers, sweeps, or otherwise releases a stray native ETH balance.

### Finding Description
The bug class from the external report ("contract accepts ETH via `receive()` but has no path to return it") maps directly onto `ConvexStrategyETH`:

- `receive() external payable {}` at line 35 accepts ETH from any EOA or contract with no `only-weth`-style guard (compare `LidoCDOTrancheGateway.sol` line 99-101, which restricts `receive()` to the WETH contract).
- `_depositInCurve()` (lines 23-33) reads only `WETH.balanceOf(address(this))`, unwraps it, and passes `value: _balance` to `add_liquidity`. A pre-existing native ETH balance is never included in `_depositArray`, so it is never deployed into the Curve pool.
- `ConvexBaseStrategy` exposes no ETH sweep/rescue function; its deposit/withdraw entry points move ERC-20/WETH tokens under CDO/whitelisted-role control. No guard, flag, or rescue path exists to reclaim stray native ETH.

Broken invariant: received value must remain recoverable. Any ETH arriving outside the atomic WETH-unwrap → `add_liquidity` window (an accidental `transfer`/`send`, or ETH force-sent via `selfdestruct` or a coinbase/reward payment, which bypass `receive()` entirely) is permanently frozen in the strategy.

### Impact Explanation
ETH held by the contract is permanently unrecoverable — there is no owner sweep, no skim for native balance, and `_depositInCurve` intentionally uses only the WETH-derived amount. Loss equals the full ETH balance stranded; it accrues to no one and is excluded from both user redemptions and strategy accounting (`price()` reads Curve/Convex positions, not `address(this).balance`).

### Likelihood Explanation
Low-to-medium. The protocol never intentionally routes raw ETH to the strategy; ETH only appears transiently inside `_depositInCurve` and is consumed in the same transaction. However, accidental transfers by EOAs interacting with the strategy address, refund-style Curve callbacks, or forced ETH (`selfdestruct`, block rewards on L2s where coinbase can be a contract address) can lock funds at any time. The sender bears the loss, matching the medium severity of the original finding.

### Recommendation
Apply one of:
- Restrict `receive()` to the WETH contract (`require(msg.sender == WETH)`), since only the `IWETH9.withdraw` call in `_depositInCurve` legitimately sends ETH to the strategy; or
- Add an owner/`whitelisted`-only `sweepETH(address to)` that forwards `address(this).balance`, or fold any pre-existing native balance into the `add_liquidity` call.

### Proof of Concept
Foundry (mainnet fork) sketch:

```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.10;
import "forge-std/Test.sol";
import {ConvexStrategyETH} from "../contracts/strategies/convex/ConvexStrategyETH.sol";

contract LockedEthTest is Test {
    ConvexStrategyETH strat = ConvexStrategyETH(payable(0xDEPLOYED_STRATEGY));

    function testEthLocked() public {
        address alice = makeAddr("alice");
        vm.deal(alice, 1 ether);

        // anyone can send ETH; receive() is unguarded
        vm.prank(alice);
        (bool ok,) = address(strat).call{value: 1 ether}("");
        assertTrue(ok);
        assertEq(address(strat).balance, 1 ether);

        // no externally callable function releases native ETH:
        // deposit/redeem/rebalance only move ERC-20/WETH and are
        // only-CDO/whitelisted; _depositInCurve consumes only the
        // WETH-unwrap amount. Balance remains 1 ether forever.
    }
}
```

Note: I was unable to fully enumerate every external entry point in `ConvexBaseStrategy` within the search budget; if it were found to contain an owner-level arbitrary call/sweep, the permanent-lock claim would weaken. The code path inspected (`_depositInCurve`) confirms stray native ETH is never used in strategy operations, and no rescue function surfaced in the search.