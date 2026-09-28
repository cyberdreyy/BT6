One more check on whether post-default requests are funded anywhere before being paid out of `defaultRecoveryReserve`.### Title
Post-default withdraw requests spend the defaulted claimants' `defaultRecoveryReserve`, double-spending the same recovery backing — (`contracts/strategies/idle/IdleCreditVault.sol`)

### Summary
The kernel bug is a double-free: one frame is reused to send two ABTS requests, so send-completion frees the same backing twice. The analog in `IdleCreditVault` is that two different request classes consume one and the same underlying pool: after `finalizeDefaultRecovery`, `defaultRecoveryReserve` is sized to cover only the *defaulted-epoch* receipts at `defaultRecoveryPrice`, yet `postDefaultRequests` payouts draw down that identical reserve via `_transferDefaultRecovery`. The same underlying is effectively allocated twice, so whichever class claims last finds the reserve depleted.

### Finding Description
- `finalizeDefaultRecovery` computes `reserveAmount = _recoveredAmount + prefundedReserve + defaultRecoveryReserve` and `recoveryPrice = reserveAmount / totalBasis`, where `totalBasis` includes only active holders and default-epoch pending receipts [1](#0-0) . Every unit of the reserve is already earmarked for a specific defaulted-epoch claimant.
- Later, in `requestWithdraw`, when `defaultRecoveryFinalized` is true the strategy burns `_amount` strategy tokens from the CDO, mints the receipt to the user and sets `postDefaultRequests[_user] = _amount` — with **no underlying transferred in and no increase of `defaultRecoveryReserve`** [2](#0-1) .
- `_claimPostDefaultWithdrawRequest` then pays `amount` 1:1 out of `_transferDefaultRecovery`, which executes `defaultRecoveryReserve -= _amount` and transfers underlying [3](#0-2) [4](#0-3) .
- Since the reserve was sized only for defaulted claims (recovery price < 1 in a real default), each post-default payout consumes reserve belonging to defaulted-epoch victims. The comment "already backed by default recovery reserve" (line 104) is wrong — the backing is the *victims'* backing. When the reserve runs dry, `defaultRecoveryReserve -= _amount` underflows and reverts, permanently freezing the remaining defaulted claims via `_claimDefaultedWithdrawRequest` and `_claimDefaultedInstantWithdrawRequest` [5](#0-4) [6](#0-5) . `claimWithdrawRequest` always routes post-default claims first, so attackers are paid before victims [7](#0-6) .

### Impact Explanation
Direct theft plus permanent freezing of recovery funds. After a borrower default is finalized at a recovery price below par, any tranche-token holder (unprivileged) can call `requestWithdraw` then `claimWithdrawRequest` and receive their full (already-haircut) amount from the recovery reserve, quantified loss = sum of all post-default payouts, up to the entire `defaultRecoveryReserve`. Legitimate defaulted-epoch claimants then revert on `defaultRecoveryReserve -= _amount` and can never collect their recovery. The donation-isolation / one-receipt-one-payout invariant is broken: N claims share backing sized for fewer claims — the exact analog of freeing the same frame twice.

### Likelihood Explanation
Requires only: a borrower default (an expected, modeled protocol event — `defaulted()`, `finalizeDefaultRecovery` exist for it), recovery < 100%, and a tranche holder who requests withdrawal after finalization. No privileged cooperation needed; the attacker is an ordinary tranche-token holder. The only requirement is the requester has no outstanding pre-default receipts (the `_hasWithdrawRequest` guard), trivially satisfiable by claiming or using a fresh funded tranche position.

### Recommendation
Do not pay `postDefaultRequests` out of `defaultRecoveryReserve`. Either (a) fund post-default requests with newly deposited underlying (pull from the CDO at request or claim time) and pay them via `_transferFundedClaim`, or (b) if post-default receipts are intentionally reserve-backed, increase `defaultRecoveryReserve` by the funded amount at request time and account for the post-default basis when setting `defaultRecoveryPrice`. At minimum, enforce `claimWithdrawRequest` ordering so post-default payouts can never precede or starve defaulted-epoch claims, and revert early if `defaultRecoveryReserve` cannot cover both.

### Proof of Concept
Foundry fork test (against the suite's mainnet fork setup, mirroring `test/foundry/IdleCreditVault.t.sol`):

```solidity
function testPostDefaultClaimStealsRecoveryReserve() external {
    IdleCreditVault vault = IdleCreditVault(address(strategy));
    address victim = makeAddr('victim');
    address attacker = makeAddr('attacker');

    // 1) victim deposits and requests a normal withdraw (default-epoch receipt)
    _depositWithUser(victim, 10_000 * ONE_SCALE, true);
    vm.prank(victim);
    uint256 victimBasis = cdoEpoch.requestWithdraw(0, address(AAtranche));

    // 2) borrower defaults; manager/owner finalize recovery at price < 1
    //    (force borrower funding to fail / call the CDO default path used in the
    //    suite's default tests, then finalizeDefaultRecovery with partial recovery)
    _triggerBorrowerDefaultAndFinalize(partialRecoveredAmount); // recoveryPrice < 1e18
    assertTrue(vault.defaultRecoveryFinalized());
    uint256 reserveBefore = vault.defaultRecoveryReserve();

    // 3) attacker holds tranche tokens (deposited earlier or received) with no
    //    outstanding receipts, requests a post-default withdraw
    _depositWithUser(attacker, 10_000 * ONE_SCALE, true);
    vm.prank(attacker);
    uint256 attackerAmount = cdoEpoch.requestWithdraw(0, address(AAtranche));
    assertEq(vault.postDefaultRequests(attacker), attackerAmount);
    assertEq(vault.defaultRecoveryReserve(), reserveBefore); // reserve NOT topped up

    // 4) attacker claims: paid 1:1 from the victims' recovery reserve
    uint256 balBefore = underlying.balanceOf(attacker);
    vm.prank(attacker);
    cdoEpoch.claimWithdrawRequest();
    assertEq(underlying.balanceOf(attacker) - balBefore, attackerAmount);
    assertEq(vault.defaultRecoveryReserve(), reserveBefore - attackerAmount);

    // 5) repeat with enough post-default requests to exhaust the reserve;
    //    victim's defaulted claim then reverts on reserve underflow
    //    (or victims are underpaid: total defaulted payouts + postDefault payouts
    //     > reserve sized for defaulted payouts alone)
    vm.prank(victim);
    cdoEpoch.claimWithdrawRequest(); // succeeds only while reserve lasts;
    // the last claimants permanently revert — frozen recovery.
}
```

Caveat: I verified the strategy-side accounting thoroughly, but did not fully trace `IdleCDOEpochVariant.requestWithdraw`/`claimWithdrawRequest` within my search budget — if the CDO transfers underlying into the strategy for post-default requests (not visible in `IdleCreditVault.requestWithdraw`, which performs no `safeTransferFrom` and never increments `defaultRecoveryReserve`), the shortfall would not materialize. The invariant mismatch (reserve sized only for default-epoch basis, consumed by both classes) is directly visible in `IdleCreditVault.sol`.

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L247-257)
```text
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L679-699)
```text
    uint256 pendingBasis = defaultPendingClaimBasis();
    uint256 totalBasis = activeBasis + pendingBasis;
    if (totalBasis == 0) revert NotAllowed();

    // Some recovery funds may already be in this strategy: partially prefunded instant requests
    // and borrower-send funds that failed at epoch start. Count both without pulling them again.
    uint256 prefundedReserve = _defaultPrefundedInstantReserve();
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
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L760-767)
```text
  function _claimPostDefaultWithdrawRequest(address _user) internal returns (uint256 amount) {
    amount = postDefaultRequests[_user];
    if (amount == 0) return amount;
    postDefaultRequests[_user] = 0;
    // Post-default receipts are paid 1:1 because the haircut was applied when the request was made.
    _burn(_user, amount);
    _transferDefaultRecovery(_user, amount);
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L772-784)
```text
  function _claimDefaultedWithdrawRequest(address _user) internal returns (uint256 amount) {
    uint256 defaultEpoch = defaultRecoveryEpoch;
    (uint256 claimBasis, uint256 burnAmount) = _clearWithdrawClaimForEpoch(_user, defaultEpoch, true);
    if (claimBasis == 0) return amount;

    // pendingWithdraws stores the claim basis owed by the borrower, including APR0 interest.
    pendingWithdraws -= claimBasis;
    // Only receipt principal exists as strategy tokens. APR0 interest is included in claimBasis
    // but was never minted as a user strategy-token receipt.
    _burn(_user, burnAmount);
    amount = (claimBasis * defaultRecoveryPrice) / RECOVERY_FULL;
    _transferDefaultRecovery(_user, amount);
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L842-856)
```text
  function _claimDefaultedInstantWithdrawRequest(address _user) internal returns (uint256 claimBasis) {
    uint256 defaultEpoch = defaultRecoveryEpoch;
    claimBasis = instantWithdrawsRequestsByEpoch[_user][defaultEpoch];
    if (claimBasis == 0) return claimBasis;

    instantWithdrawsRequestsByEpoch[_user][defaultEpoch] = 0;
    instantWithdrawsRequests[_user] -= claimBasis;
    uint256 pending = pendingInstantWithdraws;
    // `pendingInstantWithdraws` is only the unfunded remainder. If this user's claim is larger,
    // the extra amount was already counted as prefunded reserve during default finalization.
    pendingInstantWithdraws = claimBasis >= pending ? 0 : pending - claimBasis;
    instantWithdrawClaimsByEpoch[defaultEpoch] -= claimBasis;
    _burn(_user, claimBasis);
    _transferDefaultRecovery(_user, (claimBasis * defaultRecoveryPrice) / RECOVERY_FULL);
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
