Looking at the actual code in `PeripheryPayments.sol` and `MetricOmmSimpleRouter.sol` to trace the exact execution path.

The vulnerability is real. Here is the full analysis:

---

### Title
Residual Native ETH from Prior Users Consumed by Subsequent WETH Swap in `pay()` Hybrid Branch — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

### Summary

The `pay()` function's hybrid WETH branch reads `address(this).balance` as a shared pool of native ETH without any per-user attribution. Any residual ETH left on the router from a prior user's overpayment is silently consumed as part of a subsequent user's WETH swap obligation, causing direct loss of the prior user's native ETH.

### Finding Description

`pay()` in `PeripheryPayments.sol` handles WETH payments with three branches keyed on `address(this).balance`: [1](#0-0) 

```
} else if (token == WETH) {
    uint256 nativeBalance = address(this).balance;
    if (nativeBalance >= value) {
        IWETH9(WETH).deposit{value: value}();
        IERC20(WETH).safeTransfer(recipient, value);
    } else if (nativeBalance > 0) {          // hybrid branch
        IWETH9(WETH).deposit{value: nativeBalance}();
        IERC20(WETH).safeTransfer(recipient, nativeBalance);
        IERC20(WETH).safeTransferFrom(payer, recipient, value - nativeBalance);
    } else {
        IERC20(WETH).safeTransferFrom(payer, recipient, value);
    }
```

`address(this).balance` is the router's **total** native ETH balance — it is not scoped to the current transaction's `msg.value`. The router is a shared, stateless contract. ETH sent by userA in transaction T₁ that is not refunded in the same call persists on the router and is indistinguishable from ETH sent by userB in transaction T₂.

**Attack path (two separate transactions):**

1. **Tx₁ — UserA** calls `exactInputSingle{value: 2 ether}` with `tokenIn=WETH`, `amountIn=1 ether`. During the swap callback `_justPayCallback` → `pay()`, `nativeBalance = 2 ether ≥ 1 ether`, so exactly 1 ether is wrapped and forwarded to the pool. The remaining 1 ether stays on the router. UserA does not call `refundETH()` in the same transaction (or calls it in a separate, later transaction). [2](#0-1) 

2. **Tx₂ — UserB** calls `exactInputSingle` (no ETH sent) with `tokenIn=WETH`, `amountIn=0.5 ether`. During the callback, `pay()` is called with `payer=userB`, `value=0.5 ether`. `nativeBalance = 1 ether ≥ 0.5 ether`, so the router wraps **userA's** 1 ether (taking 0.5 ether of it) and sends it to the pool. UserB's swap is settled entirely from userA's residual ETH — userB pays nothing from their own WETH balance.

3. **Tx₃ — UserA** calls `refundETH()`. The router now holds only 0.5 ether instead of 1 ether. UserA permanently loses 0.5 ether. [3](#0-2) 

The `receive()` guard only prevents unsolicited ETH pushes; it does not prevent `msg.value` attached to `payable` entry points from accumulating on the router across transactions. [4](#0-3) 

### Impact Explanation

Direct loss of native ETH for any user who:
- Sends `msg.value > amountIn` to a WETH swap entry point (a common pattern — users overpay to avoid reverts), **and**
- Does not atomically combine the swap with `refundETH()` in a single `multicall`.

The existing test suite demonstrates the recommended safe pattern (`multicall` + `refundETH`) but does not enforce it: [5](#0-4) 

A MEV searcher can monitor the mempool for `exactInputSingle{value: X}` calls where `X > amountIn`, wait for inclusion, then immediately submit a WETH swap to drain the residual ETH before the victim's `refundETH()` lands. Loss is bounded only by the victim's overpayment amount.

### Likelihood Explanation

- Overpaying ETH on WETH swaps is a standard defensive pattern (users set `msg.value` slightly above the quoted amount to absorb price movement).
- The `multicall` + `refundETH` pattern is not enforced by the router; `exactInputSingle` and `exactOutputSingle` are independently callable `payable` functions.
- A passive MEV bot requires no special privileges — it only needs to submit a WETH swap after observing residual ETH on the router.

### Recommendation

Track the current transaction's contributed native ETH in transient storage at the entry point (e.g., store `msg.value` in a transient slot at the start of `exactInputSingle`/`exactOutputSingle`/`exactInput`/`exactOutput`) and cap the amount `pay()` may consume from `address(this).balance` to that recorded value. Alternatively, require that the hybrid branch only fires when `msg.value > 0` in the current call frame, which is already available via transient storage patterns used elsewhere in the router. [6](#0-5) 

### Proof of Concept

```solidity
// Foundry integration test (pseudocode)
function test_residualEthStolenBySubsequentWethSwap() public {
    // UserA sends 2 ether but only needs 1 ether for the swap
    vm.deal(userA, 2 ether);
    vm.prank(userA);
    router.exactInputSingle{value: 2 ether}(ExactInputSingleParams({
        pool: pool, tokenIn: WETH, tokenOut: token1,
        zeroForOne: true, amountIn: 1 ether,
        amountOutMinimum: 0, recipient: userA,
        deadline: block.timestamp + 1, priceLimitX64: 0, extensionData: ""
    }));
    // Router now holds 1 ether residual (userA did not call refundETH)
    assertEq(address(router).balance, 1 ether);

    // UserB swaps WETH->token1 with no ETH sent; WETH allowance covers full amount
    // but pay() consumes userA's residual ETH instead
    vm.prank(userB);
    router.exactInputSingle(ExactInputSingleParams({
        pool: pool, tokenIn: WETH, tokenOut: token1,
        zeroForOne: true, amountIn: 0.5 ether,
        amountOutMinimum: 0, recipient: userB,
        deadline: block.timestamp + 1, priceLimitX64: 0, extensionData: ""
    }));
    // UserB's WETH balance is unchanged — paid with userA's ETH
    assertEq(weth.balanceOf(userB), initialWethB); // unchanged

    // UserA tries to refund — only 0.5 ether remains
    vm.prank(userA);
    router.refundETH();
    assertEq(userA.balance, 0.5 ether); // lost 0.5 ether
}
```

### Citations

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L32-34)
```text
  receive() external payable {
    if (msg.sender != WETH) revert NotWETH();
  }
```

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L58-63)
```text
  function refundETH() external payable override {
    uint256 balance = address(this).balance;
    if (balance > 0) {
      _transferETH(msg.sender, balance);
    }
  }
```

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L73-84)
```text
    } else if (token == WETH) {
      uint256 nativeBalance = address(this).balance;
      if (nativeBalance >= value) {
        IWETH9(WETH).deposit{value: value}();
        IERC20(WETH).safeTransfer(recipient, value);
      } else if (nativeBalance > 0) {
        IWETH9(WETH).deposit{value: nativeBalance}();
        IERC20(WETH).safeTransfer(recipient, nativeBalance);
        IERC20(WETH).safeTransferFrom(payer, recipient, value - nativeBalance);
      } else {
        IERC20(WETH).safeTransferFrom(payer, recipient, value);
      }
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L192-199)
```text
  function _justPayCallback(int256 amount0Delta, int256 amount1Delta) private {
    pay(
      _getTokenToPay(),
      _getPayer(),
      msg.sender,
      uint256(MetricOmmSwapResults.extractPositiveAmount(amount0Delta, amount1Delta))
    );
  }
```

**File:** metric-periphery/test/MetricOmmSimpleRouter.native.t.sol (L106-133)
```text
  function test_multicall_ethInput_exactInputSingle_refundsUnusedEth() public {
    uint128 amountIn = 1_000;
    uint256 msgValue = 2 ether;
    uint256 swapperEthBefore = swapper.balance;

    vm.prank(swapper);
    bytes[] memory calls = new bytes[](2);
    calls[0] = abi.encodeWithSelector(
      router.exactInputSingle.selector,
      IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: address(pool),
        tokenIn: address(weth),
        tokenOut: address(token1),
        zeroForOne: true,
        amountIn: amountIn,
        amountOutMinimum: 0,
        recipient: recipient,
        deadline: _deadline(),
        priceLimitX64: 0,
        extensionData: ""
      })
    );
    calls[1] = abi.encodeWithSelector(router.refundETH.selector);
    router.multicall{value: msgValue}(calls);

    assertEq(swapper.balance, swapperEthBefore - amountIn, "unused eth refunded");
    _assertRouterEmpty();
  }
```

**File:** metric-periphery/contracts/libraries/TransientCallbackPool.sol (L1-1)
```text
// SPDX-License-Identifier: MIT
```
