### Title
Unused ETH Not Refunded in `exactInputSingle` When Price Limit Is Reached — (`metric-periphery/contracts/MetricOmmSimpleRouter.sol`)

---

### Summary

`MetricOmmSimpleRouter.exactInputSingle` accepts a `priceLimitX64` that can cause the pool to partially consume the user's input. When the user pays with native ETH (sending `msg.value`), the swap callback only pays the pool the *actual* amount consumed; the unspent ETH is silently left in the router with no refund path called, making it permanently stranded or extractable by any caller.

---

### Finding Description

`exactInputSingle` is `payable` and forwards the full `params.amountIn` to the pool as the exact-input amount, while also forwarding a caller-supplied `priceLimitX64`: [1](#0-0) 

The pool's `swap()` stops early when the price limit is reached, returning deltas that reflect only the *partial* fill. The callback `_justPayCallback` then pays the pool exactly the positive delta — the actual amount consumed — not the full `params.amountIn`: [2](#0-1) 

When `tokenIn` is WETH and the user sent native ETH, `PeripheryPayments.pay()` wraps only the partial amount and forwards it to the pool. The remainder of `msg.value` stays in the router. `exactInputSingle` performs no post-swap refund and no check that `amountInActual == params.amountIn`: [3](#0-2) 

By contrast, `exactInput` (multi-hop, no price limit) explicitly reverts if the actual input consumed is less than the specified amount: [4](#0-3) 

No equivalent guard exists in `exactInputSingle`.

---

### Impact Explanation

A user who sells native ETH with a `priceLimitX64` that is reached mid-swap loses the unspent ETH to the router contract. The amount lost equals `params.amountIn − actualAmountConsumed`. Because the router is a shared contract, any address can drain the stranded ETH (e.g., via a public `refundETH` helper in `PeripheryPayments`, or by any other ETH-pull path). This is a direct, quantifiable loss of user principal with no recovery mechanism inside `exactInputSingle` itself.

---

### Likelihood Explanation

The trigger requires three ordinary user actions: (1) call `exactInputSingle` with native ETH, (2) set a non-zero `priceLimitX64`, and (3) have the price limit actually reached during the swap. All three are normal, documented usage patterns for a limit-price-aware ETH seller. No privileged role, malicious setup, or non-standard token is needed.

---

### Recommendation

After the `swap()` call in `exactInputSingle`, compare the actual input consumed against `params.amountIn` and refund any surplus ETH to the caller:

```solidity
// After swap returns:
int128 amountInActual = MetricOmmSwapResults.extractAmountIn(
    params.zeroForOne, amount0Delta, amount1Delta
);
if (uint128(amountInActual) < params.amountIn) {
    // refund unspent ETH
    uint256 refund = params.amountIn - uint128(amountInActual);
    Address.sendValue(payable(msg.sender), refund);
}
```

Alternatively, document clearly that callers must wrap `exactInputSingle` in a `multicall` that appends a `refundETH` call whenever a price limit is set and native ETH is used.

---

### Proof of Concept

1. Alice wants to sell 1 ETH for token B, but only up to price limit `P`.
2. Alice calls `exactInputSingle({tokenIn: WETH, amountIn: 1e18, priceLimitX64: P, ...})` and sends `msg.value = 1e18`.
3. The pool executes a partial swap: only 0.7 ETH is consumed before price `P` is reached.
4. `metricOmmSwapCallback` fires; `_justPayCallback` calls `pay(WETH, Alice, pool, 0.7e18)` — wrapping and forwarding only 0.7 ETH.
5. `exactInputSingle` returns `amountOut` (the tokens received for 0.7 ETH) and exits — no refund.
6. 0.3 ETH remains in `MetricOmmSimpleRouter`.
7. A MEV bot (or any address) calls the router's ETH-withdrawal helper and collects Alice's 0.3 ETH.

Alice spent 1 ETH but received output worth only 0.7 ETH; the 0.3 ETH difference is unrecoverable from within the swap call.

### Citations

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L67-86)
```text
  function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData
      );
    int128 out = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    amountOut = MetricOmmSwapInputs.int128ToUint128(out);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L114-115)
```text
      int128 amountInActual = MetricOmmSwapResults.extractAmountIn(zeroForOne, amount0Delta, amount1Delta);
      if (amountInActual < amount) revert InvalidInputAmountAtHop(uint8(i), amountInActual, amount);
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
