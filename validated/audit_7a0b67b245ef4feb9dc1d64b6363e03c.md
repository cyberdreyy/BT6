### Title
Post-default withdraw claims drain `defaultRecoveryReserve`, freezing recovery owed to defaulted-epoch claimants - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
CVE-2023-2985 is a use-after-free: a resource that was released is still consumed. The analog in `IdleCreditVault` is a recovery-reserve lifetime bug: after `finalizeDefaultRecovery`, the isolated `defaultRecoveryReserve` is sized exactly for pre-default claim basis, yet post-default withdraw receipts created via `requestWithdraw` are paid 1:1 out of that same reserve (`_transferDefaultRecovery`) without any accounting path ever replenishing it. Post-default claimants therefore consume accounting units that were "freed" for a different claim class, and once cumulative claims exceed the finalized reserve, `defaultRecoveryReserve -= _amount` underflows and all remaining defaulted-epoch claims permanently revert.

### Finding Description
At finalization, `defaultRecoveryReserve` is set to `reserveAmount = _recoveredAmount + prefundedReserve + oldReserve`, and `defaultRecoveryPrice` is computed against `totalBasis = activeBasis + pendingBasis` (the defaulted-epoch claims only) [1](#0-0) . After finalization, `requestWithdraw` takes the `defaultRecoveryFinalized` branch: it mints a receipt and records `postDefaultRequests[_user]`, deliberately without touching `pendingWithdraws` [2](#0-1) . The claim path `_claimPostDefaultWithdrawRequest` pays the full `amount` through `_transferDefaultRecovery`, which unconditionally decrements `defaultRecoveryReserve` [3](#0-2) [4](#0-3) . No code path adds post-default borrower repayments into the reserve — `collectWithdrawFunds` only moves tokens into the strategy balance, and `reserveDefaultRecovery` reverts once `defaultRecoveryFinalized` is true [5](#0-4) . The reserve is thus a fixed budget meant for defaulted claimants, but it is spent by a claim class that was never in `totalBasis`.

### Impact Explanation
When post-default claims plus defaulted-epoch claims exceed the finalized `reserveAmount`, `defaultRecoveryReserve -= _amount` underflows (Solidity 0.8 checked arithmetic) and reverts. Every remaining defaulted-epoch claimant — both normal withdraw receipts (`_claimDefaultedWithdrawRequest`) and defaulted instant receipts (`_claimDefaultedInstantWithdrawRequest`) — is permanently unable to claim, even though their pro-rata recovery was crystallized at finalization. Loss equals the unclaimed defaulted receipt basis times `defaultRecoveryPrice`, up to the entire reserve; the post-default claimants effectively receive a 1:1 payout while earlier claimants are haircutted or frozen, breaking the "one receipt, one pro-rata payout" invariant and recovery isolation.

### Likelihood Explanation
Requires a borrower default that gets finalized with recovery (a supported, intentional flow), plus any post-default withdraw request — an unprivileged tranche-token holder requesting withdrawal through the CDO suffices; the CDO path is standard user behavior. No privileged attacker is needed: the sequencing is user-driven around honest borrower/manager calls. The reserve drain is deterministic arithmetic, not a race.

### Recommendation
Do not pay post-default claims from `defaultRecoveryReserve`. Either route `_claimPostDefaultWithdrawRequest` through `_transferFundedClaim` (post-default receipts are funded by post-default repayments, not recovery), or have the CDO's post-default funding path increment `defaultRecoveryReserve`/`pendingWithdraws` so post-default claims carry their own backing. Keep `_transferFundedClaim`'s `balance - reserve >= amount` guard semantics consistent with whichever bucket is used.

### Proof of Concept
Foundry fork, `IdleCreditVault` + `IdleCDOEpochVariant`:

```solidity
// test/foundry/PostDefaultReserveDrain.t.sol
function test_PostDefaultClaimsDrainRecoveryReserve() public {
    // 1. Epoch running (fixed-APR mode). Alice deposits via AA, requests withdraw (epoch N).
    _depositAA(alice, 100e6);
    _requestWithdrawViaCDO(alice, 100e6);        // withdrawsRequestsByEpoch[alice][N] = 100e6

    // 2. Borrower defaults; owner calls finalizeDefaultRecovery with partial recovery.
    //    reserveAmount = 40e6, totalBasis = 100e6 (pending) + active; recoveryPrice < 1.
    _borrowerDefault();
    vm.prank(cdo);
    strategy.finalizeDefaultRecovery(40e6, recoverySource);

    // 3. Post-default: Bob (unprivileged holder) requests withdraw via CDO.
    //    defaultRecoveryFinalized branch -> postDefaultRequests[bob] = bobAmount,
    //    pendingWithdraws untouched, reserve untouched.
    _depositAA(bob, 100e6);                       // post-default deposit
    _requestWithdrawViaCDO(bob, 50e6);

    // 4. Bob claims -> _transferDefaultRecovery pays 50e6 and decrements
    //    defaultRecoveryReserve from 40e6 -> underflow-or-drain.
    vm.prank(cdo);
    strategy.claimWithdrawRequest(bob);           // pays bob 1:1 from reserve budget

    // 5. Alice's defaulted claim now reverts on reserve underflow or is unpaid:
    vm.prank(cdo);
    vm.expectRevert();                            // defaultRecoveryReserve -= amount underflows
    strategy.claimWithdrawRequest(alice);
}
```

Assert additionally that `strategy.defaultRecoveryReserve()` decreased by Bob's post-default claim even though Bob's basis was never part of `totalBasis`, and that Alice's `withdrawsRequestsByEpoch[alice][defaultRecoveryEpoch]` remains nonzero but unclaimable — permanent freezing of unclaimed recovery.

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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L629-633)
```text
  function reserveDefaultRecovery(uint256 _amount) external {
    _onlyIdleCDO();
    if (defaultRecoveryFinalized) revert NotAllowed();
    defaultRecoveryReserve += _amount;
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L679-693)
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
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L760-766)
```text
  function _claimPostDefaultWithdrawRequest(address _user) internal returns (uint256 amount) {
    amount = postDefaultRequests[_user];
    if (amount == 0) return amount;
    postDefaultRequests[_user] = 0;
    // Post-default receipts are paid 1:1 because the haircut was applied when the request was made.
    _burn(_user, amount);
    _transferDefaultRecovery(_user, amount);
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
