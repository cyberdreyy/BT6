### Title
Blacklisted `feeReceiver`/`owner` on the underlying token forces `stopEpoch` into a false borrower default and freezes the vault - (File: contracts/IdleCDOEpochVariant.sol)

### Summary
The Arrakis report describes a forced fee payout on a privileged-role change that permanently reverts when the payout recipient is blacklisted by the underlying token (e.g., USDC). The closest analog in idle-tranches is `IdleCDOEpochVariant._stopEpoch`: fee payouts to `feeReceiver` and `owner()` are executed *inside* the `try` block of `this.getFundsFromBorrower(...)`, so a revert caused by a blacklisted fee recipient is misinterpreted as the borrower failing to repay, triggering `_handleBorrowerDefault`.

### Finding Description
In `IdleCDOEpochVariant._stopEpoch`, the whole post-repayment body — including the two `token.safeTransfer` calls in `_transferFeeUnderlyings` — sits inside the `try` on the self-call `getFundsFromBorrower`: [1](#0-0) [2](#0-1) [3](#0-2) 

`_transferFeeUnderlyings` pushes fees to `feeReceiver` and `owner()`: [4](#0-3) 

If either recipient is blacklisted by `token` (e.g., USDC/USDT blacklist), `safeTransfer` reverts, the `catch` runs, and `_handleBorrowerDefault` sets `defaulted = true`, pauses the contract, ends the epoch, and disables AA/BB withdraw requests — even though the borrower fully repaid and funds are sitting in the contract: [5](#0-4) 

The same blacklist also breaks `_skimDonatedAssets`, which pushes the entire skimmed balance to `feeReceiver`: [6](#0-5) 

Attack surface vs. the external report: idle-tranches caches fees in `unclaimedFees` (the report's suggested fix) for accounting, but the *payout* is still a push to a fixed recipient at the worst possible place — inside the borrower-repayment `try/catch`, where a recipient-side revert is indistinguishable from borrower insolvency.

### Impact Explanation
- A solvent borrower's repayment is recorded as a default: `defaulted = true`, epoch stopped, deposits paused, withdraw requests blocked. All pending withdraw-request holders and tranche holders are frozen until the default flow is unwound via `finalizeDefault`/recovery, which is a heavier, loss-oriented path not designed for a bookkeeping revert.
- Every retry of `stopEpoch` reverts the same way while the recipient stays blacklisted, so the vault is stuck in the defaulted state. Recovery depends on honest privileged action (rotating `feeReceiver` via `setFeeParams` or transferring ownership), but `defaulted` cannot be un-set — once tripped, the vault is on the default/finalization track regardless.
- The same recipient blacklist bricks any non-minted-interest fee payout and `_skimDonatedAssets`, so donated-asset skims also revert.

### Likelihood Explanation
- The trigger is external (token-issuer blacklist of `feeReceiver` or the owner multisig), identical in nature to the original report — no privileged malice needed. Credit vaults dealing in USDC/USDT underlyings are exposed to this environmental event.
- Escapability is partial: `setFeeParams`/ownership transfer can swap the recipient (no token transfer occurs on the setter itself), so it is not a *permanent* DOS of the setter like in Arrakis. However, the spurious `defaulted` flag and forced default-flow accounting are the real damage — the bug is not the stuck setter but the misplaced transfer inside the repayment `try`.
- Note: I could not verify the exact `setFeeParams` body (whether it performs any transfer) within the iteration limit; the finding stands even if rotation is free, because the false-default latch is the broken invariant.

### Recommendation
- Move `_transferFeeUnderlyings` calls (and `_skimDonatedAssets`) outside the `try/catch` on `getFundsFromBorrower`, so only an actual borrower repayment failure routes to `_handleBorrowerDefault`.
- Better, per the upstream recommendation: keep fees pull-based — credit `feeReceiver`/`owner()` balances (e.g., extend `unclaimedFees` into a per-recipient mapping or keep minting AA tranche shares as in the `_mintInterest` path, which cannot be blacklisted at the ERC20 level since it is the vault's own tranche token) and let recipients claim.
- Add a guard so that once repayment succeeds (`getFundsFromBorrower` returned), a later fee-transfer failure cannot flip `defaulted`.

### Proof of Concept
Foundry fork PoC sketch (USDC as `token`, mainnet fork):

```solidity
// setup: depositAA/depositBB, setFeeParams(feeReceiver, fee, feeSplit, mgmtFee),
// startEpoch, warp to epochEndDate, borrower repays getFundsFromBorrower amount.

// blacklist feeReceiver on USDC (prank USDC masterMover/blacklister):
usdcBlacklist(feeReceiver, true);

deal(usdc, borrower, expectedFundsEndEpoch);
vm.prank(borrower); usdc.approve(address(cdo), type(uint256).max);
vm.prank(manager);
cdoEpoch.stopEpoch(newApr, 0);

// repayment succeeded (CDO holds the tokens) but:
assertTrue(cdoEpoch.defaulted());          // false default
assertTrue(cdoEpoch.paused());
assertFalse(cdoEpoch.allowAAWithdrawRequest());

// retrying also reverts into default path; vault is latched in default flow.
```

Broken invariant: a recipient-side ERC20 blacklist on a *fee payout* is converted by the shared `try/catch` into a borrower-insolvency event, violating solvency/fair-accounting (honest borrower marked defaulted; users pushed onto the loss-recovery path with funds fully present).

### Citations

**File:** contracts/IdleCDOEpochVariant.sol (L408-420)
```text
    try this.getFundsFromBorrower(_amountToPullFromBorrower + _pendingWithdraws) {
      // transfer in strategy and decrease pendingWithdraws
        _strategy.collectWithdrawFunds(_pendingWithdraws);
      // Only settle borrower interest when CDO is fronting it (minted mode, not closing pool).
      // When requesting all funds (_interest == 1) the CDO pulls cash directly, no fronting.
      if (_mintInterest && isProgrammableBorrower) {
        IProgrammableBorrower(_borrower()).settleBorrowerInterest();
      }
      // Split pending withdraw fees before update accounting
      // NOTE: Fees are sent with 2 different transfer calls, here and after updateAccounting, to avoid complicated calculations
      if (!_mintInterest) {
        _transferFeeUnderlyings(_pendingWithdrawFees);
      }
```

**File:** contracts/IdleCDOEpochVariant.sol (L450-459)
```text
      } else {
        // Cash-funded fees can only use gross interest not already owed to pending withdrawals.
        uint256 _availableForFees = _grossInterest > _pendingWithdrawFees ? _grossInterest - _pendingWithdrawFees : 0;
        if (_fees > _availableForFees) {
          _fees = _availableForFees;
        }
        _transferFeeUnderlyings(_fees);
      }
      // Any fee that cannot be paid in cash remains accrued and continues reducing NAV.
      unclaimedFees -= _fees;
```

**File:** contracts/IdleCDOEpochVariant.sol (L501-505)
```text
    } catch {
      // if borrower defaults, prev instant withdraw requests can still be withdrawn
      // as were already fullfilled prior to the default (all funds already sent to the strategy)
      _handleBorrowerDefault(_amountToPullFromBorrower + _pendingWithdraws);
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

**File:** contracts/IdleCDOEpochVariant.sol (L793-796)
```text
  /// @notice Transfer donated assets to the feeReceiver
  function _skimDonatedAssets() internal {
    _transferUnderlyings(feeReceiver, _contractTokenBalance(token));
  }
```

**File:** contracts/IdleCDOCreditVault.sol (L587-591)
```text
  function _transferFeeUnderlyings(uint256 _amount) internal {
    uint256 feeReceiverAmount = _feeReceiverAmount(_amount);
    _transferUnderlyings(feeReceiver, feeReceiverAmount);
    _transferUnderlyings(owner(), _amount - feeReceiverAmount);
  }
```
