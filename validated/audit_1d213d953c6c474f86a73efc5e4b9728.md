### Title
Underlying tokens of a withdraw claim are permanently frozen if the claimant's address is blacklisted by the token - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
Withdraw receipts in `IdleCreditVault` are strictly address-bound: the request mints receipt strategy tokens to `msg.sender`, the claim sends underlying only to that same address, and receipt transfers are disabled for everyone except the IdleCDO. There is no receiver parameter and no way to change the payout address. If the underlying token (e.g. USDC, which supports blacklisting) blacklists the user's address after the request is created, every claim path reverts on `safeTransfer` and the funded underlying is locked in the vault forever.

### Finding Description
- `requestWithdraw` burns the CDO's strategy tokens and mints a receipt to `_user = msg.sender`; ownership is recorded in `withdrawsRequests[_user]` / `withdrawsRequestsByEpoch[_user]` keyed by that address. [1](#0-0) 
- `IdleCDOEpochVariant.claimWithdrawRequest` hard-codes `msg.sender` as the beneficiary and forwards it to `IdleCreditVault.claimWithdrawRequest(msg.sender)`; there is no `receiver` argument anywhere in the claim flow. [2](#0-1) 
- Every payout path (`_claimFundedWithdrawRequest`, `_claimDefaultedWithdrawRequest`, `_claimPostDefaultWithdrawRequest`, `claimInstantWithdrawRequest`) ends in `_transferFundedClaim` / `_transferDefaultRecovery`, which do `underlyingToken.safeTransfer(_user, amount)` — the transfer reverts if `_user` is blacklisted by the underlying token. [3](#0-2) [4](#0-3) 
- The escape hatch that would normally mitigate this — transferring the receipt to a clean address — is deliberately disabled: `_transfer` reverts unless `msg.sender == idleCDO`, and `setCanTransfer(true)` is impossible. [5](#0-4) 
- The same applies to `claimInstantWithdrawRequest` and to default/post-default recovery claims, which also pay only `_user`.

This mirrors the external bug class: once the claim "recipient" is fixed (here, implicitly at request time, since it can never be changed), a token-level blacklist freezes funds in the protocol permanently.

### Impact Explanation
Permanent freezing of funds. A user whose address lands on the underlying token's blacklist (e.g. USDC/ USDT blacklist applied between `requestWithdraw` and claim maturity) can never execute `claimWithdrawRequest` or `claimInstantWithdrawRequest`: the funded underlying stays in the vault while the receipt can neither be claimed, redirected, nor sold. Loss = 100% of the user's claim amount.

### Likelihood Explanation
Requires an external condition (token-issuer blacklisting), so it is not triggerable by the user at will, but: (a) underlyings for these credit vaults are blacklistable stablecoins, (b) the receipt is non-transferable so there is zero workaround, and (c) the frozen funds also cannot be rescued — `transferToken` is owner-only and would break claim accounting anyway. Consistent with a Medium severity.

### Recommendation
Allow the claim functions to accept a `receiver` parameter (e.g. `claimWithdrawRequest(address _user, address _receiver)` gated so only the receipt owner can set the destination), or allow receipt holders to re-assign their claim to a new address once, similar to allowing the recipient to update `recipientAddress` in the referenced report.

### Proof of Concept
Foundry fork test (mainnet fork, USDC underlying):

```solidity
function testBlacklistedClaimantFundsFrozen() external {
    // 1. user deposits and requests withdraw during buffer
    _depositWithUser(user, amount, true);
    vm.prank(user);
    cdoEpoch.requestWithdraw(trancheBal, address(AAtranche));

    // 2. epoch runs and stops; funds collected by IdleCreditVault
    _startEpochAndCheckPrices(0);
    _stopEpochAndCheckPrices(0, apr, expectedFunds);

    // 3. USDC blacklists `user` (fork prank on USDC blacklist role)
    vm.prank(usdcBlacklister);
    IUSDC(underlying).blacklist(user);

    // 4. every claim path reverts inside safeTransfer
    vm.prank(user);
    vm.expectRevert(); // USDC revert on transfer to blacklisted
    cdoEpoch.claimWithdrawRequest();

    // 5. receipt cannot be moved to a clean address
    vm.prank(user);
    vm.expectRevert(abi.encodeWithSelector(NotAllowed.selector));
    IERC20Detailed(strategy).transfer(cleanAddr, receipt);

    // 6. funds remain stuck in IdleCreditVault permanently
    assertGt(underlying.balanceOf(strategy), 0);
}
```

The same sequence applies post-default via `finalizeDefault` + `_transferDefaultRecovery`, and for instant withdrawals via `claimInstantWithdrawRequest`.

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L243-295)
```text
  function requestWithdraw(uint256 _amount, address _user, uint256 _principal) external {
    _onlyIdleCDO();
    _ensureDefaultRecoveryInitialized();
    if (_amount == 0) return;
    if (defaultRecoveryFinalized) {
      // user should first claim old already-funded withdraw requests before requesting new ones after default
      if (_hasWithdrawRequest(_user) || instantWithdrawsRequests[_user] != 0 || postDefaultRequests[_user] != 0) {
        revert NotAllowed();
      }
      // Preserve request/claim UX after default without increasing borrower-facing pendingWithdraws.
      // The CDO passes an already-haircut amount because finalization lowered virtualPrice first.
      _burn(msg.sender, _amount);
      _mint(_user, _amount);
      postDefaultRequests[_user] = _amount;
      return;
    }
    bool isClosed = IIdleCDOEpochVariant(idleCDO).epochEndDate() == 0;
    uint256 currentEpoch = epochNumber;
    uint256 lossEpoch = lastWithdrawRequest[_user];
    uint256 lossRecoveryPrice = lossRecoveryPriceByEpoch[lossEpoch];
    if (
      lossRecoveryPrice != 0 &&
      (withdrawsRequestsByEpoch[_user][lossEpoch] != 0 ||
      (apr0Users[_user].principal != 0 && apr0Users[_user].principalEpoch == lossEpoch))
    ) {
      // A loss-adjusted receipt must be claimed before opening a later request, otherwise
      // `lastWithdrawRequest` would stop pointing to the epoch that stores its haircut.
      revert NotAllowed();
    }
    // burn strategy tokens from cdo (we don't burn future interest here, only the principal)
    _burn(msg.sender, _principal);
    // mint equal amount of strategy tokens to the user as receipt (interest included), useful in case of default
    _mint(_user, _amount);
    // A successfully closed pool already recalled all funds and has no later stopEpoch.
    if (!isClosed) {
      // Global amount that stopEpoch must source from borrower/strategy for all pending receipts.
      pendingWithdraws += _amount;
    }
    // save the epoch of the last withdraw request (buffer + epochDuration is 1 epoch)
    lastWithdrawRequest[_user] = currentEpoch;
    // APR=0 requests keep separate accounting and settle interest at stopEpoch.
    // `_amount` here is the post-management-fee principal bucket for that flow.
    if (unscaledApr == 0 && !isClosed) {
      _requestWithdrawApr0(_amount, _user);
    } else {
      // increase the withdraw requests for the user
      // we record both per-user (old, kept for compatibility) and per-epoch so
      // on finalization we can distinguish "default-epoch pending receipts"
      // from old funded receipts.
      withdrawsRequests[_user] += _amount;
      withdrawsRequestsByEpoch[_user][currentEpoch] += _amount;
    }
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L301-350)
```text
  function claimWithdrawRequest(address _user) external returns (uint256 amount) {
    _onlyIdleCDO();
    if (defaultRecoveryFinalized) {
      // Post-default requests are already priced after the haircut and backed by the reserve,
      // so they must not fall through to the defaulted-epoch receipt logic.
      amount = _claimPostDefaultWithdrawRequest(_user);
      if (amount != 0) return amount;
      // Only receipts created in the defaulted epoch are haircutted here; old fulfilled
      // receipts are handled below at par if they were already funded before default.
      amount = _claimDefaultedWithdrawRequest(_user);
    }
    amount += _claimLossAdjustedWithdrawRequest(_user);
    return amount + _claimFundedWithdrawRequest(_user);
  }

  /// @notice Claim a funded non-default withdraw request at par.
  /// @param _user address of the user
  /// @return amount amount claimed
  function _claimFundedWithdrawRequest(address _user) internal returns (uint256 amount) {
    // User should wait at least an epoch before claiming the withdraw. Once the epoch is over user can withdraw 
    // at any time even if a new epoch started. 
    // So if epochNumber is the same as the last withdraw request then we revert. Epoch number is increased at stopEpoch
    // NOTE: If a user does not claim a withdraw request and instead requests another withdraw, he will have to wait
    // for another epoch to claim both requests.
    // NOTE 2: if borrower defaults, old withdraw requests can still be claimed
    if (IIdleCDOEpochVariant(idleCDO).epochEndDate() != 0 && (epochNumber <= lastWithdrawRequest[_user])) {
      revert NotAllowed();
    }
    // settle APR=0 requests once the related epoch has ended
    _settleApr0(_user);
    Apr0UserData storage _apr0User = apr0Users[_user];
    // Claim includes:
    // - settled APR0 principal from finalized epochs
    // - still-open APR0 principal: if pool-close mode was used (_interest == 1), IdleCDO sets
    //   epochEndDate = 0 and claims can be immediate, while _settleApr0 can still skip settlement
    //   for the current request epoch (reqEpoch >= epochNumber).
    // - settled APR0 interest
    uint256 normalAmount = withdrawsRequests[_user];
    uint256 apr0PrincipalAmount = _apr0User.settledPrincipal + _apr0User.principal;
    uint256 apr0InterestAmount = _apr0User.settledInterest;
    amount = normalAmount + apr0PrincipalAmount + apr0InterestAmount;
    // burn strategy tokens 1:1 with the principal only (normal amount already includes interest)
    _burn(_user, normalAmount + apr0PrincipalAmount);
    withdrawsRequests[_user] = 0;
    lastWithdrawRequest[_user] = 0;
    if (apr0PrincipalAmount != 0 || apr0InterestAmount != 0) {
      delete apr0Users[_user];
    }
    _transferFundedClaim(_user, amount);
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L939-951)
```text
  function _transfer(address sender, address recipient, uint256 amount) internal virtual override {
    if (msg.sender != idleCDO) revert NotAllowed();
    super._transfer(sender, recipient, amount);
  }

  /// @notice Clear the deprecated receipt-token transfer flag.
  /// @dev Kept for upgrade compatibility. Enabling transfers is permanently disabled because
  /// receipt claims are address-bound; the manager may only clear a legacy `true` value.
  /// @param _canTransfer must be false
  function setCanTransfer(bool _canTransfer) external {
    if (msg.sender != manager || _canTransfer) revert NotAllowed();
    canTransfer = false;
  }
```

**File:** contracts/IdleCDOEpochVariant.sol (L965-979)
```text
  /// @notice Claim a withdraw request from the vault. Can be done when at least 1 epoch passed
  /// since last withdraw request
  function claimWithdrawRequest() external {
    // underlyings requested, here we check that user waited at least one epoch and that borrower
    // did not default upon repayment (old requests can still be claimed)
    IdleCreditVault(strategy).claimWithdrawRequest(msg.sender);
  }

  /// @notice Claim an instant withdraw request from the vault. Can be done when epoch is running
  /// as funds will get transferred from borrower when epoch starts
  function claimInstantWithdrawRequest() external {
    // Check that instant withdraws are available
    _checkNotAllowed(!allowInstantWithdraw);
    IdleCreditVault(strategy).claimInstantWithdrawRequest(msg.sender);
  }
```
