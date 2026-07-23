The vulnerability is real. Here is the full analysis:

---

### Title
`PeripheryPayments.pay()` WETH Hybrid Path Consumes Arbitrary Contract ETH Before Pulling From Payer — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

---

### Summary

`PeripheryPayments.pay()` contains a WETH hybrid path that checks `address(this).balance` and uses the contract's entire native ETH balance to fund a WETH payment before pulling from the designated `payer`. Because several functions on `MetricOmmPoolLiquidityAdder` are `payable`, ETH can accumulate in the contract across transactions. Any attacker can then call `addLiquidityExactShares` with `token0 = WETH` and have the contract's ETH (belonging to other users) consumed to fund their liquidity deposit, with zero WETH pulled from the attacker.

---

### Finding Description

The `pay()` function in `PeripheryPayments` implements a three-branch WETH path: [1](#0-0) 

When `token == WETH` and `payer != address(this)`, the function reads `address(this).balance` — the contract's **total** native ETH balance — and uses it first. It does not verify that this ETH was deposited by the current `payer`. If `nativeBalance >= value`, the payer's WETH is never pulled at all.

The `receive()` guard blocks plain ETH transfers: [2](#0-1) 

However, it does **not** block ETH sent via `payable` function calls. The following functions on `MetricOmmPoolLiquidityAdder` are all `payable` and do not consume `msg.value` themselves:

- `addLiquidityExactShares(...)` — `payable`
- `addLiquidityWeighted(...)` — `payable`
- `sweepToken(...)` — `payable` (inherited)
- `unwrapWETH9(...)` — `payable` (inherited, only forwards WETH balance, not `msg.value`) [3](#0-2) 

ETH sent with any of these calls stays in the contract if not explicitly refunded. A user who calls `addLiquidityExactShares{value: X}(...)` and the pool consumes less than `X` worth of WETH will have residual ETH stranded in the contract.

In the callback, `payer` is always `msg.sender` of the original `addLiquidityExactShares` call: [4](#0-3) 

When `pay(token0, payer, pool, amount0Delta)` is called and `token0 == WETH`, the contract's entire ETH balance is consumed first — regardless of who deposited it.

---

### Impact Explanation

**Direct loss of user principal.** ETH left in the contract by any user (e.g., from a `payable` call with excess `msg.value`, or from a multicall that omitted `refundETH()`) can be stolen by an attacker:

1. Attacker calls `addLiquidityExactShares` with `token0 = WETH`, no ETH attached.
2. Pool calls back `metricOmmModifyLiquidityCallback` with `amount0Delta = V`.
3. `pay(WETH, attacker, pool, V)` fires; `nativeBalance >= V` (victim's ETH) → contract wraps victim's ETH and sends WETH to pool.
4. Attacker receives LP shares; attacker's WETH is never pulled; victim's ETH is gone.

This is a direct, unprivileged theft of user funds above Sherlock Medium/High thresholds.

---

### Likelihood Explanation

- The `payable` modifier on `addLiquidityExactShares` and `addLiquidityWeighted` invites users to send ETH for WETH payment (standard Uniswap-style pattern).
- Any user who sends slightly more ETH than consumed, or who omits `refundETH()` from a multicall, leaves ETH in the contract.
- The attack requires no special permissions, no malicious pool, and no oracle manipulation — just a public call to `addLiquidityExactShares` on a legitimate WETH pool.

---

### Recommendation

In `pay()`, when `payer != address(this)` and `token == WETH`, only use `msg.value` (passed explicitly) as the native ETH contribution, not `address(this).balance`. Alternatively, track per-caller ETH deposits in transient storage and only consume the current caller's deposited amount. The contract should never silently consume ETH that was not deposited in the current call context by the current payer.

---

### Proof of Concept

```solidity
// 1. Victim sends ETH via a payable call (e.g., sweepToken with msg.value)
//    ETH stays in MetricOmmPoolLiquidityAdder
adder.sweepToken{value: 1 ether}(address(someToken), 0, victim);
// adder.balance == 1 ether

// 2. Attacker calls addLiquidityExactShares with token0=WETH, no ETH
//    maxAmountToken0 = 1 ether, attacker has WETH approval but it won't be used
adder.addLiquidityExactShares(pool, attacker, 0, deltas, 1 ether, 0, "");

// 3. In metricOmmModifyLiquidityCallback:
//    pay(WETH, attacker, pool, 1 ether)
//    → nativeBalance (1 ether) >= value (1 ether)
//    → contract wraps victim's 1 ETH → sends 1 WETH to pool
//    → attacker's WETH never pulled

// Assert: adder.balance == 0 (victim's ETH gone)
//         attacker holds LP shares worth 1 WETH
//         victim's 1 ETH is lost
``` [5](#0-4) [6](#0-5)

### Citations

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L32-34)
```text
  receive() external payable {
    if (msg.sender != WETH) revert NotWETH();
  }
```

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L69-88)
```text
  function pay(address token, address payer, address recipient, uint256 value) internal {
    // If the payer is contract it means we are in the middle of a path. In the middle of a path we operate on ERC20 only.
    if (payer == address(this)) {
      IERC20(token).safeTransfer(recipient, value);
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
  }
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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L162-177)
```text
    (address expectedPool, address payer, uint256 max0, uint256 max1) = _loadPayContext();
    if (expectedPool == address(0)) revert CallbackContextNotActive();
    if (msg.sender != expectedPool) revert InvalidCallbackCaller(msg.sender, expectedPool);
    if (amount0Delta > max0 || amount1Delta > max1) {
      revert MaxAmountExceeded(amount0Delta, amount1Delta, max0, max1);
    }

    PoolImmutables memory imm = IMetricOmmPool(msg.sender).getImmutables();
    address token0 = imm.token0;
    address token1 = imm.token1;
    if (amount0Delta > 0) {
      pay(token0, payer, msg.sender, amount0Delta);
    }
    if (amount1Delta > 0) {
      pay(token1, payer, msg.sender, amount1Delta);
    }
```
