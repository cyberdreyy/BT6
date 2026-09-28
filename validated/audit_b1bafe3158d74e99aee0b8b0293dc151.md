### Title
ETH sent to `ConvexStrategyETH` via its unrestricted `receive()` is permanently locked and never used by the strategy - (contracts/strategies/convex/ConvexStrategyETH.sol)

### Summary
`ConvexStrategyETH` exposes an unguarded `receive() external payable {}`, so any EOA or contract can send native ETH to the strategy. The only ETH-consuming code path, `_depositInCurve`, unwraps the contract's *WETH* balance and forwards exactly that amount to `CurveDeposit_2token.add_liquidity{value: _balance}`. ETH held directly by the contract (from `receive()`) is never included, and the contract has no ETH sweep/rescue function — only ERC20/token handling exists in `ConvexBaseStrategy`/`BaseStrategy`. Any ETH sent by mistake is locked forever.

### Finding Description
In `ConvexStrategyETH.sol:35`:

```solidity
receive() external payable {}
```

accepts ETH from anyone with no accounting. In `_depositInCurve` (`ConvexStrategyETH.sol:23-33`):

```solidity
uint256 _balance = _weth.balanceOf(address(this));
_weth.withdraw(_balance);
uint256[2] memory _depositArray;
_depositArray[depositPosition] = _balance;
ICurveDeposit_2token(_curvePool(curveLpToken)).add_liquidity{value: _balance}(_depositArray, _minLpTokens);
```

the Curve call value is bound to `_balance` (the just-unwrapped WETH amount), not `address(this).balance`. So ETH already sitting in the contract from `receive()` is neither forwarded to the Curve pool nor credited to `underlyingToken`/`token` accounting — `price()`/`getContractValue` in `IdleCDO` only value `token` and `strategyToken` balances, so stray ETH is invisible to NAV as well.

This mirrors the report's bug class: a payable surface that silently accepts the chain's native token while the contract's deposit logic only consumes a specific token (here WETH-derived ETH), leaving the native transfer stranded with no recovery path.

### Impact Explanation
Any unprivileged user (e.g., a lender or third party interacting with the strategy contract directly, or a contract whose fallback sends ETH to the strategy) permanently loses 100% of the ETH sent. There is no `sweep`/`rescue` that handles the native balance in `ConvexBaseStrategy`/`BaseStrategy` (ERC20 sweeps cannot move ETH), and `_depositInCurve` never consumes `address(this).balance - _balance`. The ETH remains locked in the contract for the lifetime of the deployment — permanent freezing of user funds, quantified as the full `msg.value` transferred.

### Likelihood Explanation
The strategy is deployed behind an `IdleCDO`, but it is a standalone contract with a public unrestricted `receive()`. A mistaken plain ETH transfer, a WETH-withdrawal pattern that sends ETH to the strategy address instead of the caller, or a wallet/aggregator sending ETH to the wrong contract all hit `receive()` and irreversibly lock funds. Mistaken native-token transfers to contracts are a recurring real-world occurrence; likelihood is moderate, matching the report's Medium classification. Unlike `LidoCDOTrancheGateway`, which restricts `receive()` to the WETH contract (`require(msg.sender == wethToken)`), this contract has no such guard.

### Recommendation
Either reject unaccounted ETH or consume it. Simplest: restrict `receive()` to the WETH contract only:

```solidity
receive() external payable {
    require(msg.sender == WETH, "only-weth");
}
```

(already required for the `_weth.withdraw(_balance)` callback in `_depositInCurve`). Alternatively, deposit `address(this).balance` into Curve instead of `_balance`, or add an owner-only `sweepETH` rescue function.

### Proof of Concept
Foundry fork test (mainnet, Convex ETH strategy e.g. stETH/ETH pool variant):

```solidity
function test_StuckEth() public {
    ConvexStrategyETH strat = ConvexStrategyETH(payable(STRATEGY));
    uint256 balBefore = address(strat).balance;

    // unprivileged user mistakenly sends ETH
    vm.deal(alice, 1 ether);
    vm.prank(alice);
    (bool ok,) = address(strat).call{value: 1 ether}("");
    assertTrue(ok); // receive() accepts it

    // owner harvests / deposits in curve: stray ETH is not consumed
    deal(WETH, address(strat), 10 ether);
    vm.prank(strat.owner());
    // triggers _depositInCurve -> add_liquidity{value: 10 ether} only
    strat.deposit(10 ether);

    // user's 1 ETH is still locked, no ETH sweep exists
    assertEq(address(strat).balance, balBefore + 1 ether);
}
```

The invariant broken is "the contract must not accept value it cannot credit or return" — identical to `fundBountyToken` accepting `msg.value` on an ERC20 deposit path.