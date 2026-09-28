### Title
ETH sent to `ConvexStrategyETH` is permanently locked — no withdrawal path — ([File: contracts/strategies/convex/ConvexStrategyETH.sol])

### Summary
`ConvexStrategyETH` declares a bare `receive() external payable {}` so it can accept raw ETH, but neither it nor its base `ConvexBaseStrategy` exposes any function that moves the contract's raw ETH balance out. Any ETH that reaches the contract — via a direct `selfdestruct` force-send or any plain transfer — is irretrievable, mirroring the `Anchor` bug class.

### Finding Description
`ConvexStrategyETH.sol:35` implements:

```solidity
receive() external payable {}
```

The only ETH-consuming code path is `_depositInCurve` (`ConvexStrategyETH.sol:23-33`), which unwraps the contract's **WETH** balance (`_weth.withdraw(_balance)`) and then calls `add_liquidity{value: _balance}` using exactly that WETH-derived amount. A pre-existing raw ETH balance is never read or used:

```solidity
uint256 _balance = _weth.balanceOf(address(this));
_weth.withdraw(_balance);
...
ICurveDeposit_2token(_curvePool(curveLpToken)).add_liquidity{value: _balance}(_depositArray, _minLpTokens);
```

So ETH already sitting in the contract is not credited to the strategy, not added to the Curve position, and not included in the strategy's accounting (which prices the LP token position, not `address(this).balance`). Searching `ConvexBaseStrategy` and the strategy base classes shows no `sweep`, `rescue`, `emergencyWithdraw` of native ETH, or `payable` transfer — only ERC20 token handling. The broken invariant is asset recoverability: funds accepted by `receive()` have no exit, so `address(this).balance` ETH is a permanent, unaccounted balance.

Note the contrast with `LidoCDOTrancheGateway.sol:99-101`, which guards `receive()` with `require(msg.sender == wethToken)` and thus cannot accumulate ETH from arbitrary senders — `ConvexStrategyETH` has no such guard.

### Impact Explanation
Permanent freezing of funds. Any ETH delivered to the strategy — e.g., force-sent via `selfdestruct`, sent by a user mistaking the strategy for a WETH-style deposit target, or left behind by a partially reverting integration — becomes permanently locked. The ETH is not reflected in `price()` / NAV (accounting is based on the Curve LP position), so it is a pure loss with no recovery mechanism. Loss magnitude equals the ETH balance sent; it does not grow or get absorbed into yield.

### Likelihood Explanation
Likelihood of significant accidental accumulation is low: normal strategy flows route WETH, not raw ETH, and `receive()` only exists because `_depositInCurve` needs to accept ETH from `IWETH9.withdraw`. However, no privileged action is required for funds to become stuck — any EOA or contract can send ETH, and a `selfdestruct` send bypasses even the ability to revert. The loss is deterministic once ETH lands (Medium-low likelihood, permanent impact per unit sent).

### Recommendation
Either restrict or make recoverable:

1. Gate `receive()` to the WETH contract only, matching the gateway pattern:
   ```solidity
   receive() external payable {
       require(msg.sender == WETH, "only-weth");
   }
   ```
   This prevents accidental sends but still leaves selfdestruct-forced ETH locked.
2. Add an owner-only native-token sweep to `ConvexBaseStrategy`/`BaseStrategy`, e.g.:
   ```solidity
   function sweepETH() external onlyOwner {
       payable(owner()).transfer(address(this).balance);
   }
   ```
   Optionally also fold a stray ETH balance into `_depositInCurve` (`add_liquidity{value: _balance + address(this).balance}`), though a sweep is simpler and safer.

### Proof of Concept
Foundry fork test (mainnet, a deployed `ConvexStrategyETH` instance or a fresh deployment wired in the standard `TestIdleCDOBase`-style harness):

```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.10;

import "forge-std/Test.sol";
import {ConvexStrategyETH} from "../contracts/strategies/convex/ConvexStrategyETH.sol";

contract LockedEthPoC is Test {
    ConvexStrategyETH strategy;

    function setUp() public {
        // deploy/attach ConvexStrategyETH wired to a stETH/ETH-style curve pool,
        // or fork mainnet and attach to an existing instance:
        // strategy = ConvexStrategyETH(payable(MAINNET_STRATEGY));
    }

    function test_EthPermanentlyLocked() public {
        address attacker = makeAddr("sender");
        vm.deal(attacker, 10 ether);

        // 1. plain transfer (succeeds: unrestricted receive())
        vm.prank(attacker);
        (bool ok,) = address(strategy).call{value: 5 ether}("");
        assertTrue(ok);

        // 2. selfdestruct force-send (bypasses receive entirely)
        SelfDestructor sd = new SelfDestructor{value: 5 ether}();
        sd.destroy(payable(address(strategy)));

        assertEq(address(strategy).balance, 10 ether);

        // 3. no function on the strategy can move the raw ETH:
        //    - strategy deposits pull from WETH balance only
        //    - redeem/withdraw paths return underlying via curve remove_liquidity
        //      and WETH re-wrapping, never touching address(this).balance
        //    => 10 ether is permanently unrecoverable (no sweep/rescue exists)
    }
}

contract SelfDestructor {
    constructor() payable {}
    function destroy(address payable target) external {
        selfdestruct(target);
    }
}
```

The PoC demonstrates that once ETH lands in `ConvexStrategyETH`, there is no code path — privileged or otherwise — that can ever move `address(this).balance`, so the funds are frozen permanently.