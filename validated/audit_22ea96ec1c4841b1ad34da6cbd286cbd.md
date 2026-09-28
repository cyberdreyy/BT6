### Title
Withdrawals revert when the strategy returns less underlying than requested, freezing funds on legitimate provider losses - (File: contracts/IdleCDO.sol)

### Summary
`IdleCDO._withdraw` liquidates the shortfall via `_liquidate(..., revertIfTooLow)`, which reverts with `AmountTooLow` whenever the strategy returns even slightly less than requested (only ~100 wei or `liquidationTolerance` is tolerated). This mirrors the Illuminate/Sense bug: the code treats any redemption shortfall as an anomaly to revert on rather than a legitimate loss to socialize, so a real loss event in the lending provider (e.g., stMATIC slashing on the PoLido variant, withdrawal restrictions/fees) that is too small to trip `_checkDefault` makes `withdrawAA`/`withdrawBB` permanently revert until governance intervenes.

### Finding Description
In `IdleCDO._withdraw`, when the unlent balance is insufficient, the contract pulls the remainder from the strategy: [1](#0-0) 

`_liquidate` then enforces near-1:1 redemption: [2](#0-1) 

If `revertIfTooLow` is enabled (the flag exists precisely to protect against strategy misbehavior, `contracts/IdleCDOStorage.sol:38`), any shortfall greater than ~100 wei reverts the entire withdrawal. `_checkDefault` only catches *price* decreases above `maxDecreaseDefault`; a small realized loss (slash, provider withdrawal fee/penalty, negative rebase) leaves `_strategyPrice()` within tolerance, so the default path is not triggered and there is no code path that allows the loss. All tranche holders' withdrawals revert; the only escape is the owner unsetting `revertIfTooLow` or invoking `_emergencyShutdown`, both trusted interventions. When `revertIfTooLow` is false the loss is instead silently borne by the withdrawer while `lastNAV` is reduced by the full `_want` (`IdleCDO.sol:502`, `513-522`), so losses are also not socialized correctly — but the flag-on path is the freezing one.

### Impact Explanation
Temporary/permanent freezing of funds: every `withdrawAA`/`withdrawBB` reverts with `AmountTooLow` while the strategy can only return less than requested. Quantified impact = the full NAV of the affected tranche(s) for the duration of the freeze; unfreezing requires a privileged call, which (as in the source report) defeats trustless redemption.

### Likelihood Explanation
Requires (a) `revertIfTooLow == true` and (b) the provider returning less than requested by more than `liquidationTolerance` without dropping `_strategyPrice()` below `maxDecreaseDefault`. Lido/PoLido-style slashing or withdrawal-penalty scenarios are the canonical triggers (the repo ships `IdleCDOPoLidoVariant` and lido tests). Medium-low likelihood, consistent with the original finding's accepted downgrade to medium.

### Recommendation
Mirror the source fix: when the provider can signal a verified/legitimate loss (or when `revertIfTooLow` is unset), proceed with the reduced `toRedeem` and reduce the tranche NAV by the *actually received* amount rather than reverting, so losses are socialized across remaining holders instead of freezing redemptions. Alternatively cap the revert to cases where the strategy is provably misbehaving (e.g., only when `_strategyPrice` did not decrease at all).

### Proof of Concept
Foundry fork PoC sketch:

```solidity
// contracts/IdleCDO.sol surface under test
function testWithdrawFrozenOnProviderShortfall() public {
    // Setup: CDO with AA deposits fully lent to strategy; revertIfTooLow = true
    vm.prank(owner); idleCDO.setFlag(REVERT_IF_TOO_LOW_SLOT, true); // or setter

    // Simulate legitimate small loss: make redeemUnderlying return amount - delta
    // e.g., slash stMATIC / mock strategy returning _amount - 1e4 (> 100 wei tolerance)
    strategy.setRedeemShortfall(1e4); // below maxDecreaseDefault so _checkDefault passes

    uint256 balBefore = underlying.balanceOf(user);
    vm.expectRevert(AmountTooLow.selector);
    vm.prank(user); idleCDO.withdrawAA(userShares);

    assertEq(underlying.balanceOf(user), balBefore); // funds frozen
}
```

Key points demonstrated: `_checkDefault` does not revert (price drop < `maxDecreaseDefault`), `_liquidate` reverts on a real-but-small shortfall, and no unprivileged path lets users redeem at a loss-adjusted amount.

Caveat: I could not fully verify how `revertIfTooLow` is set in production deployments or whether the epoch-variant `requestWithdraw`/`claimWithdrawRequest` path in `IdleCreditVault` has the same strict shortfall check; if all live CDOs run with `revertIfTooLow == false`, the freeze vector collapses to the (accepted-design) per-withdrawer loss instead.

### Citations

**File:** contracts/IdleCDO.sol (L493-500)
```text
    toRedeem = _amount * _tranchePrice(_tranche) / ONE_TRANCHE_TOKEN;
    uint256 _want = toRedeem;
    if (toRedeem > balanceUnderlying) {
      // if the unlent balance is not enough we try to redeem what's missing directly from the strategy
      // and then add it to the current unlent balance
      // NOTE: A difference of up to 100 wei due to rounding is tolerated
      toRedeem = _liquidate(toRedeem - balanceUnderlying, revertIfTooLow) + balanceUnderlying;
    }
```

**File:** contracts/IdleCDO.sol (L584-597)
```text
  function _liquidate(uint256 _amount, bool _revertIfNeeded) internal virtual returns (uint256 _redeemedTokens) {
    _redeemedTokens = IIdleCDOStrategy(strategy).redeemUnderlying(_amount);
    if (_revertIfNeeded) {
      uint256 _tolerance = liquidationTolerance;
      if (_tolerance == 0) {
        _tolerance = 100;
      }
      // keep `_tolerance` wei as margin for rounding errors
      if (_redeemedTokens + _tolerance < _amount) revert AmountTooLow();
    }

    if (_redeemedTokens > _amount) {
      _redeemedTokens = _amount;
    }
```
