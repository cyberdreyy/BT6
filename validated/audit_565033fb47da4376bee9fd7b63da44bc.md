### Title
ETH Sent With Non-WETH Swap Is Silently Retained and Consumed by Subsequent WETH Swaps — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

---

### Summary

All four swap entry-points in `MetricOmmSimpleRouter` and all `addLiquidity*` entry-points in `MetricOmmPoolLiquidityAdder` are `payable`. The internal `pay()` helper in `PeripheryPayments` uses `address(this).balance` — the **entire** contract ETH balance — when settling a WETH leg. There is no guard that rejects `msg.value > 0` when the input token is not WETH. A user who accidentally sends native ETH with a non-WETH swap leaves that ETH stranded in the router, where the very next WETH swap by any other user silently consumes it as free payment.

---

### Finding Description

`PeripheryPayments.pay()` branches on the token address:

```
} else if (token == WETH) {
    uint256 nativeBalance = address(this).balance;   // ← whole contract balance
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
} else {
    IERC20(token).safeTransferFrom(payer, recipient, value);  // ← ETH ignored
}
``` [1](#0-0) 

When `token != WETH`, the `else` branch executes a plain `safeTransferFrom` and **never touches** any native ETH that arrived with the call. That ETH accumulates in the contract.

When the next caller swaps with `token == WETH`, `address(this).balance` now includes the stranded ETH from the previous user. If `nativeBalance >= value`, the entire WETH leg is settled from the stranded ETH — the legitimate payer's WETH is never pulled.

All four router entry-points are `payable` with no `msg.value == 0` guard for non-WETH tokens: [2](#0-1) 

The `receive()` guard (`if (msg.sender != WETH) revert NotWETH()`) only fires on bare ETH transfers with no calldata; it does **not** fire when ETH is bundled with a function call. [3](#0-2) 

The same exposure exists in `MetricOmmPoolLiquidityAdder`, whose `addLiquidityExactShares` and `addLiquidityWeighted` overloads are also `payable` and settle via the same `pay()` helper. [4](#0-3) 

---

### Impact Explanation

**Direct loss of user ETH.** User A's stranded ETH is consumed by User B's WETH swap: User B pays nothing from their own wallet yet receives the full swap output. User A's ETH is gone with no on-chain record linking it to User A. The `refundETH()` helper sends the **entire** contract balance to `msg.sender`, so a bot that front-runs User A's recovery call also steals the funds. [5](#0-4) 

This satisfies the **swap conservation failure** and **direct loss of user principal** impact gates: the pool receives its owed WETH input, but that input is sourced from a third party's ETH without their consent.

---

### Likelihood Explanation

- Sending ETH with a non-WETH swap is a common user/UI mistake (wrong `value` field in a transaction).
- The exploit requires no special role, no privileged setup, and no flash loan — any subsequent WETH swap by any address drains the stranded ETH automatically.
- The `pay()` WETH branch is triggered on every WETH-input swap, so exploitation is passive and immediate.

---

### Recommendation

Add a `msg.value == 0` guard in the non-WETH path, mirroring the NestedFactory fix:

```solidity
} else {
    require(msg.value == 0, "PeripheryPayments: ETH sent for non-WETH token");
    IERC20(token).safeTransferFrom(payer, recipient, value);
}
```

Alternatively, enforce the check at each `payable` entry-point before the swap is initiated:

```solidity
function exactInputSingle(ExactInputSingleParams calldata params)
    external payable returns (uint256 amountOut)
{
    if (params.tokenIn != WETH) require(msg.value == 0, "ETH not accepted for ERC20 input");
    ...
}
``` [6](#0-5) 

---

### Proof of Concept

```
// Setup: router deployed with WETH = weth, pool(weth, token1) exists.

// Step 1 – User A accidentally sends 1 ETH with a USDC swap.
router.exactInputSingle{value: 1 ether}(ExactInputSingleParams({
    pool:           usdcPool,
    tokenIn:        address(usdc),   // NOT WETH
    tokenOut:       address(token1),
    amountIn:       1_000e6,
    ...
}));
// pay(usdc, userA, pool, 1_000e6) → safeTransferFrom branch; 1 ETH stays in router.
// assert(address(router).balance == 1 ether);

// Step 2 – User B swaps WETH → token1 sending 0 ETH.
router.exactInputSingle{value: 0}(ExactInputSingleParams({
    pool:    wethPool,
    tokenIn: address(weth),          // WETH
    amountIn: 1 ether,
    ...
}));
// pay(weth, userB, pool, 1e18):
//   nativeBalance = 1 ether  (User A's ETH)
//   nativeBalance >= value   → deposit(1 ether) + transfer WETH to pool
//   safeTransferFrom(userB, ...) is NEVER called.
// User B's WETH balance unchanged; User A's 1 ETH is gone.
``` [7](#0-6)

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

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L73-87)
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
    } else {
      IERC20(token).safeTransferFrom(payer, recipient, value);
    }
```

**File:** metric-periphery/contracts/interfaces/IMetricOmmSimpleRouter.sol (L166-174)
```text
  function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut);

  function exactInput(ExactInputParams calldata params) external payable returns (uint256 amountOut);

  // ============ Mutating: exact output ============

  function exactOutputSingle(ExactOutputSingleParams calldata params) external payable returns (uint256 amountIn);

  function exactOutput(ExactOutputParams calldata params) external payable returns (uint256 amountIn);
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L56-68)
```text
  function addLiquidityExactShares(
    address pool,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    uint256 maxAmountToken0,
    uint256 maxAmountToken1,
    bytes calldata extensionData
  ) external payable override returns (uint256 amount0Added, uint256 amount1Added) {
    _validateOwner(owner);
    _validateDeltas(deltas);
    return _addLiquidity(pool, owner, salt, deltas, msg.sender, maxAmountToken0, maxAmountToken1, extensionData);
  }
```
