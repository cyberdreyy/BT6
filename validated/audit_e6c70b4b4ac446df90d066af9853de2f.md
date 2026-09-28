### Title
Fixed-recipient withdraw claims and non-transferable receipts permanently freeze funds for blacklisted-underlying users - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
The withdraw claim path in the IdleCreditVault/IdleCDOEpochVariant system hardcodes the payout recipient to the address that owns the withdraw receipt, and the receipt itself cannot be transferred to another address. The vault's underlying is a blacklistable fiat-backed token (USDC-class). If a lender's address is blacklisted by the underlying token issuer after requesting a withdrawal, their funded claim is permanently frozen: they cannot claim (the transfer to their address reverts) and cannot escape the claim to a clean address (receipts are soulbound by `_transfer`, and queue request records are plain storage keyed to `msg.sender`).

### Finding Description
All underlyings payout paths send tokens only to the receipt owner, with no `recipient` parameter:

- `claimWithdrawRequest(_user)` and `claimInstantWithdrawRequest(_user)` are `_onlyIdleCDO()` gated and pay out via `_transferFundedClaim(_user, amount)`, which calls `underlyingToken.safeTransfer(_user, _amount)` — `_user` is always the original requester forwarded by the CDO as `msg.sender` [1](#0-0) [2](#0-1) .
- Post-default and loss-adjusted claims have the same fixed-recipient pattern via `_transferDefaultRecovery(_user, ...)` [3](#0-2) .
- Escape via receipt transfer is impossible: the strategy-token receipt overrides `_transfer` to revert unless `msg.sender == idleCDO` [4](#0-3) , and `setCanTransfer` can only ever clear the flag to `false` [5](#0-4) .
- The epoch queue has the same pattern: `claimWithdrawRequest(_epoch)` pays `underlying.safeTransfer(msg.sender, ...)` and `userWithdrawalsEpochs[msg.sender]` is non-transferable storage [6](#0-5) .

Sequence: a KYC-passed lender calls `requestWithdraw` while the epoch is running; after `stopEpoch` the borrower funds `pendingWithdraws` via `collectWithdrawFunds` and the lender's receipt is backed by vault-held underlyings [7](#0-6) . If the lender's EOA is then added to the underlying token's blacklist (e.g., USDC), every subsequent `claimWithdrawRequest` reverts inside `safeTransfer`. The lender cannot transfer the receipt to a clean address, cannot reroute the payout, and re-requesting is blocked (`_hasWithdrawRequest` reverts post-default; `lastWithdrawRequest` guards pre-default). This is identical to the Particle M-08 class: fixed recipient + non-transferable claim position + blacklistable asset.

### Impact Explanation
Permanent freezing of the full funded withdraw amount for the affected user. The underlyings are already pulled into the vault/queue and earmarked for the claimant, so the funds sit in the contract but are unreachable — a total loss for that claim, bounded only by the user's claim size. Default-recovery and loss-adjusted claims are affected equally since they share the same `_user`-bound payout.

### Likelihood Explanation
Requires the underlying to implement a blacklist (true for USDC, the underlying used throughout the test suite) and for the token issuer — not the user — to blacklist the address after the request. This is an external event outside any party's control, not attacker-initiated; likelihood is low but nonzero (OFAC-driven USDC blacklists are documented). Note this does not create a profit vector for any unprivileged attacker; it is a liveness/asset-recovery defect, matching the severity assigned in the source report.

### Recommendation
Add a `recipient` parameter to the claim path: `claimWithdrawRequest(uint256 _epoch, address _recipient)` in the queue, and `claimWithdrawRequest(address _user, address _recipient)` / `claimInstantWithdrawRequest(address _user, address _recipient)` forwarded through the CDO, paying out via `underlyingToken.safeTransfer(_recipient, amount)` while still burning receipts keyed to `_user`. If blacklist-circumvention concerns are preferred over recoverability (the position the original judge took), document the risk explicitly so depositors understand a mid-epoch blacklist event forfeits funded claims.

### Proof of Concept
```solidity
// Fork PoC (Foundry), assuming MockERC20WithBlacklist or real USDC on mainnet fork
function testBlacklistFrozenClaim() external {
    // user deposits and requests withdraw during running epoch
    vm.startPrank(user);
    idleCDO.depositAA(10000e6);
    cdoEpoch.requestWithdraw(0, cdoEpoch.AATranche());
    vm.stopPrank();

    // epoch stops, borrower funds pending withdraws
    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.prank(manager);
    cdoEpoch.stopEpoch(0, expectedFunds);

    // token issuer blacklists user (e.g., USDC admin)
    blacklistToken.blacklist(user);

    // claim reverts inside safeTransfer -> funds frozen in vault forever;
    // receipt cannot be moved (IdleCreditVault._transfer reverts for non-CDO callers)
    vm.prank(user);
    vm.expectRevert();
    cdoEpoch.claimWithdrawRequest();
}
```

Caveat: I verified the `_user`-bound payout and non-transferable receipt logic in `IdleCreditVault` and the queue claim, but could not fully read `IdleCDOEpochVariant.claimWithdrawRequest` to confirm no recipient/pass-through exists there — the grep hits indicate the standard `msg.sender` forwarding pattern consistent with the rest of the codebase.

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L301-314)
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L897-907)
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
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L912-917)
```text
  function _transferDefaultRecovery(address _user, uint256 _amount) internal {
    if (_amount == 0) return;
    // Every defaulted or post-default claim consumes the isolated recovery reserve.
    defaultRecoveryReserve -= _amount;
    underlyingToken.safeTransfer(_user, _amount);
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L939-942)
```text
  function _transfer(address sender, address recipient, uint256 amount) internal virtual override {
    if (msg.sender != idleCDO) revert NotAllowed();
    super._transfer(sender, recipient, amount);
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L948-951)
```text
  function setCanTransfer(bool _canTransfer) external {
    if (msg.sender != manager || _canTransfer) revert NotAllowed();
    canTransfer = false;
  }
```

**File:** contracts/IdleCDOEpochQueue.sol (L393-411)
```text
  function claimWithdrawRequest(uint256 _epoch) external {
    // check if withdraw requests were processed and claimed for the epoch
    uint256 _withdrawPrice = epochWithdrawPrice[_epoch];
    _checkNotAllowed(
      (_withdrawPrice == 0 && !isEpochWithdrawZero[_epoch]) ||
      epochPendingClaims[_epoch] != 0
    );
    // amount is in tranche tokens
    uint256 amount = userWithdrawalsEpochs[msg.sender][_epoch];
    if (amount == 0) {
      return;
    }
    // reset user withdraw request counter for the epoch
    userWithdrawalsEpochs[msg.sender][_epoch] = 0;
    // transfer underlyings to user based on the withdraw price of that epoch
    if (_withdrawPrice != 0) {
      IERC20Detailed(underlying).safeTransfer(msg.sender, amount * _withdrawPrice / ONE_TRANCHE);
    }
  }
```
