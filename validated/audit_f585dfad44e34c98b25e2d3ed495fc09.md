### Title
Unconditional `receive()` in `ConvexStrategyETH` permanently freezes any ETH sent by a non-WETH caller - (File: contracts/strategies/convex/ConvexStrategyETH.sol)

### Summary
`ConvexStrategyETH` exposes a bare `receive() external payable {}` whose only legitimate purpose is to accept ETH unwrapped from WETH inside `_depositInCurve`. Unlike the analogous `LidoCDOTrancheGateway.receive()` (which enforces `require(msg.sender == wethToken, "only-weth")`), this sink accepts ETH from anyone and no code path ever recovers or accounts for stray native ETH, so any ETH sent outside the WETH-unwrap flow is locked forever. This is the same bug class as the Alchemix `RewardsDistributor.claim()` report: a payable entry point that swallows `msg.value` without validation or refund.

### Finding Description
`ConvexStrategyETH.sol:35` defines `receive() external payable {}` with no `msg.sender` check. The only intentional ETH inflow is in `_depositInCurve` (`contracts/strategies/convex/ConvexStrategyETH.sol:23-33`): it calls `_weth.withdraw(_balance)`, which triggers `receive()` from the WETH contract, and the same transaction forwards exactly `_balance` to `add_liquidity{value: _balance}`. Every other ETH deposit to the contract — a direct transfer, an accidental `call{value:...}`, or a self-destruct/force-send — is never consumed: `depositIntoStrategy`/ `_depositInCurve` only measure the WETH balance, nothing sweeps `address(this).balance`, and there is no `sweep`/`rescue` of native ETH. The contract thus silently absorbs ETH that no function can ever route into the Curve position or return.

### Impact Explanation
Permanent freezing of funds: any ETH held by `ConvexStrategyETH` outside the same-transaction WETH-unwrap flow is unrecoverable. Loss is quantified as the full `msg.value` sent by the sender (equivalent to the report's 40 ETH locked in the distributor).

### Likelihood Explanation
Low, matching the original report's medium/severity-low-likelihood profile: it requires a user or integrating contract to send native ETH to the strategy address directly. However the guard is trivially cheap (compare with `LidoCDOTrancheGateway.sol:99-101`, which already restricts `receive()` to the WETH contract in this same codebase), and the WETH contract itself is the only legitimate ETH source.

### Recommendation
Restrict the receive hook to the WETH contract, consistent with the gateway pattern:

```solidity
receive() external payable {
    require(msg.sender == WETH, "only-weth");
}
```

### Proof of Concept
Foundry fork-style test against a deployed `ConvexStrategyETH` instance:

```solidity
function testStuckEthInConvexStrategyETH() public {
    ConvexStrategyETH strategy = ConvexStrategyETH(payable(address(convexStrategyETH)));

    uint256 before = address(strategy).balance;
    // any EOA sends ETH directly; receive() accepts it unconditionally
    (bool ok,) = address(strategy).call{value: 10 ether}("");
    assertTrue(ok);
    assertEq(address(strategy).balance, before + 10 ether);

    // deposit() is called by the CDO: it only unwraps WETH and adds
    // exactly that WETH balance as liquidity; the stray 10 ETH is never
    // touched by _depositInCurve and there is no sweep function.
    // assert: no code path can move the 10 ETH -> permanently frozen.
}
```

Note: I could not verify whether `ConvexStrategyETH` is considered in-scope (strategies may be excluded per the rules); if legacy/strategy files are out of scope, no payable production entry point in this repo exhibits the swallow-`msg.value` pattern — `LidoCDOTrancheGateway.depositAAWithEth`/`depositBBWithEth` always forward the full `msg.value` to Lido `submit`, and `IdleCDOEthenaVariant._withdraw` forwards `msg.value` into the cooldown clone, with no early return before consumption.