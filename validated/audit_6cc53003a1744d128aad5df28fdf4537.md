### Title
Unfunded instant-withdraw receipts drain pooled deposits held in `IdleCreditVault` - ([File: contracts/strategies/idle/IdleCreditVault.sol])

### Summary
`IdleCreditVault.claimInstantWithdrawRequest` burns a user's instant-withdraw receipt and pays out `instantWithdrawsRequests[_user]` at par from the strategy's underlying balance, without checking that the request was actually funded. The only guard is `_transferFundedClaim`'s `defaultRecoveryReserve` isolation. But the strategy routinely holds underlying that does not belong to the instant-withdraw queue: `deposit()` pulls underlyings from the CDO into the strategy during the buffer phase (`totEpochDeposits` accounting at lines 607-614), where they sit until `startEpoch` forwards them to the borrower. An instant-withdraw requester can therefore claim before the next `startEpoch` funds the queue and steal other lenders' freshly deposited principal — a "type confusion / state handling" analog of CVE-2021-1789: an unfunded receipt is treated as a funded claim because the two states share one aggregate counter and one balance pool.

### Finding Description
- `requestInstantWithdraw` (lines 356-375) burns CDO strategy tokens, mints a receipt to the user, and bumps `instantWithdrawsRequests`/`pendingInstantWithdraws`. No underlying moves at request time; funding arrives later via `collectInstantWithdrawFunds` (lines 398-403), called by the CDO during `startEpoch`/`getInstantWithdrawFunds`.
- `claimInstantWithdrawRequest` (lines 380-393) contains no check tying the claim to collected funds. It burns `instantWithdrawsRequests[_user]` and calls `_transferFundedClaim`, which (lines 897-907) verifies only `balance - defaultRecoveryReserve >= amount`.
- The CDO entry point `IdleCDOEpochVariant.claimInstantWithdrawRequest` (lines 975-979) only checks the `allowInstantWithdraw` flag; there is no epoch-running gate on the claim side.
- Meanwhile `deposit()` (lines 596-617) transfers underlyings into the strategy and records them in `totEpochDeposits` when the epoch is not running. Those pooled deposits are indistinguishable, at balance level, from collected instant-withdraw funding.
- Concretely: in the buffer phase after a `stopEpoch` that lowered APR, an attacker calls `requestWithdraw` on the CDO, which routes to `requestInstantWithdraw` when `lastEpochApr > currentApr + instantWithdrawAprDelta` (EpochVariant lines 761-769). The attacker then calls `claimInstantWithdrawRequest` in the same or a later buffer-phase block and receives pooled deposits 1:1 — funds that belong to depositors whose principal is waiting to be lent to the borrower.

### Impact Explanation
Direct theft with quantified loss equal to the attacker's receipt amount, capped by the strategy's unfunded underlying balance (pooled buffer deposits plus any borrower-send funds parked in the strategy). Each stolen token creates an equivalent shortfall: when `startEpoch` forwards deposits to the borrower, either the transaction reverts on insufficient balance (temporary freezing of epoch start / permanent freezing until recapitalized) or the borrower is underfunded while instant claimants were overpaid — i.e., solvency and "one receipt one funded payout" invariants are broken. No privileged misbehavior is required: the attacker is a KYC-passed tranche holder and honest manager calls (APR reduction at stopEpoch) create the precondition.

### Likelihood Explanation
Requires (a) instant-withdraw mode enabled (`allowInstantWithdraw`, not disabled by `disableInstantWithdraw`/`isProgrammableBorrower`), (b) an APR decrease exceeding `instantWithdrawAprDelta` — a normal, honest manager action — and (c) attacker-held tranche tokens plus KYC (`isWalletAllowed` on `requestWithdraw`). The claim is atomic and front-runnable within a single buffer window; cost is only the tranche position itself. Caveat: I could not fully trace `startEpoch`/`getInstantWithdrawFunds` ordering to confirm no additional guard normalizes `pendingInstantWithdraws` against collected funds before deposits are forwarded, but no such balance check exists in the strategy's claim path itself.

### Recommendation
Track funded instant-withdraw liquidity explicitly: e.g., a `fundedInstantWithdraws`/`instantWithdrawPool` counter incremented in `collectInstantWithdrawFunds` and decremented on claims, reverting when a claim exceeds funded balance. Alternatively, gate `claimInstantWithdrawRequest` on epoch-running state and reconcile `pendingInstantWithdraws` against the strategy's claimable balance, so pooled deposits (`totEpochDeposits` in flight) are never spendable by receipt holders.

### Proof of Concept
```solidity
// Foundry fork PoC sketch
// Setup: buffer phase after stopEpoch lowered APR by > instantWithdrawAprDelta
// 1. Honest depositor: idleCDO.depositAA(X) -> vault.deposit pulls X underlying into strategy
//    assert underlying.balanceOf(strategy) == X; totEpochDeposits == X
// 2. Attacker (KYC'd tranche holder): idleCDO.requestWithdraw(attackerTrancheBal, AATranche)
//    -> routes to creditVault.requestInstantWithdraw (APR dropped)
//    assert creditVault.instantWithdrawsRequests(attacker) == receiptAmt
//    assert underlying.balanceOf(strategy) unchanged == X   // request was NOT funded
// 3. Attacker: idleCDO.claimInstantWithdrawRequest()
//    -> _transferFundedClaim pays from pooled deposits
//    assert underlying.balanceOf(attacker) == receiptAmt
//    assert underlying.balanceOf(strategy) == X - receiptAmt  // depositors' principal stolen
// 4. manager.startEpoch() -> sendInterestAndDeposits / borrower funding
//    -> underfunded by receiptAmt (insolvency) or reverts (frozen epoch)
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L356-393)
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

  /// @notice claim the instant withdraw request
  /// @dev we transfer the underlying tokens
  /// @param _user address of the user
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L897-917)
```text
  function _transferFundedClaim(address _user, uint256 _amount) internal {
    if (_amount == 0) return;
    uint256 reserve = defaultRecoveryReserve;
    if (reserve != 0) {
      uint256 balance = underlyingToken.balanceOf(address(this));
      // This should be unreachable when accounting is consistent. Keep the guard so old funded
      // receipts can never spend underlyings reserved for default recovery claimants.
      if (balance < reserve || balance - reserve < _amount) revert NotAllowed();
    }
    underlyingToken.safeTransfer(_user, _amount);
  }

  /// @notice Transfer default recovery reserve to a user.
  /// @param _user claim receiver
  /// @param _amount amount to transfer
  function _transferDefaultRecovery(address _user, uint256 _amount) internal {
    if (_amount == 0) return;
    // Every defaulted or post-default claim consumes the isolated recovery reserve.
    defaultRecoveryReserve -= _amount;
    underlyingToken.safeTransfer(_user, _amount);
  }
```

**File:** contracts/IdleCDOEpochVariant.sol (L739-791)
```text
  function requestWithdraw(uint256 _amount, address _tranche) external returns (uint256 _underlyings) {
    _checkTranche(_tranche);
    _checkNotAllowed(
      (_tranche == AATranche ? !allowAAWithdrawRequest : !allowBBWithdrawRequest) ||
      !isWalletAllowed(msg.sender)
    );
  
    // we check if there are donated assets to the pool and transfer them to the feeReceiver if any
    _skimDonatedAssets();

    // we trigger an update accounting to check for eventual losses
    _updateAccounting();

    IdleCreditVault creditVault = IdleCreditVault(strategy);
    if (_amount == 0) {
      _amount = _userTrancheBal(msg.sender, _tranche);
    }
    _underlyings = _trancheToUnderlyings(_amount, _tranche);

    // Programmable borrower deployments do not support instant withdrawals.
    // If apr decresed wrt last epoch, request instant withdraw and burn tranche tokens directly
    // we compare unscaled aprs
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

    uint256 principal = _underlyings;
    (uint256 interest, int256 diff) = _calcInterestWithdrawRequest(_underlyings, _tranche);
    uint256 totalFees = _totalWithdrawFees(principal, interest);
    // user is requesting principal + interest minus upfront management fee and net performance fee
    _underlyings = principal + interest - totalFees;
    // add expected fees to pending withdraw fees counter
    pendingWithdrawFees += totalFees;

    /// if there is an AA withdrawal the overperformance that the amount withdrawed would have generated for BB tranches
    /// is saved in interestForOverUnderPerformance. This is used to calculate the interest that should be added to the
    /// expectedEpochInterest at the startEpoch.
    /// If there is a BB withdrawal this amount is subtracted from the expectedEpochInterest
    interestForOverUnderPerformance += diff;

    // The receipt is fixed now and leaves live NAV. Charge management fees upfront
    // for the time it waits outside live NAV: remaining buffer plus the next epoch.
    creditVault.requestWithdraw(_underlyings, msg.sender, principal);
    // burn tranche tokens and decrease NAV without interest for the next epoch as it was not yet counted in NAV
    _withdrawOps(_amount, principal, _tranche);
  }
```

**File:** contracts/IdleCDOEpochVariant.sol (L973-980)
```text
  /// @notice Claim an instant withdraw request from the vault. Can be done when epoch is running
  /// as funds will get transferred from borrower when epoch starts
  function claimInstantWithdrawRequest() external {
    // Check that instant withdraws are available
    _checkNotAllowed(!allowInstantWithdraw);
    IdleCreditVault(strategy).claimInstantWithdrawRequest(msg.sender);
  }

```
