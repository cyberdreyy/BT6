### Title
Withdrawal claims and queued-deposit claims reuse request-time Keyring authorization — revoked wallets still claim after credential revocation - (File: contracts/IdleCDOEpochVariant.sol, contracts/IdleCDOEpochQueue.sol)

### Summary
The OpenClaw advisory describes handlers that resolved bearer auth once at startup and kept accepting a rotated-out token. The direct analog exists in the credit vault's two-step withdraw flow: `isWalletAllowed` (which calls `Keyring.checkCredential` live) is evaluated only when a request is *created*, while the claim paths that actually pay out underlying never re-resolve the credential. A lender whose Keyring policy credential is revoked after `requestWithdraw`/`requestDeposit` retains a valid "bearer token" (the strategy-token receipt / queued balance) and can claim the funds indefinitely, exactly matching "old token remains valid after operators believed it was rotated out."

### Finding Description
`IdleCDOEpochVariant.isWalletAllowed` re-reads `keyring` and calls `IKeyring(_keyring).checkCredential(keyringPolicyId, _user)` fresh on every invocation, so the runtime "secret" resolution itself is per-call and correct [1](#0-0) . The problem is where it is *not* invoked:

- `requestWithdraw` / `requestInstantWithdraw` gate on `isWalletAllowed(msg.sender)` at request time, but `claimWithdrawRequest()` and `claimInstantWithdrawRequest()` forward to the strategy with no wallet check at all [2](#0-1) .
- In `IdleCreditVault`, `claimWithdrawRequest`/`_claimFundedWithdrawRequest`/`claimInstantWithdrawRequest` only check `_onlyIdleCDO`, epoch maturation, and burn the user's minted receipt tokens; there is no credential re-check before `safeTransfer(_user, amount)` [3](#0-2) [4](#0-3) .
- The same stale-auth pattern exists in `IdleCDOEpochQueue`: `requestDeposit`/`requestWithdraw` call `_checkAllowed` (epoch running + `isWalletAllowed`), but `claimDepositRequest` and `claimWithdrawRequest` pay out `epochPrice`-denominated tranches/underlyings with no `isWalletAllowed` re-check [5](#0-4) .
- The minted receipts are address-bound (`_transfer` reverts unless caller is `idleCDO`), so the revoked user cannot launder the claim through a fresh-KYC wallet — but they also don't need to: the claim itself is unauthenticated [6](#0-5) .

Additionally, the tranche-token recipient is never validated: a non-Keyring wallet that legitimately obtains tranche tokens (the test `testNonKeyringBuyerCanFulfillExistingRequestButCannotWithdraw` shows a write-off-escrow fulfiller receives tranches while `checkCredential` returns false) is correctly blocked from *requesting* — but any wallet that was KYC-passing at request time keeps claim rights forever.

### Impact Explanation
Broken invariant: **access control**. Keyring credential revocation is the compliance enforcement mechanism of the vault (`keyring`/`keyringPolicyId` on the CDO, `KeyringIdleWhitelist.checkCredential` consulted per call). After the Keyring admin revokes a wallet's credential — e.g., a sanctioned/law-enforcement-blocked lender or a compromised-key account — operators reasonably believe the wallet can no longer interact. In reality:

- Any pending withdraw receipt minted before revocation still pays out principal + accrued interest at `claimWithdrawRequest`, and any funded instant receipt still pays at `claimInstantWithdrawRequest`.
- Any queued deposit in `IdleCDOEpochQueue` still mints tranche tokens to the revoked wallet via `claimDepositRequest`, and queued withdrawals still pay out.
- The exposure window is unbounded: unlike epoch-matured requests that expire, claims remain valid for the lifetime of the strategy (the "lifetime of the process" analog).

Quantified loss: every wei requested by the revoked wallet before revocation (its full pending basis in `withdrawsRequestsByEpoch`/`apr0Users`/`postDefaultRequests`/`instantWithdrawsRequestsByEpoch`, or `userDepositsEpochs`/`userWithdrawalsEpochs` in the queue) remains claimable, including post-default recovery payments drawn from the isolated `defaultRecoveryReserve` via `_transferDefaultRecovery`, which is precisely the reserve meant to be paid only to legitimate claimants [7](#0-6) . In a compromised-key scenario, revocation is the *defense* — an attacker controlling the revoked key extracts the full claim balance that revocation was intended to freeze.

### Likelihood Explanation
Likelihood is conditional but realistic: the attacker is an ordinary KYC-passing lender (in-scope role) who requests a withdrawal, then has their credential revoked (honest Keyring admin action), then calls `claimWithdrawRequest`. No privileged collusion, no default, no timing manipulation is required — only the normal sequence `requestWithdraw → epoch stops → credential revoked → claimWithdrawRequest`. The bypass also survives defaults: `_claimDefaultedWithdrawRequest` and `_claimPostDefaultWithdrawRequest` pay from recovery reserve with no credential check. Existing guards (`_onlyIdleCDO`, `epochNumber > lastWithdrawRequest`, `defaultRecoveryReserve` isolation in `_transferFundedClaim`) only gate *who calls the strategy* and *when claims mature* — none re-resolve wallet authorization, so none stop it.

### Recommendation
Re-resolve the credential at claim time, mirroring the upstream fix of resolving auth per request:

- In `IdleCDOEpochVariant.claimWithdrawRequest()` and `claimInstantWithdrawRequest()`, add `_checkNotAllowed(!isWalletAllowed(msg.sender))` before delegating to the strategy (gated by `keyringAllowWithdraw` semantics if post-revocation claims for entitled funds are intentionally permitted — in that case document that revocation only blocks new requests, not payouts).
- In `IdleCDOEpochQueue.claimDepositRequest`/`claimWithdrawRequest`, call `cdoEpoch.isWalletAllowed(msg.sender)` (drop the `isEpochRunning` requirement of `_checkAllowed` since claims happen between epochs).
- Alternatively, if the intended design is "request-time KYC is sufficient," document it explicitly; under the advisory's threat model that is the vulnerable behavior.

### Proof of Concept
Reproducible Foundry scenario (against the existing `IdleCreditVault.t.sol` harness):

```solidity
function testClaimWithdrawAfterKeyringRevocation() external {
  uint256 amountWei = 10_000 * ONE_SCALE;

  // LP is KYC-passing; deposit and request withdraw during buffer
  _depositWithUser(LP, amountWei, true);           // mints AA tranches
  vm.prank(LP);
  cdoEpoch.requestWithdraw(amountWei, address(AAtranche)); // isWalletAllowed(LP) == true, receipt minted

  _startEpochAndCheckPrices(0);
  vm.warp(cdoEpoch.epochEndDate() + 1);
  _stopEpochAndCheckPrices(0, initialProvidedApr, _expectedFundsEndEpoch()); // request funded

  // Operator rotates: Keyring now reports LP's credential revoked
  vm.mockCall(
    cdoEpoch.keyring(),
    abi.encodeWithSelector(IKeyring.checkCredential.selector),
    abi.encode(false)
  );
  assertFalse(cdoEpoch.isWalletAllowed(LP), "credential revoked");

  // Revoked LP cannot open a new request...
  vm.prank(LP);
  vm.expectRevert(abi.encodeWithSelector(NotAllowed.selector));
  cdoEpoch.requestWithdraw(1, address(AAtranche));

  // ...but the stale receipt still pays out the full claim
  uint256 balPre = underlying.balanceOf(LP);
  vm.prank(LP);
  cdoEpoch.claimWithdrawRequest();                // no isWalletAllowed check -> succeeds
  assertGt(underlying.balanceOf(LP), balPre, "revoked wallet claimed funded withdrawal");
}
```

The same shape applies to `IdleCDOEpochQueue`: `requestDeposit` while KYC-valid → `vm.mockCall(checkCredential → false)` → `claimDepositRequest(epoch)` still transfers minted tranche tokens to the revoked wallet.

### Citations

**File:** contracts/IdleCDOEpochVariant.sol (L967-978)
```text
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
```

**File:** contracts/IdleCDOEpochVariant.sol (L984-987)
```text
  function isWalletAllowed(address _user) public view returns (bool) {
    address _keyring = keyring;
    return _keyring == address(0) || IKeyring(_keyring).checkCredential(keyringPolicyId, _user);
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L301-313)
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

**File:** contracts/IdleCDOEpochQueue.sol (L373-411)
```text
  function claimDepositRequest(uint256 _epoch) external {
    // Deposits can be claimed only after the epoch has been finalized and priced.
    _checkNotAllowed(
      epochPrice[_epoch] == 0 ||
      epochPendingDeposits[_epoch] != 0 ||
      epochPrefundedDeposits[_epoch] != 0
    );

    uint256 amount = userDepositsEpochs[msg.sender][_epoch];
    if (amount == 0) {
      return;
    }
    // reset user deposit for the epoch
    userDepositsEpochs[msg.sender][_epoch] = 0;
    // transfer tranche tokens to user based on the price of that epoch
    IERC20Detailed(tranche).safeTransfer(msg.sender, amount * ONE_TRANCHE / epochPrice[_epoch]);
  }

  /// @notice claim withdraw request
  /// @param _epoch epoch when withdraw request were processed
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
