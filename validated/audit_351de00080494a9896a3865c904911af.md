### Title
Fee-on-transfer underlying causes underfunded withdraw receipts and frozen claims - ([File: contracts/strategies/idle/IdleCreditVault.sol](contracts/strategies/idle/IdleCreditVault.sol))

### Summary
The external report describes crediting a strategy with a gross `amountAfterFee` even though a fee-on-transfer token delivers less. The same bug class exists in the credit-vault strategy `IdleCreditVault`: `deposit()` mints `_amount` strategy tokens regardless of tokens actually received, and `collectWithdrawFunds()` / `collectInstantWithdrawFunds()` settle receipt accounting for the gross `_amount` while the `safeTransferFrom` can deliver less. The intermediate CDO forward (`depositDuringEpoch` → `mintStrategyTokens(_amount)` + `_transferUnderlyings(_borrower(), _amount)`) likewise assumes 1:1 delivery.

### Finding Description
In `contracts/strategies/idle/IdleCreditVault.sol`:

```solidity
function deposit(uint256 _amount) external virtual override returns (uint256) {
    _onlyIdleCDO();
    if (_amount > 0) {
      underlyingToken.safeTransferFrom(msg.sender, address(this), _amount);
      _mint(msg.sender, _amount);        // mints gross, not received
    }
    ...
}
``` [1](#0-0) 

```solidity
function collectWithdrawFunds(uint256 _amount) external {
    _onlyIdleCDO();
    ...
    pendingWithdraws = pendingBasis - _amount;   // accounting reduced by gross
    ...
    underlyingToken.safeTransferFrom(idleCDO, address(this), _amount); // may deliver less
}
``` [2](#0-1) 

`collectInstantWithdrawFunds` has the same shape (`pendingInstantWithdraws -= _amount` then gross `safeTransferFrom`) [3](#0-2) , and `finalizeDefaultRecovery` adds the requested `_recoveredAmount` to `defaultRecoveryReserve` before pulling it, so a shortfall on delivery permanently overstates the reserve backing claims [4](#0-3) .

Concretely, during `stopEpoch`/`stopEpochWithDuration` the CDO pulls principal + interest from the borrower and then pushes the pending-receipt portion to the strategy via `collectWithdrawFunds(pendingToFund)`. With a fee-on-transfer underlying, each hop delivers `amount - fee`. Two failure modes:

1. If the CDO's balance is insufficient, the inner `safeTransferFrom(idleCDO, strategy, _amount)` reverts, reverting the entire `stopEpoch` — the epoch cannot be stopped and no withdrawal receipt can be funded until someone donates the shortfall to the CDO (donations are otherwise skimmed to `feeReceiver` via `_skimDonatedAssets` [5](#0-4) ).
2. If the pull succeeds only partially in value terms (or a gap is covered by mixing donations elsewhere), `pendingWithdraws`/`defaultRecoveryReserve` are decremented/credited by the gross `_amount`, leaving fewer tokens in the strategy than outstanding receipt basis. `_claimFundedWithdrawRequest` / `_transferFundedClaim` will then pay earlier claimants and revert for the last claimant(s), whose receipt burns would still have succeeded in the same ordering — a direct loss to the last claimer equal to the accumulated transfer fees.

The deposit-side analog in `IdleCDOEpochVariant.depositDuringEpoch` mints tranche shares priced on gross `_amount`, mints `_amount` strategy tokens, and then forwards `_amount` to the borrower — this path reverts rather than silently misaccounts, but confirms the codebase assumes feeless transfer semantics throughout [6](#0-5) . The non-reverting misaccounting lives in the claim-funding functions above.

### Impact Explanation
- Insolvency of receipt backing: strategy token / pending receipt obligations are recorded at gross while held underlyings are net. The shortfall is borne by the last claimants, whose `claimWithdrawRequest`/`claimInstantWithdrawRequest`/`_claimDefaulted*` calls revert or are paid less than their finalized basis.
- Temporary freezing: `stopEpoch` reverts when the CDO cannot cover the gross pull, blocking epoch settlement and all matured claims until the deficit is externally covered; after default finalization, `_transferDefaultRecovery` underflows `defaultRecoveryReserve` for the tail claimants, permanently freezing the residual claim (burn already applied in `_claimDefaultedWithdrawRequest` before transfer in the same call, but with reserve < basis the transfer reverts, leaving the claim unclaimable).

Quantified loss: cumulative fee charged on every hop of the funded amount (e.g., 1% FoT on a 1,000,000 USDC-funded pending bucket strands ~10,000 USDC of claims).

### Likelihood Explanation
The underlying token is fixed at `initialize` and these vaults target stablecoins, but nothing restricts `token` to feeless ERC20s and the codebase explicitly defends against raw donations (`_skimDonatedAssets`), implying non-standard token behavior is in scope. No existing guard measures balance deltas on the funding pulls, so a FoT pool token hits this path deterministically on the first `stopEpoch`/`collectWithdrawFunds`. Likelihood is medium (depends on token choice, an owner decision), impact is high for affected claimants.

### Recommendation
Measure actual received amounts in `IdleCreditVault`:

- In `deposit()`, `collectWithdrawFunds()`, `collectInstantWithdrawFunds()`, and `finalizeDefaultRecovery()`, snapshot `underlyingToken.balanceOf(address(this))` before and after `safeTransferFrom` and use the delta for `_mint`, `pendingWithdraws` reduction, and `defaultRecoveryReserve` accounting — or revert if the delta is less than the requested amount, so gross-backed accounting is never created.
- In `IdleCDOEpochVariant.depositDuringEpoch`, compute the minted share amount and `mintStrategyTokens` from the balance delta of the inbound transfer, and forward that same delta to the borrower.

### Proof of Concept
Foundry fork PoC outline against `IdleCreditVault`/`IdleCDOEpochVariant` (Assumes the vault's `token` is a fee-on-transfer ERC20, e.g. a mock that burns 1% on each `transfer`/`transferFrom`):

```solidity
function testFoTUnderfundsPendingWithdrawals() public {
    // deploy vault with FoT underlying (1% transfer fee), start epoch normally
    uint256 depositAmt = 1_000_000e6;
    idleCDO.depositAA(depositAmt);          // reverts in _deposit for FoT → deploy with fee=0 first,
                                          // then enable 1% fee before repay to isolate funding path
    managerStartEpoch();

    // user opens withdraw request
    userRequestWithdraw(500_000e6);

    // borrower repays interest+principal at stopEpoch; each transferFrom loses 1%
    vm.prank(borrower);
    underlying.approve(address(cdoEpoch), type(uint256).max);
    cdoEpochStopEpoch();                     // collectWithdrawFunds pulls pendingToFund

    // strategy holds pendingToFund * 0.99 but pendingWithdraws treated as fully funded
    uint256 stratBal = underlying.balanceOf(address(strategy));
    assertLt(stratBal, 500_000e6);

    // claimant A drains most; last claim reverts on safeTransfer
    userClaimWithdrawRequest();              // succeeds while balance suffices
    vm.expectRevert();
    otherClaimantClaim();                    // NotAllowed or ERC20 transfer revert
}
```

If the deficit is small enough that only `defaultRecoveryReserve` accounting is overstated (post-default path), the same setup with `finalizeDefaultRecovery(_recoveredAmount, recoverySource)` leaves `defaultRecoveryReserve` > actual balance, making the final `_transferDefaultRecovery` underflow/revert.

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L398-403)
```text
  function collectInstantWithdrawFunds(uint256 _amount) external {
    _onlyIdleCDO();
    if (_amount == 0) return;
    pendingInstantWithdraws -= _amount;
    underlyingToken.safeTransferFrom(idleCDO, address(this), _amount);
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L411-430)
```text
  function collectWithdrawFunds(uint256 _amount) external {
    _onlyIdleCDO();
    uint256 pendingBasis = pendingWithdraws;
    if (_amount < pendingBasis) {
      // Legacy receipts do not have per-epoch ownership data, so they can only be fully funded.
      if (!defaultRecoveryInitialized) revert NotAllowed();
      uint256 lossRecoveryPrice = _amount * RECOVERY_FULL / pendingBasis;
      // Avoid storing a zero price, which is indistinguishable from "no loss-adjusted epoch".
      if (lossRecoveryPrice == 0) revert NotAllowed();
      pendingWithdraws = 0;
      lossRecoveryPriceByEpoch[epochNumber] = lossRecoveryPrice;
    } else {
      // A plain implementation upgrade may leave legacy normal receipts pending. Their next
      // successful stop can fully fund the aggregate before lazy initialization occurs.
      pendingWithdraws = pendingBasis - _amount;
    }
    if (_amount != 0) {
      underlyingToken.safeTransferFrom(idleCDO, address(this), _amount);
    }
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L596-617)
```text
  function deposit(uint256 _amount)
    external
    virtual
    override
    returns (uint256) {
    _onlyIdleCDO();
    if (_amount > 0) {
      underlyingToken.safeTransferFrom(msg.sender, address(this), _amount);
      _mint(msg.sender, _amount);
    }

    if (IIdleCDOEpochVariant(idleCDO).isEpochRunning()) {
      // deposit done on stopEpoch (before setting the var to false) so we reset the counter
      totEpochDeposits = 0;
      epochNumber += 1;
    } else {
      // deposit done between epochs so we increase the counter
      totEpochDeposits += _amount;
    }

    return _amount;
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L686-709)
```text
    uint256 reserveAmount = _recoveredAmount + prefundedReserve + defaultRecoveryReserve;
    // Recovery can be above par if the recovered funds exceed the computed basis.
    uint256 recoveryPrice = reserveAmount * RECOVERY_FULL / totalBasis;

    defaultRecoveryFinalized = true;
    defaultRecoveryReserve = reserveAmount;
    defaultRecoveryPrice = recoveryPrice;
    defaultRecoveryEpoch = epochNumber;
    // A non-zero pending instant bucket means current-epoch instant receipts were not fully funded
    // and must be paid through the same recovery ratio as normal pending receipts.
    defaultInstantWithdrawsFinalized = pendingInstantWithdraws != 0;
    // Bring active CDO NAV to the same recovery ratio. IdleCDOEpochVariant then calls
    // _forceUpdateAccounting so tranche prices/virtualPrice expose the crystallized loss.
    uint256 activeFinalNAV = (activeBasis * recoveryPrice) / RECOVERY_FULL;
    defaultBBNav = defaultBBNav * recoveryPrice / RECOVERY_FULL;
    if (activeBalance > activeFinalNAV) {
      _burn(idleCDO, activeBalance - activeFinalNAV);
    } else if (activeFinalNAV > activeBalance) {
      _mint(idleCDO, activeFinalNAV - activeBalance);
    }
    if (_recoveredAmount != 0) {
      // Pull external recovery last: if the transfer fails, the whole finalization reverts.
      underlyingToken.safeTransferFrom(_recoverySource, address(this), _recoveredAmount);
    }
```

**File:** contracts/IdleCDOEpochVariant.sol (L684-732)
```text
    // Get underlyings from user
    _transferUnderlyingsFrom(msg.sender, address(this), _amount);

    // interest for the remaining epoch plus the full buffer period
    // NOTE: _calcInterest already gives full‑epoch + full‑buffer interest for the whole epoch
    //   So when a user joins mid‑epoch, we take the fraction of that full‑period interest 
    //   that matches the time they actually remain plus the entire buffer
    uint256 buffer = bufferPeriod;
    uint256 remaining = epochEndDate - block.timestamp;
    uint256 interest = _calcInterest(_amount) *
      // the time the depositor actually participates (remaining epoch + full buffer)
      (remaining + buffer) /
      // the total time baked into the scaled APR (epoch + buffer).
      (epochDuration + buffer);

    uint256 expectedInt = expectedEpochInterest;
    uint256 pendingFees = pendingWithdrawFees;
    uint256 trancheExpected;
    // existing holders' share of net expected interest for the epoch (pre-deposit)
    // (exclude pendingWithdrawFees since they go to fee receivers, not tranche holders)
    if (expectedInt > pendingFees) {
      trancheExpected = _calcTrancheInterestShare(
        _netGainAfterFees(expectedInt - pendingFees, _calculateManagementFee(lastNAVAA + lastNAVBB, remaining)),
        _tranche
      );
    }
    // interest this deposit will earn for the tranche over the remaining time (net of fees)
    uint256 trancheInterest = _calcTrancheInterestShare(
      _netGainAfterFees(interest, _calculateManagementFee(_amount, remaining)),
      _tranche
    );
    // pre-deposit expected final NAV for existing holders.
    // This won't ever be zero as we checked _trancheTotSupply and we seed initial NAV at tranche creation
    uint256 expectedFinal = _lastSavedNAV(_tranche) + trancheExpected;

    // mint at a discounted price so depositor gets principal + its prorated interest at epoch end
    // A mid‑epoch depositor should get _amount + trancheInterest at epoch end.
    // So they need minted = (amount + trancheInterest) / priceEnd.
    // priceEnd = expectedFinal / _trancheTotSupply
    // so minted = (amount + trancheInterest) * _trancheTotSupply / expectedFinal
    _minted = (_amount + trancheInterest) * _trancheTotSupply / expectedFinal;
    _mintShares(_tranche, msg.sender, _minted, _amount);

    // update expected epoch interest
    expectedEpochInterest += interest;
    // mint strategy tokens to this contract
    IdleCreditVault(strategy).mintStrategyTokens(_amount);
    // transfer underlyings to the borrower
    _transferUnderlyings(_borrower(), _amount);
```

**File:** contracts/IdleCDOEpochVariant.sol (L793-796)
```text
  /// @notice Transfer donated assets to the feeReceiver
  function _skimDonatedAssets() internal {
    _transferUnderlyings(feeReceiver, _contractTokenBalance(token));
  }
```
