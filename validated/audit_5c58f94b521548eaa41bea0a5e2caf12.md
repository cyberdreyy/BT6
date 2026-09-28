### Title
`ConvexStrategyETH.receive()` accepts native ETH from any sender, permanently freezing mistakenly sent funds - (File: contracts/strategies/convex/ConvexStrategyETH.sol)

### Summary
The external bug class is "native funds accepted where only ERC20/contract-internal flows are expected, leaving them frozen on the contract balance". The closest analog in this repo is `ConvexStrategyETH`, an ETH-denominated Convex/Curve strategy. Its `receive()` is an unrestricted payable fallback, while the only legitimate ETH inflow is the WETH unwrap in `_depositInCurve`. Any ETH sent directly by an EOA or contract sits on the strategy balance with no code path that ever reads `address(this).balance`, so it is permanently locked.

### Finding Description
`ConvexStrategyETH._depositInCurve` unwraps the contract's WETH balance into ETH and immediately forwards exactly that amount to Curve's `add_liquidity{value: _balance}`. The empty `receive()` exists only to accept that WETH unwrap. However, it does not gate the sender:

```solidity
// contracts/strategies/convex/ConvexStrategyETH.sol
function _depositInCurve(uint256 _minLpTokens) internal override {
    IWETH9 _weth = IWETH9(WETH);
    uint256 _balance = _weth.balanceOf(address(this));
    _weth.withdraw(_balance);
    uint256[2] memory _depositArray;
    _depositArray[depositPosition] = _balance;
    ICurveDeposit_2token(_curvePool(curveLpToken)).add_liquidity{value: _balance}(_depositArray, _minLpTokens);
}
receive() external payable {}   // <- accepts ETH from anyone, no msg.sender == WETH check
```

Compare with `LidoCDOTrancheGateway`, which correctly restricts its `receive()` to the WETH contract (`require(msg.sender == wethToken, "only-weth")`). `ConvexStrategyETH` lacks the equivalent guard. Nothing in `BaseStrategy`/`ConvexBaseStrategy` consumes stray ETH: `price()` computes value from staked/reward positions, `_depositInCurve` only ever pushes the freshly unwrapped `_balance`, and there is no ETH sweep/withdraw function. So `address(this).balance` after a tx is permanently unrecoverable.

### Impact Explanation
Direct fund loss / permanent freezing. Any user (or any contract that mistakenly selfdestructs-forwards or plain-transfers ETH to the strategy) loses the full `msg.value` amount. The ETH is not credited to any depositor, is not counted in `price()`, and cannot be rescued by owner/rebalancer/`IdleCDO`, since none of the deposit/withdraw/harvest paths move raw ETH except the exact-amount `add_liquidity{value: _balance}` call. Loss magnitude equals the mistakenly sent amount; there is no bound.

### Likelihood Explanation
Low-to-medium. It requires an erroneous native transfer to the strategy address rather than an attacker action — same profile as the original Li.Fi finding (mistakenly sent native funds frozen on contract balance). ETH-denominated strategies are exactly the context where users and integrators are most likely to accidentally attach `msg.value` (e.g., a router or wrapper forwarding leftover ETH, or a user sending ETH believing the strategy wraps it like the `LidoCDOTrancheGateway.depositAAWithEth` flow). The unprivileged-attacker model cannot steal the funds, but the acceptance criterion "permanent freezing" of funds is satisfied.

### Recommendation
Mirror the guard already used in `LidoCDOTrancheGateway`: restrict the `receive()` to the WETH contract so the only accepted ETH is the internal unwrap.

```solidity
// contracts/strategies/convex/ConvexStrategyETH.sol
receive() external payable {
    require(msg.sender == WETH, "only-weth");
}
```

Alternatively/additionally, sweep `address(this).balance` into the next `_depositInCurve` (e.g., re-wrap or add to the Curve deposit) so any residual ETH is captured rather than stranded.

### Proof of Concept
Foundry (mainnet fork) sketch demonstrating the freeze:

```solidity
// test/foundry/ConvexStrategyETHFreeze.t.sol
function test_StrayEthFrozen() public {
    // strategy: ConvexStrategyETH instance already deployed/initialized on fork
    ConvexStrategyETH strat = ConvexStrategyETH(payable(STRATEGY));

    uint256 balBefore = address(strat).balance;
    assertEq(balBefore, 0);

    // unprivileged user mistakenly sends 1 ETH
    vm.deal(attacker, 1 ether);
    vm.prank(attacker);
    (bool ok,) = address(strat).call{value: 1 ether}("");
    assertTrue(ok);                       // receive() accepts it -- no msg.sender check

    assertEq(address(strat).balance, 1 ether);

    // No external call can move it: _depositInCurve only forwards WETH-withdrawn
    // balance; there is no sweep. Even owner/IdleCDO calls leave balance unchanged.
    vm.prank(strat.owner());
    // any admin/deposit/harvest path...
    assertEq(address(strat).balance, 1 ether); // still stuck -> permanent freeze
}
```

Notes/caveats: I was unable to fully verify within the indexed context whether `ConvexBaseStrategy`/`BaseStrategy` add any ETH sweep in `harvest`/`deposit` — the indexed snippets show no `address(this).balance` usage, but if a hidden path consumes the full ETH balance the impact reduces to temporary freezing. Also, if Convex strategies are treated as legacy/out-of-scope in this engagement, this finding should be downgraded accordingly.