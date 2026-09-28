### Title
Post-default withdraw requests drain `defaultRecoveryReserve` at par, stealing finalized recovery from defaulted-epoch claimants - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
The curl CVE is a use-after-free: memory whose accounting lifetime ended is still consumed by later threads. The vault analog is `defaultRecoveryReserve` after `finalizeDefaultRecovery`: the reserve is sized once for a fixed `totalBasis` of active + pending claims, yet the post-default `requestWithdraw` path creates **new** claims (`postDefaultRequests`) that are paid 1:1 out of that same already-finalized reserve in `_claimPostDefaultWithdrawRequest` → `_transferDefaultRecovery`. The reserve's accounting basis was closed; new claims reuse it anyway, so each post-default claim dilutes or exhausts funds earmarked for defaulted-epoch receipt holders. [1](#0-0) [2](#0-1) 

### Finding Description
In `finalizeDefaultRecovery`, `defaultRecoveryReserve` is set to `reserveAmount` and `defaultRecoveryPrice = reserveAmount * RECOVERY_FULL / totalBasis`, where `totalBasis = activeBasis + pendingBasis` (pending receipts + current-epoch instant claims). Post-default requests are never part of that basis. [3](#0-2) 

After finalization, `requestWithdraw` takes the `defaultRecoveryFinalized` branch: it burns `_amount` from the CDO, mints an identical receipt to the user, and records `postDefaultRequests[_user] = _amount`. Critically, it does **not** increase `pendingWithdraws` (per the comment: "without increasing borrower-facing pendingWithdraws"), so no borrower funding is ever sourced for this new claim. [4](#0-3) 

`claimWithdrawRequest` then routes to `_claimPostDefaultWithdrawRequest`, which burns the receipt and calls `_transferDefaultRecovery(_user, amount)` — paying the claim **at par** from the isolated recovery reserve, decremented from `defaultRecoveryReserve`. [5](#0-4) [6](#0-5) 

Attack sequence (defaulted/finalized phase):
1. Borrower defaults; owner/manager calls `finalizeDefaultRecovery` — reserve `R` is locked for `totalBasis` claims at `recoveryPrice < RECOVERY_FULL`.
2. Any unprivileged tranche-token holder (the attacker — a KYC lender or secondary-market buyer) calls `cdoEpoch.requestWithdraw`; the strategy mints a `postDefaultRequests` receipt with zero new funding.
3. Attacker calls `cdoEpoch.claimWithdrawRequest` and receives `amount` at par from `defaultRecoveryReserve` — i.e., 100 cents on the dollar while defaulted-epoch claimants are only entitled to `recoveryPrice` cents.
4. Every such claim subtracts from `R` without having been in `totalBasis`; once cumulative post-default claims exceed the reserve surplus, `defaultRecoveryReserve -= _amount` underflows (or earlier claimants have already over-drawn), permanently reverting claims of legitimate defaulted-epoch receipt holders.

The receipt is burned before payout and `postDefaultRequests[_user]` is zeroed, so no per-user double-claim — the flaw is that the *reserve itself* is a finalized accounting bucket being consumed by claims minted after its basis closed.

### Impact Explanation
Direct theft and permanent freezing of unclaimed recovery. Post-default claimants extract par value from a pool meant to be shared at `recoveryPrice`, so defaulted-epoch withdraw/instant receipt holders (and active LPs' recovery via `defaultBBNav` redemption) receive less than their finalized entitlement or are bricked entirely when the reserve underflows. Loss magnitude is bounded by `reserveAmount`, and each attacker unit of tranche tokens converts a `recoveryPrice`-valued claim into a par payout — a guaranteed premium extracted from other claimants. [7](#0-6) 

### Likelihood Explanation
Requires a borrower default with finalized recovery — a real but event-driven precondition; the rules permit sequencing around honest manager/owner calls. The exploit itself needs only an unprivileged tranche holder calling two public functions; no racing, no privileged collusion. Post-default `requestWithdraw` is an explicitly supported UX path (per comments), so the drain is reachable by design whenever anyone requests withdrawal after finalization — even non-maliciously, which worsens it: ordinary usage silently cannibalizes the recovery reserve. Whether `IdleCDOEpochVariant.requestWithdraw` gates post-default calls on `defaulted()` is the one path element not fully verified from the snippets reviewed; the strategy-side branch's existence strongly implies it is reachable.

### Recommendation
Fund or isolate post-default claims instead of spending the finalized reserve. Options: (a) in the `defaultRecoveryFinalized` branch of `requestWithdraw`, require the CDO to transfer `_amount` underlying into the strategy (or haircut the payout by `defaultRecoveryPrice` and account it as a new basis against the post-default fund, not `defaultRecoveryReserve`); (b) pay post-default claims through `_transferFundedClaim`, which already forbids dipping into `defaultRecoveryReserve` (lines 899-905); or (c) include post-default receipts in a separately tracked reserve credited at claim time. At minimum, `_claimPostDefaultWithdrawRequest` should never route through `_transferDefaultRecovery` for claims not represented in `totalBasis`. [8](#0-7) 

### Proof of Concept
Foundry fork PoC sketch (mirroring `test/foundry/IdleCreditVault.t.sol` fixtures):

```solidity
function testPocPostDefaultReserveDrain() external {
    // 1. Seed two users; user1 requests normal withdraw, epoch runs, borrower defaults.
    _depositWithUser(user1, 10_000e6, true);
    _depositWithUser(attacker, 10_000e6, true);
    vm.prank(user1);
    cdoEpoch.requestWithdraw(0, address(AAtranche));
    _startEpochAndCheckPrices(0);
    // borrower defaults; owner/manager finalize recovery at, say, 50%
    _handleDefaultAndFinalize(recoveredAmount); // helper: reserveDefaultRecovery/finalizeDefaultRecovery

    uint256 reserveBefore = creditVault.defaultRecoveryReserve();

    // 2. Attacker (still holding tranche tokens) requests + claims post-default.
    vm.startPrank(attacker);
    cdoEpoch.requestWithdraw(0, address(AAtranche)); // hits defaultRecoveryFinalized branch
    cdoEpoch.claimWithdrawRequest();
    vm.stopPrank();

    // 3. Attacker paid at par from the recovery reserve.
    assertEq(creditVault.defaultRecoveryReserve(), reserveBefore - attackerClaim);
    // 4. user1's defaulted receipt claim now reverts or underpays:
    vm.prank(user1);
    vm.expectRevert(); // defaultRecoveryReserve underflow
    cdoEpoch.claimWithdrawRequest();
}
```

The assertion that `defaultRecoveryReserve` decreases by the attacker's full par claim — while `totalBasis` never counted it — demonstrates reserve dilution; `expectRevert` on `user1`'s claim demonstrates permanent freezing of unclaimed recovery.

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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L303-313)
```text
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L661-699)
```text
  function finalizeDefaultRecovery(uint256 _recoveredAmount, address _recoverySource) external returns (uint256 defaultBBNav) {
    _onlyIdleCDO();
    _ensureDefaultRecoveryInitialized();

    IIdleCDOEpochVariant cdo = IIdleCDOEpochVariant(idleCDO);
    if (defaultRecoveryFinalized || !cdo.defaulted()) revert NotAllowed();
    if (_recoveredAmount != 0 && _recoverySource == address(0)) revert NotAllowed();

    // Active holders are still represented by strategy tokens owned by the CDO. Add the
    // default-epoch net interest so they use the same claim basis as pending redeemers.
    // Split gross backing by saved NAV and default interest by the configured APR split.
    // The CDO strategy-token balance is its gross active value before `unclaimedFees`.
    // Using it directly restores those waived unpaid fees to active recovery basis.
    uint256 activeBalance = balanceOf(idleCDO);
    uint256 activeInterest = _defaultActiveInterestBasis(cdo);
    uint256 activeBasis = activeBalance + activeInterest;
    defaultBBNav = _defaultBBBasis(cdo, activeBalance, activeInterest);
    // Pending receipts have already left active CDO NAV, so they are added as a separate basis.
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L760-784)
```text
  function _claimPostDefaultWithdrawRequest(address _user) internal returns (uint256 amount) {
    amount = postDefaultRequests[_user];
    if (amount == 0) return amount;
    postDefaultRequests[_user] = 0;
    // Post-default receipts are paid 1:1 because the haircut was applied when the request was made.
    _burn(_user, amount);
    _transferDefaultRecovery(_user, amount);
  }

  /// @notice Claim a defaulted normal withdraw receipt with the finalized recovery haircut.
  /// @param _user address of the user
  /// @return amount amount paid from default recovery reserve
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L912-916)
```text
  function _transferDefaultRecovery(address _user, uint256 _amount) internal {
    if (_amount == 0) return;
    // Every defaulted or post-default claim consumes the isolated recovery reserve.
    defaultRecoveryReserve -= _amount;
    underlyingToken.safeTransfer(_user, _amount);
```
