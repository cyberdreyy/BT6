### Title
`ProgrammableBorrower` vault deposit reverts when the ERC4626 vault is paused, blocking `repay` and `startEpoch` and forcing a false borrower default - (File: contracts/strategies/idle/ProgrammableBorrower.sol)

### Summary

`ProgrammableBorrower` unconditionally calls `vault.deposit()` inside `_depositToVault` from `onStartEpoch` and from `_repay` while `epochAccountingActive` is true [1](#0-0) . If the underlying ERC4626 vault pauses deposits (the exact condition in the external Aave report), `startEpoch` reverts atomically and, critically, an honest borrower's `repay` reverts too — the borrower cannot reduce `borrowerPrincipal`/`borrowerInterestDebt` even while holding cash. If the owner then calls `stopEpoch(1)` (close pool) or `stopEpoch` with the borrower still owing, `onStopEpoch` returns `false` when `_isRequestingAllFunds` and any borrower debt is outstanding [2](#0-1) , so `IdleCDOEpochVariant._stopEpoch` invokes `_handleBorrowerDefault` [3](#0-2) , marking a solvent, willing borrower as defaulted.

### Finding Description

The code assumes the ERC4626 vault always accepts deposits. There is no try/catch or pause check around `vault.deposit` in `_depositToVault` [4](#0-3) . Two flows break:

- `onStartEpoch` deposits all idle underlying into the vault [5](#0-4) ; a paused vault makes `IdleCDOEpochVariant.startEpoch` revert [6](#0-5) , so the pool is stuck in the buffer phase and pending withdraw receipts can never be claimed (claims require a settled epoch).
- `_repay` redeploys repaid cash into the vault whenever `epochAccountingActive` [7](#0-6) . Since `onStopEpoch` returns `false` before clearing `epochAccountingActive` when debt remains, there is no path for the borrower to repay while the vault rejects deposits. A close-pool `stopEpoch` therefore triggers `_handleBorrowerDefault` [8](#0-7) , setting `defaulted = true`, pausing the CDO, and routing all LPs into the default/finalizeDefaultRecovery flow even though the borrower's repayment was sitting ready.

### Impact Explanation

Temporary freezing of all deposits/withdrawal claims for the duration of the vault pause, plus a forced hard default of a solvent borrower: once `defaulted` is set, tranche holders can only recover via `finalizeDefault`, which applies an aggregate recovery multiplier and zeroes prices if recovery is sub-precision — a permanent loss path triggered purely by an external pause, not by borrower insolvency.

### Likelihood Explanation

ERC4626 vaults (the documented use: "keep undrawn capital parked inside an ERC4626 vault") commonly expose pausable deposits. No attacker action is needed beyond the external condition; privileged actors (owner/manager/borrower) all behave honestly and still cannot avoid the default.

### Recommendation

In `_repay`, keep repaid funds on-hand instead of redepositing when `vault.deposit` would revert (e.g., try/catch or check a `maxDeposit`/`paused` view), and in `onStopEpoch`, set `epochAccountingActive = false` and return `true` when the shortfall is covered on-hand, so a paused vault never converts a willing repayment into a default. Optionally skip the `_depositToVault` call in `onStartEpoch` when the vault reports zero deposit capacity and let the CDO proceed.

### Proof of Concept

Foundry fork PoC sketch:

```solidity
// fork mainnet, deploy IdleCDOEpochVariant + IdleCreditVault + ProgrammableBorrower
// with a pausable ERC4626 vault (e.g. a real paused vault or mock with paused deposits)

function testVaultPauseForcesDefault() public {
    // 1. lenders deposit during buffer, borrower draws during epoch 1
    // 2. external vault pauses deposits
    vault.pauseDeposits();
    // 3. borrower holds full principal+interest and approves repay
    vm.prank(borrower);
    vm.expectRevert(); // vault.deposit reverts inside _depositToVault
    programmableBorrower.repay(0);
    // 4. epoch ends; manager closes pool
    vm.warp(epochEndDate + 1);
    vm.prank(manager);
    cdo.stopEpoch(0, 1);
    // 5. borrower is marked defaulted despite being solvent and willing
    assertTrue(cdo.defaulted());
}
```

### Citations

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L214-216)
```text
    uint256 startAssets = underlyingToken.balanceOf(address(this)) + currentVaultAssets;
    // Any idle balance left on the contract between epochs is parked immediately into the vault.
    _depositToVault(underlyingToken.balanceOf(address(this)), 0);
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L235-237)
```text
    if (_isRequestingAllFunds && (borrowerPrincipal != 0 || borrowerInterestDebt != 0 || borrowerInterestAccrued != 0)) {
      return false;
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

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L524-527)
```text
    if (epochAccountingActive) {
      // Current-epoch borrower interest must remain visible as profit at stopEpoch, while principal,
      // previously-fronted debt, and any excess repayment should only extend the epoch principal baseline.
      _depositToVault(totalRepaidAssets, totalRepaidAssets - currentEpochInterestPaid);
```

**File:** contracts/IdleCDOEpochVariant.sol (L398-403)
```text
      if (!IProgrammableBorrower(_borrower()).onStopEpoch(_amountToPullFromBorrower + _pendingWithdraws, _isRequestingAllFunds)) {
        // Emit the exact cash liability requested from the borrower, including recalled principal
        // in close-pool mode and excluding interest fronted through minted accounting.
        _handleBorrowerDefault(_amountToPullFromBorrower + _pendingWithdraws);
        return;
      }
```

**File:** contracts/IdleCDOEpochVariant.sol (L577-599)
```text
  function _handleBorrowerDefault(uint256 funds) internal {
    defaulted = true;
    // Do not reopen instant claims here. They remain disabled when funding is pending;
    // successful full funding is the only path that enables them before finalization.

    if (isProgrammableBorrower) {
      IProgrammableBorrower(_borrower()).onDefault();
    }

    // deposits should be already prevented
    if (!paused()) {
      _pause();
    }

    // stop the current epoch
    isEpochRunning = false;

    // prevent withdrawals requests
    allowAAWithdrawRequest = false;
    allowBBWithdrawRequest = false;

    emit BorrowerDefault(funds);
  }
```

**File:** contracts/IdleCDOEpochVariant.sol (L991-995)
```text
  function _startEpochProgrammableBorrower(uint256 _pendingWithdraws) internal {
    if (isProgrammableBorrower) {
      IProgrammableBorrower(_borrower()).onStartEpoch(_pendingWithdraws);
    }
  }
```
