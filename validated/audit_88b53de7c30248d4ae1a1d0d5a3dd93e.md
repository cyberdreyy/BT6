### Title
Instant-withdraw path in `requestWithdraw` skips management and performance fees charged to queued withdrawals - (File: contracts/IdleCDOEpochVariant.sol)

### Summary

`requestWithdraw` has two exit paths with inconsistent fee treatment. The normal queued path charges the full `_totalWithdrawFees` (upfront management fee for the receipt's off-NAV time plus performance fee on projected interest). The instant-withdraw path, taken whenever `lastEpochApr > unscaledApr + instantWithdrawAprDelta`, burns tranche tokens and mints a claim receipt for the full principal while charging zero fees. A lender can therefore exit the pool at the same life-cycle point (receipt leaves live NAV immediately, claim is gated on borrower funding) without paying the management fee that any queued requester must pay.

### Finding Description

In `IdleCDOEpochVariant.requestWithdraw`, after `_skimDonatedAssets()` and `_updateAccounting()`, the contract branches:

```solidity
if (_isInstantWithdrawEnabled()) {
  uint256 currentApr = creditVault.unscaledApr();
  if (lastEpochApr > (currentApr + instantWithdrawAprDelta)) {
    // burn strategy tokens from cdo and mint an equal amount to msg.sender as receipt
    creditVault.requestInstantWithdraw(_underlyings, msg.sender);
    // burn tranche tokens and decrease NAV
    _withdrawOps(_amount, _underlyings, _tranche);
    return _underlyings;
  }
}
``` [1](#0-0) 

The instant path returns before any fee logic. The queued path immediately below computes `totalFees = _totalWithdrawFees(principal, interest)`, nets them out of the receipt, and accrues them to `pendingWithdrawFees`. [2](#0-1) 

`_totalWithdrawFees` always includes an upfront management fee on principal for `_withdrawRequestManagementFeeDuration()` (one epoch plus remaining buffer), plus a performance fee on interest. [3](#0-2) [4](#0-3) 

The instant receipt is economically the same instrument as a queued receipt: `requestInstantWithdraw` burns the CDO's strategy tokens, mints an equal receipt to the user, and the claim is blocked until the borrower funds `pendingInstantWithdraws` during the next epoch (`instantWithdrawDelay` / `getInstantWithdrawFunds`). [5](#0-4) [6](#0-5) 

So the instant receipt also sits outside live NAV for a non-zero period, yet is charged no management fee at all — and no performance fee is ever levied on the interest already embedded in `_underlyings` via the current tranche price (`_trancheToUnderlyings` at `priceAA`/`priceBB` includes accrued yield). The APR-drop condition that gates the free path is set by honest manager `stopEpoch`/`setAprs` calls; an unprivileged lender only needs to time `requestWithdraw` after such a stop, exactly like the pre-v9.2 trade that escapes charges on the closing leg in the external report.

### Impact Explanation

Any KYC-passed lender holding tranche tokens can exit with zero management and performance fees whenever the manager lowers the vault APR by more than `instantWithdrawAprDelta`. The loss is the uncharged fee: `managementFee * principal * _withdrawRequestManagementFeeDuration() / YEAR` plus `fee%` of the accrued interest embedded in the tranche price. These amounts should have accrued to `pendingWithdrawFees`/feeReceiver but instead leak to the withdrawer. Because other withdrawing users are charged in the same epoch, this is a direct, repeatable fee leak rather than a design-level fee waiver (the code explicitly models receipt off-NAV time as fee-bearing for the queued path).

### Likelihood Explanation

High. The trigger is an APR decrease across epochs, a routine honest manager operation (the protocol itself treats APR drops as expected — that is why the instant path exists). The attacker only calls `requestWithdraw` then `claimInstantWithdrawRequest`; no privileged action is required. Guards do not stop it: `_skimDonatedAssets`, `_updateAccounting`, `isWalletAllowed`, and the `allowInstantWithdraw` flag all permit the flow, and the fee-skip branch executes before `_totalWithdrawFees` is ever reached.

### Recommendation

Apply the same fee model to the instant path: compute `totalFees` on the principal (management fee for the expected off-NAV period, e.g. `instantWithdrawDelay`/buffer duration, plus performance fee on the yield already embedded in the tranche price), net it out of the amount passed to `requestInstantWithdraw`, and add it to `pendingWithdrawFees` — mirroring lines 773-778 before the early return.

### Proof of Concept

Foundry fork test (extend `test/foundry/IdleCreditVault.t.sol` helpers):

```solidity
function testInstantWithdrawSkipsAllFees() external {
    uint256 mgmtFeeRate = 1_000; // 1%
    uint256 perfFeeRate = 10_000; // 10%
    _setFeeParams(TL_MULTISIG, perfFeeRate, FULL_ALLOC, mgmtFeeRate);
    _setManagementFee(mgmtFeeRate);

    uint256 amount = 100_000 * ONE_SCALE;
    uint256 mintedAA = idleCDO.depositAA(amount);

    // epoch 0 runs at initialProvidedApr, then manager honestly lowers APR
    _startEpochAndCheckPrices(0);
    _stopEpochAndCheckPrices(0, initialProvidedApr / 4, _expectedFundsEndEpoch());

    // attacker requests withdraw: APR dropped > instantWithdrawAprDelta => instant path
    uint256 requested = cdoEpoch.requestWithdraw(mintedAA, address(AAtranche));

    // instant path booked NO fees
    assertEq(cdoEpoch.pendingWithdrawFees(), 0, 'instant path charged zero fees');

    // whereas the queued path would have charged _totalWithdrawFees(principal, interest)
    // attacker just funds and claims at next epoch start
    _startEpochAndCheckPrices(1);
    vm.warp(block.timestamp + cdoEpoch.instantWithdrawDelay() + 1);
    _getInstantFunds();

    uint256 balPre = underlying.balanceOf(address(this));
    cdoEpoch.claimInstantWithdrawRequest();
    // attacker receives full principal+accrued yield, no mgmt/perf fee deducted
    assertEq(underlying.balanceOf(address(this)) - balPre, requested);
}
```

Expected result: `pendingWithdrawFees == 0` and the claim pays the gross tranche value, while an equivalent queued `requestWithdraw` in the same conditions would have deducted `_totalWithdrawFees`, demonstrating the inconsistent charge.

### Citations

**File:** contracts/IdleCDOEpochVariant.sol (L761-770)
```text
    if (_isInstantWithdrawEnabled()) {
      uint256 currentApr = creditVault.unscaledApr();
      if (lastEpochApr > (currentApr + instantWithdrawAprDelta)) {
        // burn strategy tokens from cdo and mint an equal amount to msg.sender as receipt
        creditVault.requestInstantWithdraw(_underlyings, msg.sender);
        // burn tranche tokens and decrease NAV
        _withdrawOps(_amount, _underlyings, _tranche);
        return _underlyings;
      }
    }
```

**File:** contracts/IdleCDOEpochVariant.sol (L772-778)
```text
    uint256 principal = _underlyings;
    (uint256 interest, int256 diff) = _calcInterestWithdrawRequest(_underlyings, _tranche);
    uint256 totalFees = _totalWithdrawFees(principal, interest);
    // user is requesting principal + interest minus upfront management fee and net performance fee
    _underlyings = principal + interest - totalFees;
    // add expected fees to pending withdraw fees counter
    pendingWithdrawFees += totalFees;
```

**File:** contracts/IdleCDOEpochVariant.sol (L900-906)
```text
  function _totalWithdrawFees(uint256 _principal, uint256 _interest) private view returns (uint256) {
    uint256 _mgmtFee = _calculateManagementFee(_principal, _withdrawRequestManagementFeeDuration());
    // When interest covers management fees, interest - netGain equals management fee plus performance fee.
    return _mgmtFee >= _interest ?
      _mgmtFee :
      _interest - _netGainAfterFees(_interest, _mgmtFee);
  }
```

**File:** contracts/IdleCDOEpochVariant.sol (L925-931)
```text
  function _withdrawRequestManagementFeeDuration() private view returns (uint256 _duration) {
    uint256 bufferEnd = epochEndDate + bufferPeriod;
    _duration = epochDuration;
    if (block.timestamp < bufferEnd) {
      _duration += bufferEnd - block.timestamp;
    }
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L356-375)
```text
  function requestInstantWithdraw(uint256 _amount, address _user) external {
    _onlyIdleCDO();
    _ensureDefaultRecoveryInitialized();
    // burn strategy tokens from cdo
    _burn(msg.sender, _amount);
  
    // mint equal amount of strategy tokens to the user as receipt, useful in case of default
    _mint(_user, _amount);

    // increase the instant withdraw requests for the user
    instantWithdrawsRequests[_user] += _amount;
    uint256 currentEpoch = epochNumber;
    // we record both per-user (old, kept for compatibility) and per-epoch so on
    // finalization we can distinguish "default-epoch pending instant receipts"
    // from old funded instant receipts.
    instantWithdrawsRequestsByEpoch[_user][currentEpoch] += _amount;
    instantWithdrawClaimsByEpoch[currentEpoch] += _amount;
    // increase the total instant withdraw requests
    pendingInstantWithdraws += _amount;
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L380-393)
```text
  function claimInstantWithdrawRequest(address _user) external {
    _onlyIdleCDO();
    if (defaultRecoveryFinalized && defaultInstantWithdrawsFinalized) {
      // Clear the defaulted-epoch instant receipt first, then continue so the same call can
      // also pay any older instant receipt that was already funded before default finalization.
      _claimDefaultedInstantWithdrawRequest(_user);
    }
    uint256 amount = instantWithdrawsRequests[_user];
    // burn strategy tokens from user
    _burn(_user, amount);

    instantWithdrawsRequests[_user] = 0;
    _transferFundedClaim(_user, amount);
  }
```
