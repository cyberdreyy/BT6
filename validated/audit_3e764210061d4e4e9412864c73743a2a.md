### Title
`ProgrammableBorrower._depositToVault` / `vault.withdraw` lack slippage protection, allowing an ERC4626 share-price sandwich that socializes a permanent vault loss to the pool - ([File: contracts/strategies/idle/ProgrammableBorrower.sol](contracts/strategies/idle/ProgrammableBorrower.sol))

### Summary
`ProgrammableBorrower` parks all idle facility liquidity in an owner-chosen ERC4626 vault. Every entry point that moves assets in or out of that vault — `_depositToVault` (called from `onStartEpoch` and `_repay`) and `vault.withdraw` (called from `onStopEpoch` and `_borrow`) — accepts the spot `convertToShares`/`convertToAssets` price with no `minSharesOut` / `maxSharesIn` bound. An unprivileged attacker who holds shares of that external vault can inflate its share price (direct asset donation / first-depositor inflation) immediately before the honest `startEpoch`/`stopEpoch`/`repay` call, forcing the facility to mint near-zero shares or burn excess shares. The missing value is then recorded as a real vault loss in epoch accounting (`_vaultNetInterest`), which lowers `totalInterestDueNow()` and is socialized onto Junior/Senior tranche holders at the next `stopEpoch`.

### Finding Description
All external-vault interactions are unbounded:

- `_depositToVault` calls `vault.deposit(_assetAmount, address(this))` and accepts whatever shares come back, with no minimum-shares check [1](#0-0) .
- `onStartEpoch` snapshots `epochStartVaultAssets` as the **pre-deposit** total (`balanceOf + currentVaultAssets`) and then deposits the entire idle balance [2](#0-1) . If the deposit mints fewer shares than `convertToShares` implied because the share price was manipulated upward, the whole shortfall is invisible as donated tokens (the vault keeps the assets) and the facility's position is permanently under-valued.
- `onStopEpoch` calls `vault.withdraw(shortfall, ...)` with no cap on shares burned [3](#0-2) , and `_borrow` does the same [4](#0-3) . An inflated share price makes `withdraw` burn more shares than the fair amount.
- The accounting then converts the manipulation into a real pool loss: `_vaultNetInterest` computes `bufferedVaultDelta + (currentVaultAssets + epochWithdrawnFromVault) - (epochStartVaultAssets + epochDepositedToVault)` and reports the negative leg as `loss` [5](#0-4) , which `totalInterestDueNow()` subtracts from borrower interest [6](#0-5) .
- Repayments during an active epoch are redeposited through the same unchecked path (`_depositToVault(totalRepaidAssets, ...)`) [7](#0-6) .

There is no `minSharesOut`/`maxSharesBurned` parameter anywhere, no post-call share sanity check, and no deviation check against `previewDeposit`/`previewWithdraw`. The honest actors (manager calling `startEpoch`, borrower/executor calling `repay`) sequence the attacker's transactions; the attacker only needs to be a holder/depositor of the external ERC4626 vault — an unprivileged position.

### Impact Explanation
Concrete theft/loss: for a vault without virtual-share offset (or with a weak offset), an attacker who is the first/large depositor donates `X` underlying, pushing price-per-share so high that the facility's deposit of `D` mints `floor(D * totalShares / totalAssets)` ≈ far fewer (or zero) shares. The un-minted value stays in the vault and is captured by the attacker's shares on redemption. On the withdraw side, an inflated price makes `vault.withdraw(shortfall)` burn shares worth more than `shortfall`. In both cases the difference is booked as `vaultLoss`, reducing `totalInterestDueNow()` at `stopEpoch`; the pool fronts the contractual borrower interest out of its own assets while the yield leg is destroyed — a permanent loss borne by tranche holders (BB-first), not recoverable by `emergencyExitVault` since the shares were never minted.

### Likelihood Explanation
Requires (a) the owner-configured ERC4626 vault to permit share-price influence by an unprivileged depositor (donation/first-depositor surface — true for many ERC4626s, and the vault address is not constrained beyond matching `asset()`), and (b) the attacker to sequence around `startEpoch`, `stopEpoch`, `borrow`, or `repay`. Both conditions are mild; sequencing around public keeper/manager calls is standard MEV. This is the direct analog of the reported `minAmountsOut = 0` Balancer exit: an external liquidity interaction executed at an unbounded spot price inside a state-changing call.

### Recommendation
- Add slippage bounds to all vault interactions: compute `expectedShares = vault.previewDeposit(amount)` (or accept caller-supplied `minSharesOut`) and require `shares >= expectedShares * (1 - maxSlippage)` after `vault.deposit`; similarly require `shares <= previewWithdraw(shortfall) * (1 + maxSlippage)` for `vault.withdraw` in `onStopEpoch` and `_borrow`.
- Alternatively verify the position value delta: after depositing, require `vault.convertToAssets(newShares)` to be within a tolerance of `_assetAmount`.
- Prefer `vault.mint(expectedShares, ...)` where the share count is fixed by the caller.

### Proof of Concept
Foundry fork, active-epoch phase (programmable mode), unprivileged attacker:

```solidity
// Assume: IdleCDOEpochVariant + ProgrammableBorrower wired to an ERC4626 vault V
// with asset = USDC. Facility holds D = 1_000_000e6 idle USDC after deposits.

function test_DepositSandwich() public {
    // Attacker is an ordinary depositor of V (unprivileged).
    // 1) Attacker seeds V: deposit 1 wei -> 1 share (vault without strong offset),
    //    then donates D underlying directly to V, inflating price-per-share to ~D+1.
    usdc.approve(address(V), 1);
    V.deposit(1, attacker);
    usdc.transfer(address(V), D); // donation

    // 2) Honest manager calls startEpoch -> onStartEpoch -> _depositToVault(D, 0).
    vm.prank(manager);
    cdo.startEpoch(...);
    // Facility's D mints ~1 share: shares * convertToAssets(1) << D.
    uint256 facilityAssets = V.convertToAssets(V.balanceOf(address(pb)));
    assertLt(facilityAssets, D / 2);

    // 3) _vaultNetInterest reports the shortfall as vaultLoss; totalInterestDueNow drops.
    assertGt(pb.vaultLoss(), D / 2);

    // 4) Attacker redeems their shares and captures the facility's unminted value.
    vm.prank(attacker);
    V.redeem(V.balanceOf(attacker), attacker, attacker);
    assertGt(usdc.balanceOf(attacker), D); // donation + stolen share recovered
}
```

Withdraw-side variant: donate to `V` immediately before `stopEpoch`/`borrow`; `vault.withdraw(shortfall)` burns more facility shares than `shortfall` is worth, and the delta again lands in `vaultLoss` — same loss, no privileged role involved.

### Citations

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L213-219)
```text
    // exact pre-deposit amount instead of a post-deposit share-conversion round-down.
    uint256 startAssets = underlyingToken.balanceOf(address(this)) + currentVaultAssets;
    // Any idle balance left on the contract between epochs is parked immediately into the vault.
    _depositToVault(underlyingToken.balanceOf(address(this)), 0);
    // Reset the epoch accounting baseline from the exact pre-start total assets so the new epoch
    // does not treat the just-deposited idle balance as fresh vault profit or loss.
    epochStartVaultAssets = startAssets;
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L245-253)
```text
      if (shortfall > _currentVaultAssets()) return true;
      try vault.withdraw(shortfall, address(this), address(this)) returns (uint256 shares) {
        if (epochAccountingActive) {
          epochWithdrawnFromVault += shortfall;
        }
        emit WithdrawnFromVault(shortfall, shares, address(this));
      } catch {
        revert StopEpochVaultLiquidityUnavailable();
      }
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L330-334)
```text
  function totalInterestDueNow() external view returns (uint256) {
    (uint256 vaultInterest, uint256 loss) = _vaultNetInterest();
    uint256 totalGain = vaultInterest + borrowerInterestAccruedNow() + bufferInterest;
    return totalGain > loss ? totalGain - loss : 0;
  }
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L337-347)
```text
  function _vaultNetInterest() internal view returns (uint256 interest, uint256 loss) {
    if (!epochAccountingActive) return (0, 0);
    uint256 earnedAssets = _currentVaultAssets() + epochWithdrawnFromVault;
    uint256 principalAssets = epochStartVaultAssets + epochDepositedToVault;
    int256 netDelta = bufferedVaultDelta + int256(earnedAssets) - int256(principalAssets);
    if (netDelta > 0) {
      interest = uint256(netDelta);
    } else {
      loss = uint256(-netDelta);
    }
  }
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L378-385)
```text
  function _depositToVault(uint256 _assetAmount, uint256 _principalAssets) internal {
    if (_assetAmount == 0) return;
    uint256 shares = vault.deposit(_assetAmount, address(this));
    if (epochAccountingActive && _principalAssets != 0) {
      epochDepositedToVault += _principalAssets;
    }
    emit DepositedIntoVault(_assetAmount, shares);
  }
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L449-457)
```text
    uint256 onHand = underlyingToken.balanceOf(address(this));
    if (onHand < assets) {
      uint256 shortfall = assets - onHand;
      withdrawnShares = vault.withdraw(shortfall, address(this), address(this));
      if (epochAccountingActive) {
        epochWithdrawnFromVault += shortfall;
      }
      emit WithdrawnFromVault(shortfall, withdrawnShares, address(this));
    }
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L524-532)
```text
    if (epochAccountingActive) {
      // Current-epoch borrower interest must remain visible as profit at stopEpoch, while principal,
      // previously-fronted debt, and any excess repayment should only extend the epoch principal baseline.
      _depositToVault(totalRepaidAssets, totalRepaidAssets - currentEpochInterestPaid);
    } else if (currentEpochInterestPaid != 0) {
      // Buffer-period borrower interest was paid, so it is no longer debt, but it still belongs in
      // the next pool-facing stop result instead of the next epoch's principal baseline.
      bufferInterest += currentEpochInterestPaid;
    }
```
