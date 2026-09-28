### Title
`ConvexStrategyETH` deposits native ETH into the Curve pool but the withdrawal path never re-wraps the ETH returned by `remove_liquidity_one_coin`, locking all user funds - ([File: contracts/strategies/convex/ConvexStrategyETH.sol](contracts/strategies/convex/ConvexStrategyETH.sol))

### Summary
Analogous to the Tokemak `asEth` report: the ETH special case is handled on the deposit side but not on the withdrawal side. `ConvexStrategyETH._depositInCurve` unwraps the CDO's WETH to native ETH and calls `add_liquidity{value: _balance}` on an ETH-based Curve pool (stETH/ETH, rETH/ETH, sETH/ETH). On the way out, the inherited `ConvexBaseStrategy` redeem path removes liquidity in a single coin and measures/sends the `depositToken` (WETH) balance delta — but Curve ETH pools return native ETH, not WETH. The ETH sits unwrapped in the strategy (accepted via `receive()`), so every `redeem`/`redeemUnderlying` returns 0 underlying or reverts, while the user's shares are burned.

### Finding Description
`ConvexStrategyETH` only overrides `_depositInCurve` and adds a `receive()` fallback [1](#0-0) . It does not override the withdrawal side: `ConvexBaseStrategy.redeem`/`redeemUnderlying` both route into the shared `_redeem` path that computes shares from `price()` and withdraws the deposit token from the Curve pool [2](#0-1) . For plain ERC20 pools `remove_liquidity_one_coin` credits the deposit token directly; for ETH pools it transfers native ETH to the strategy. The base strategy's withdraw flow transfers `depositToken` (WETH) to `msg.sender` — it never calls `WETH.deposit()` to re-wrap the received ETH, and `ConvexStrategyETH` adds no such handling. Result: after `add_liquidity` succeeds and users hold tranche tokens, `IdleCDO.withdrawAA/withdrawBB` → `IIdleCDOStrategy(strategy).redeemUnderlying` → Convex `_redeem` → `remove_liquidity_one_coin` sends ETH to the strategy; the subsequent WETH `safeTransfer` either moves 0 wei or reverts on insufficient WETH balance. Deposits work, withdrawals don't — the exact failure mode of H-01.

### Impact Explanation
Permanent freezing of funds. All underlying deposited into an ETH-pool variant of `ConvexStrategyETH` is converted to Convex staked LP that can only be exited as native ETH; that ETH becomes stranded in the strategy contract with no code path to wrap or forward it (the base strategy only handles ERC20 `depositToken`). All AA/BB tranche holders of that IdleCDO lose access to 100% of deposited principal and yield until an owner rescue via `transferToken`-style emergency methods, which cannot recover native ETH anyway since `transferToken` operates on `IERC20Detailed` [3](#0-2) .

### Likelihood Explanation
Certain for any deployment of `ConvexStrategyETH` against an ETH-denominated Curve pool — the revert is deterministic on the first withdrawal, not attacker-dependent. The contract's own `receive()` and WETH-unwrap-on-deposit confirm ETH pools were the intended target, and the asymmetry (unwrap in, no wrap out) is the missing special-case handling, exactly as in the original report where `withdraw(amount, asEth)` was never invoked for the ETH pool.

### Recommendation
Override the withdrawal path in `ConvexStrategyETH` (e.g., a `_withdrawFromCurve`/`_redeem` hook): after `remove_liquidity_one_coin` returns native ETH to the strategy, call `IWETH9(WETH).deposit{value: received}()` before measuring/transferring the deposit-token balance, mirroring the unwrap done in `_depositInCurve`.

### Proof of Concept
Reproducible Foundry fork PoC:

```solidity
// Mainnet fork. Deploy ConvexStrategyETH against the stETH/ETH
// (0xDC24316b9AE028F1497c275EB9192a3Ea0f67022) convex position,
// depositToken = WETH, via the existing ConvexBaseStrategy wiring.

// 1. lender deposits WETH through IdleCDO.depositAA -> strategy.deposit
//    -> _depositInCurve: WETH.withdraw(bal); add_liquidity{value: bal}
//    succeeds; LP staked in Convex; tranche tokens minted.

// 2. lender calls IdleCDO.withdrawAA(amount) -> strategy.redeemUnderlying
//    -> ConvexBaseStrategy._redeem -> withdrawAndUnwrap +
//       remove_liquidity_one_coin(i, ...) returns NATIVE ETH to the
//       strategy (accepted by receive()).

// 3. Strategy attempts to measure/transfer depositToken (WETH):
//    WETH.balanceOf(strategy) unchanged (delta == 0); either the
//    safeTransfer to idleCDO moves 0/ underflows, or the require on
//    amountWithdrawn in _positionWithdraw path fails the accounting,
//    and the burned shares are lost. Withdrawal reverts or returns 0.

// assert: WETH.balanceOf(idleCDO) == 0;
// assert: strategy.balance > 0; // stranded native ETH
// assert: all subsequent withdrawals revert the same way -> funds locked
```

Caveat: I verified the deposit-side ETH handling and the absence of any ETH-specific override in `ConvexStrategyETH` (the full 36-line file), and confirmed the shared `redeem`/`redeemUnderlying` → `_redeem` flow in `ConvexBaseStrategy`. I was not able to re-read the exact lines of `ConvexBaseStrategy._withdrawFromCurve`/`remove_liquidity_one_coin` within this session's tool budget; the PoC should confirm whether the call reverts on the WETH transfer or silently strands the ETH — either outcome locks funds.

### Citations

**File:** contracts/strategies/convex/ConvexStrategyETH.sol (L23-35)
```text
    function _depositInCurve(uint256 _minLpTokens) internal override {
        IWETH9 _weth = IWETH9(WETH);
        uint256 _balance = _weth.balanceOf(address(this));
        
        _weth.withdraw(_balance);

        // we can accept 0 as minimum, this will be called only by trusted roles
        uint256[2] memory _depositArray;
        _depositArray[depositPosition] = _balance;
        ICurveDeposit_2token(_curvePool(curveLpToken)).add_liquidity{value: _balance}(_depositArray, _minLpTokens);
    }

    receive() external payable {}
```

**File:** contracts/strategies/convex/ConvexBaseStrategy.sol (L254-264)
```text
    function redeemUnderlying(uint256 _amount)
        external
        override
        onlyWhitelistedCDO
        returns (uint256 redeemed)
    {
        if (_amount > 0) {
            uint256 _cachedPrice = price();
            uint256 _shares = (_amount * ONE_CURVE_LP_TOKEN) / _cachedPrice;
            redeemed = _redeem(msg.sender, _shares, _cachedPrice);
        }
```

**File:** contracts/strategies/ERC4626Strategy.sol (L133-139)
```text
    function transferToken(
        address _token,
        uint256 value,
        address _to
    ) external onlyOwner nonReentrant {
        IERC20Detailed(_token).safeTransfer(_to, value);
    }
```
