### Title
Stuck ETH in `PeripheryPayments.pay()` is silently consumed by any subsequent WETH-input swap or liquidity add — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

---

### Summary

`PeripheryPayments.pay()` uses the raw `address(this).balance` to cover WETH payments. Because `unwrapWETH9()` and `sweepToken()` are marked `payable` but never consume `msg.value`, any ETH sent to the router via those entry points is silently retained. A subsequent caller whose input token is WETH will have their payment fully or partially subsidised by that stranded ETH — paying less (or nothing) from their own wallet while the original sender loses their funds.

---

### Finding Description

`pay()` branches on `token == WETH` and reads the contract's entire native balance: [1](#0-0) 

```solidity
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
}
```

`address(this).balance` is the **total** contract balance — it does not distinguish ETH that arrived in the current transaction from ETH left over from a prior one.

The `receive()` guard correctly blocks plain ETH transfers from non-WETH addresses: [2](#0-1) 

However, three `payable` entry points accept `msg.value` from **any** caller without consuming it: [3](#0-2) 

`unwrapWETH9()` operates on the contract's WETH ERC-20 balance; `sweepToken()` operates on an ERC-20 balance. Neither touches `msg.value`. ETH sent alongside either call is silently retained.

`multicall()` is also `payable` and delegates to these same functions: [4](#0-3) 

The swap callback path that triggers `pay()` with `payer = msg.sender` (i.e., the first hop of any WETH-input swap): [5](#0-4) 

The same `pay()` path is reached during liquidity additions when a pool token is WETH: [6](#0-5) 

---

### Impact Explanation

A user who accidentally sends ETH alongside `unwrapWETH9()` or `sweepToken()` (a realistic mistake when constructing a multicall without a trailing `refundETH()`) permanently loses that ETH. Any subsequent caller whose input token is WETH will have their pool payment covered — fully or partially — by the stranded balance, paying less from their own wallet. This is a direct loss of user principal and a swap-conservation failure: the pool receives the correct WETH amount, but it is sourced from a third party's funds rather than the actual payer.

---

### Likelihood Explanation

Multicall-based UIs routinely bundle `unwrapWETH9` or `sweepToken` with ETH-valued calls. A user who omits the trailing `refundETH()` step, or whose frontend constructs the bundle incorrectly, will strand ETH. The exploit requires no special privilege: any address can call `exactInputSingle` / `exactInput` / `addLiquidityExactShares` with WETH as the input token immediately after.

---

### Recommendation

1. **Remove `payable` from `unwrapWETH9()` and `sweepToken()`** — neither function has any use for `msg.value`. Removing the modifier prevents ETH from entering the contract through those paths entirely.

2. **Alternatively**, add a `msg.value == 0` guard (or an explicit refund of `msg.value`) at the top of each function so that any accidentally forwarded ETH is immediately returned.

3. **Do not change `pay()`** to use `msg.value` instead of `address(this).balance` — that would break the intended multicall pattern where ETH is deposited once and consumed across multiple hops.

---

### Proof of Concept

```
1. Victim calls unwrapWETH9(0, victim) with msg.value = 1 ETH.
   - The function reads IERC20(WETH).balanceOf(address(this)) == 0, does nothing.
   - 1 ETH is now stranded in the router.

2. Attacker calls exactInputSingle({
       pool: wethPool,
       tokenIn: WETH,
       amountIn: 1 ETH,
       recipient: attacker,
       ...
   }) with msg.value = 0.

3. Pool calls metricOmmSwapCallback → _justPayCallback →
   pay(WETH, attacker, pool, 1e18).

4. pay() reads nativeBalance = 1 ETH (victim's funds).
   nativeBalance >= value → wraps 1 ETH, transfers WETH to pool.
   No IERC20.safeTransferFrom is ever called on the attacker.

5. Attacker receives swap output; victim's 1 ETH is gone.
```

### Citations

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L32-34)
```text
  receive() external payable {
    if (msg.sender != WETH) revert NotWETH();
  }
```

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L37-55)
```text
  function unwrapWETH9(uint256 amountMinimum, address recipient) public payable override {
    uint256 balanceWETH = IERC20(WETH).balanceOf(address(this));
    if (balanceWETH < amountMinimum) revert InsufficientWETH(amountMinimum, balanceWETH);

    if (balanceWETH > 0) {
      IWETH9(WETH).withdraw(balanceWETH);
      _transferETH(recipient, balanceWETH);
    }
  }

  /// @inheritdoc IPeripheryPayments
  function sweepToken(address token, uint256 amountMinimum, address recipient) public payable override {
    uint256 balanceToken = IERC20(token).balanceOf(address(this));
    if (balanceToken < amountMinimum) revert InsufficientToken(token, amountMinimum, balanceToken);

    if (balanceToken > 0) {
      IERC20(token).safeTransfer(recipient, balanceToken);
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L39-44)
```text
  function multicall(bytes[] calldata data) public payable override returns (bytes[] memory results) {
    results = new bytes[](data.length);
    for (uint256 i = 0; i < data.length; i++) {
      results[i] = Address.functionDelegateCall(address(this), data[i]);
    }
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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L172-177)
```text
    if (amount0Delta > 0) {
      pay(token0, payer, msg.sender, amount0Delta);
    }
    if (amount1Delta > 0) {
      pay(token1, payer, msg.sender, amount1Delta);
    }
```
