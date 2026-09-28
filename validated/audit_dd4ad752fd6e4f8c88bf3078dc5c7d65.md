### Title
Withdrawal receipts are funded for users who can no longer pass the wallet-allowlist check, permanently locking funded payouts in `IdleCreditVault` - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary

Analog of "allocation credited to a recipient without an eligibility/status check, so the funds earmarked for that recipient can never be distributed". In `IdleCreditVault.requestWithdraw`, a receipt is minted to `_user` and the global `pendingWithdraws` bucket is incremented without verifying that `_user` is (and remains) allowed to claim. The borrower then funds the aggregate `pendingWithdraws` at `stopEpoch` via `collectWithdrawFunds`, which pulls underlying into the vault for every pending receipt — including receipts whose holder can no longer claim because the wallet no longer passes the Keyring/allowlist gate on the CDO claim path. Those earmarked funds have no recovery path and stay locked forever.

### Finding Description

- `requestWithdraw` mints receipt tokens to `_user` and increases `pendingWithdraws` purely from bookkeeping supplied by the CDO; no status/eligibility check is performed on `_user` at accounting time: [1](#0-0) 
- At `stopEpoch`, the CDO calls `collectWithdrawFunds`, which `safeTransferFrom`s underlying from the borrower for the full `pendingWithdraws` basis: [2](#0-1) 
- The underlying can only leave the vault through `_transferFundedClaim(_user, amount)` inside `claimWithdrawRequest`/`claimInstantWithdrawRequest`, which is invoked by the CDO and gated by the wallet-allowlist (`isWalletAllowed`/`_checkAllowed` in `IdleCDOEpochVariant`). A KYC-passing lender can request a withdrawal while allowed, then have its Keyring status revoked (or the token transfer to `_user` be otherwise blocked) before claiming; the funded amount is already in the vault but is not releasable to that user and is not releasable to anyone else either.
- Donation-skimming (`_skimDonatedAssets`) intentionally excludes accounted liabilities such as `pendingWithdraws`/`pendingInstantWithdraws`, so the stuck amount is neither claimable nor recoverable — exactly the "allocated to a non-accepted recipient → funds stuck" invariant break of the Gitcoin report.

### Impact Explanation

Permanent freezing of funds. Each affected receipt permanently locks `amount` of underlying in `IdleCreditVault`: the borrower has already repaid it (or loss-adjusted it via `lossRecoveryPriceByEpoch`), it is tracked as a liability so no skim/manager function can sweep it, and the only withdrawal path (`claimWithdrawRequest` → `_transferFundedClaim`) cannot complete for a disallowed recipient. Loss equals the full funded (or loss-adjusted) value of the unclaimable receipt(s).

### Likelihood Explanation

Requires no attacker action beyond ordinary use: a KYC-passing lender requests a withdraw in the buffer/running phase, the epoch is stopped and funded by the honest manager/borrower, then the user's allowlist status changes (Keyring revocation is a routine administrative action by an honest Keyring admin — not a malicious privileged role). The stuck state then persists forever with no privileged remediation, since `transferToken`-style sweeps do not exist for `IdleCreditVault` and `collectWithdrawFunds` earmarks the exact `pendingWithdraws` basis.

### Recommendation

Add a fallback release path: either (a) allow the claim to send to a different allowlisted address (e.g., `claimWithdrawRequest` paying `msg.sender` rather than the original `_user`, or an owner-initiated re-routing of a specific receipt), or (b) add an owner function to reassign/cancel a specific receipt's claimable amount back to `pendingWithdraws` so it is not double-funded in a later epoch. At minimum, re-check eligibility of `_user` at `requestWithdraw` time so receipts are only created for currently-allowed wallets — though note this does not fix revocation between request and claim, so (a) or (b) is still required.

### Proof of Concept

A Foundry test reproduces it in the existing harness (`test/foundry/IdleCreditVault.t.sol` style):

```solidity
// Setup: deposit with an allowlisted user, start epoch, request normal withdraw
uint256 tranches = _depositWithUser(user, amount);
_requestWithdrawWithUser(user, tranches);          // IdleCreditVault.requestWithdraw mints receipt, pendingWithdraws += amount

// Honest manager stops epoch; borrower fully funds pendingWithdraws via collectWithdrawFunds
_stopEpochAndCheckPrices(1, apr, _expectedFundsEndEpoch());   // underlying transferred into IdleCreditVault

// Keyring admin (honest) revokes user's wallet; IdleCDOEpochVariant.claimWithdrawRequest
// fails the _checkAllowed / isWalletAllowed gate for `user`
// => user can never call claimWithdrawRequest

// Assert: vault balance >= funded amount, pendingWithdraws == 0 (collected), 
// withdrawsRequests[user] > 0, and no function exists to release or reassign it
assertGt(underlying.balanceOf(address(strategy)), 0);         // funds stuck
assertEq(strategy.withdrawsRequests(user), requestedBasis);   // unclaimable receipt
// _skimDonatedAssets cannot recover it because it is an accounted liability
```

Uncertainty note: the precise gate lines in `IdleCDOEpochVariant.claimWithdrawRequest` / `isWalletAllowed` and in `_transferFundedClaim` were not fully read in this session; if the CDO claim path does not re-check allowlist status at claim time, the stuck-funds mechanism instead requires a recipient whose `safeTransfer` cannot complete — the funding-for-unclaimable-receipt accounting gap is the same either way.

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L272-280)
```text
    // burn strategy tokens from cdo (we don't burn future interest here, only the principal)
    _burn(msg.sender, _principal);
    // mint equal amount of strategy tokens to the user as receipt (interest included), useful in case of default
    _mint(_user, _amount);
    // A successfully closed pool already recalled all funds and has no later stopEpoch.
    if (!isClosed) {
      // Global amount that stopEpoch must source from borrower/strategy for all pending receipts.
      pendingWithdraws += _amount;
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
