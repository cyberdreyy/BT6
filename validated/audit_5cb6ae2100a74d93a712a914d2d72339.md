### Title
KYC/allowlist check is enforced at request time but never re-verified at claim time — a wallet whose Keyring credential lapses after queuing still mints tranche tokens and withdraws underlying - (File: contracts/IdleCDOEpochQueue.sol)

### Summary
The external report's bug class is "policy checked on the user-supplied name, never re-checked on the resolved result." The idle-tranches analog is the epoch queue's Keyring allowlist: `requestDeposit` and `requestWithdraw` verify `isWalletAllowed` at enqueue time, but the fulfillment functions `claimDepositRequest` and `claimWithdrawRequest` mint tranche tokens and pay underlying with no allowlist re-check, even though epochs last days and credentials can be revoked in between.

### Finding Description
`requestDeposit` and `requestWithdraw` gate entry via `_checkAllowed(msg.sender)`, which resolves to `IKeyring(keyring).checkCredential(keyringPolicyId, wallet)` on the CDO. This is a point-in-time check — analogous to Deno checking the destination hostname against `--deny-net`. [1](#0-0) [2](#0-1) [3](#0-2) 

However, the functions that actually deliver value — `claimDepositRequest` (mints tranche tokens at `epochPrice`) and `claimWithdrawRequest` (pays underlying at `epochWithdrawPrice`) — perform zero re-verification of `isWalletAllowed`, `isEpochRunning`, or any credential state. They only key payouts to `msg.sender`'s recorded position. [4](#0-3) [5](#0-4) 

The same gap exists one layer down: `IdleCDOEpochVariant.claimWithdrawRequest` and `claimInstantWithdrawRequest` forward to `IdleCreditVault.claimWithdrawRequest(msg.sender)` / `claimInstantWithdrawRequest(msg.sender)` with no `isWalletAllowed` guard, while `depositDuringEpoch` and `requestWithdraw` both enforce it. [6](#0-5) [7](#0-6) 

Sequence (attacker = ordinary KYC-passing lender, epoch phase = running epoch N):

1. Attacker passes Keyring credential, calls `queue.requestDeposit(X)` — `_checkAllowed` passes. Underlying sits in the queue as `epochPendingDeposits[N+1]`.
2. During epoch N, the honest Keyring admin revokes the attacker's credential (sanctions, expired KYC). This is normal operation, not a privileged attack.
3. Manager runs `processDepositRequests`/epoch settlement; `epochPrice[N+1]` is set and tranche tokens are funded to the queue — no wallet check occurs anywhere in the processing path.
4. Attacker calls `claimDepositRequest(N+1)` and receives freshly minted tranche tokens despite failing `isWalletAllowed` at that moment. Equivalently for `requestWithdraw` → `claimWithdrawRequest(epoch)` paying underlying to a now-revoked wallet.

The broken invariant is the access invariant the deployment configs rely on (every live credit vault sets `keyringPolicy`, e.g. `creditlaserdigitalusdc`). The protocol intends "only credentialed wallets hold tranche exposure," but the credential is resolved once at enqueue and never re-resolved at delivery — the same TOCTOU shape as hostname-check vs resolved-IP.

Guards that fail to stop it: `_checkNotAllowed` on `epochPrice`/`epochPendingClaims` only enforces settlement ordering; `nonReentrant` and `_onlyIdleCDO` are orthogonal; there is no claim-side credential gate anywhere in the queue or vault claim path.

### Impact Explanation
A wallet that no longer satisfies the pool's compliance policy can still be minted tranche tokens and redeem pool underlying. Fund-side impact is bounded to the attacker's own queued principal (no direct theft of others' funds), but the whitelist — the control that decides who may hold exposure and receive yield — is silently bypassed for the entire settlement window. All interest accrued between revocation and claim is delivered to the disallowed wallet. This is exactly the "restriction silently circumvented" impact of the reference advisory.

### Likelihood Explanation
Requires a credential change between queue and claim (revocation, expiry, policy update). Deposits can sit queued for a full epoch plus buffer (durations like 12 days in configs), so the window is wide. Only needs an unprivileged lender plus a routine Keyring admin action — no privileged collusion.

### Recommendation
Add `_checkNotAllowed(!cdoEpoch.isWalletAllowed(msg.sender))` (or reuse `_checkAllowed` minus the epoch-running requirement, since claims legitimately occur when the epoch is not running) to `claimDepositRequest` and `claimWithdrawRequest` in `IdleCDOEpochQueue.sol`, and add `isWalletAllowed` to `claimWithdrawRequest`/`claimInstantWithdrawRequest` in `IdleCDOEpochVariant.sol`. Document the trade-off: re-checking also blocks sanctioned users from exiting, so alternatively gate only the minting claim (`claimDepositRequest`) while always permitting withdrawal claims.

### Proof of Concept
Foundry sketch (pattern matches `test/foundry/IdleCDOEpochQueue.t.sol`):

```solidity
function testRevokedUserStillClaimsQueuedDeposit() external {
    address user = makeAddr('user');
    // KYC passes at request time
    vm.mockCall(keyring, abi.encodeWithSelector(IKeyring.checkCredential.selector), abi.encode(true));

    // epoch running -> user queues deposit
    _depositWithUser(user, 100e6);          // queue.requestDeposit via helper

    // honest admin revokes credential mid-epoch
    vm.mockCall(keyring, abi.encodeWithSelector(IKeyring.checkCredential.selector), abi.encode(false));

    // user can no longer request anything
    vm.expectRevert(NotAllowed.selector);
    vm.prank(user);
    queue.requestWithdraw(1);

    // manager settles epoch; epochPrice set
    _processDepositsAndStartEpoch();

    // BUG: revoked user still mints tranche tokens — no isWalletAllowed re-check
    uint256 epoch = strategy.epochNumber();
    vm.prank(user);
    queue.claimDepositRequest(epoch);
    assertGt(IERC20(tranche).balanceOf(user), 0, "revoked wallet minted tranche tokens");
}
```

Caveat: I could not fully verify whether the missing claim-side check is a deliberate design choice (exit-rights argument) or documented as accepted behavior elsewhere in the repo, since my remaining iterations were exhausted; if intentional, the gap should at minimum be narrowed to the token-minting claim path.

### Citations

**File:** contracts/IdleCDOEpochQueue.sol (L103-118)
```text
  function requestDeposit(uint256 amount) external nonReentrant {
    // check if the wallet is allowed to deposit (ie epoch is running and keyring KYC completed)
    _checkAllowed(msg.sender);

    IdleCDOEpochVariant _cdo = IdleCDOEpochVariant(idleCDOEpoch);
    uint256 nextEpoch = IdleCreditVault(strategy).epochNumber() + 1;
    uint256 _prefundedWindow = prefundedDepositWindow;
    // Only the AA prefunded queue enforces a deposit cutoff for the next epoch.
    if (tranche == _cdo.AATranche() && _isPrefundedQueueEnabled()) {
      IdleCDOEpochVariantPrefunded(idleCDOEpoch).checkPrefunding(epochPendingDeposits[nextEpoch] + amount);
      // Once funds are prefunded, or once the subscription window is reached, the next epoch is closed.
      _checkNotAllowed(
        epochPrefundedDeposits[nextEpoch] != 0 || (
        _prefundedWindow != 0 && block.timestamp + _prefundedWindow >= _cdo.epochEndDate()
      ));
    }
```

**File:** contracts/IdleCDOEpochQueue.sol (L373-389)
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

**File:** contracts/IdleCDOEpochQueue.sol (L415-421)
```text
  function _checkAllowed(address wallet) internal view {
    IdleCDOEpochVariant cdoEpoch = IdleCDOEpochVariant(idleCDOEpoch);
    if (!cdoEpoch.isEpochRunning()) {
      revert EpochNotRunning();
    }
    _checkNotAllowed(!cdoEpoch.isWalletAllowed(wallet));
  }
```

**File:** contracts/IdleCDOEpochVariant.sol (L967-979)
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
  }
```

**File:** contracts/IdleCDOEpochVariant.sol (L984-987)
```text
  function isWalletAllowed(address _user) public view returns (bool) {
    address _keyring = keyring;
    return _keyring == address(0) || IKeyring(_keyring).checkCredential(keyringPolicyId, _user);
  }
```

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
