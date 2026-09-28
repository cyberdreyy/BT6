### Title
Post-default withdraw requests pay 1:1 from the recovery reserve, draining funds earmarked for defaulted claimants - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
After `finalizeDefaultRecovery` sets `defaultRecoveryPrice` below par, `requestWithdraw` still lets any tranche holder mint a new "post-default" receipt and `claimWithdrawRequest` pays it at par via `_transferDefaultRecovery`. The reserve was only funded pro-rata (`recoveryPrice = reserveAmount / totalBasis`), so each post-default claim overdraws the reserve by `amount * (1 - recoveryPrice)`, leaving earlier defaulted-epoch receipt holders and remaining tranche holders unpayable. This mirrors the nova-lxd bug class: a security/accounting rule (the recovery haircut) is applied to the wrong identity bucket — post-default receipts are paid through the funded-claim path at par instead of being charged against the same recovery budget.

### Finding Description
In `finalizeDefaultRecovery` the recovery reserve is sized so that every unit of claim basis (active LPs + pending receipts) is backed by `defaultRecoveryPrice` units of underlying: [1](#0-0) 

After finalization, `requestWithdraw` takes a post-default branch that burns `_amount` of CDO-held strategy tokens, mints the user an equal receipt, and records it in `postDefaultRequests`: [2](#0-1) 

`claimWithdrawRequest` then routes to `_claimPostDefaultWithdrawRequest`, which pays the full `amount` out of `defaultRecoveryReserve` via `_transferDefaultRecovery`: [3](#0-2) [4](#0-3) 

The accounting mismatch: when a post-default requester burns `_amount` of active CDO backing, the claim basis they remove was funded in the reserve with only `_amount * defaultRecoveryPrice` underlying. Paying them `_amount` at par consumes `_amount * (1 - recoveryPrice)` of reserve that belongs to other claimants. The comment "paid 1:1 because the haircut was applied when the request was made" conflates the tranche-price haircut (which determines how much underlying the receipt represents) with the reserve haircut (which determines how much cash backs each unit of basis).

Concrete sequence (fixed-APR, defaulted/finalized phase):
1. Borrower defaults; owner/manager calls `finalizeDefault(_recoveredAmount, _recoverySource)` with partial recovery, e.g. `defaultRecoveryPrice = 0.5e18`.
2. `finalizeDefault` re-enables `allowAAWithdrawRequest`/`allowBBWithdrawRequest` and claims (`claimWithdrawRequest` has no epoch gating once `epochEndDate == 0`).
3. Attacker (a KYC'd tranche holder) calls `requestWithdraw(amount, AATranche)`. Post-haircut `virtualPrice` means e.g. tranches worth 100 pre-default now mint a 50-underlying receipt; but the CDO strategy-token burn removes 50 units of active basis while the reserve only carried `50 * 0.5 = 25` for them.
4. Attacker calls `claimWithdrawRequest()` and receives 50 underlying from `defaultRecoveryReserve`.
5. Repeating across post-default requesters drains the reserve; defaulted-epoch receipt holders calling `_claimDefaultedWithdrawRequest` later revert on `defaultRecoveryReserve -= _amount` / transfer — their funded recovery is permanently unpayable.

### Impact Explanation
Direct theft and permanent freezing of unclaimed recovery funds. Every post-default claim overdrawing the reserve by `(1 - recoveryPrice)` comes out of defaulted-epoch withdraw receipts and instant receipts that were explicitly included in `defaultPendingClaimBasis`. With `recoveryPrice = 0.5`, a post-default requester extracts twice their funded share; once enough post-default claims occur, remaining claimants' transactions revert (reserve underflow / insufficient balance), permanently freezing their recovery.

### Likelihood Explanation
Requires a borrower default with partial recovery — an expected, supported flow (`finalizeDefault`, `defaultRecoveryPrice`, post-default request path are all production code). The attacker needs only to be a wallet-allowed tranche holder after finalization; `finalizeDefault` explicitly re-enables withdraw requests and claims. No privileged or malicious action needed; first movers win the overdraw at the expense of later claimants. The `_transferFundedClaim` reserve guard (line 900-904) protects the reserve from *funded* claims, but post-default claims deliberately spend the reserve itself with no solvency check, so no existing guard stops it.

### Recommendation
Charge post-default claims against their funded share, not par. Either:
- Reduce `defaultRecoveryReserve` by the funded portion only: burn `_amount` of receipt but pay `_amount` (the haircut is already in `_amount`), while correspondingly recognizing that the burned active backing freed only `_amount * defaultRecoveryPrice` — i.e., the request should mint a receipt of `_amount * defaultRecoveryPrice`, or
- Track a separate `postDefaultReserve` funded at request time by debiting the active-side recovery budget (`activeBasis * recoveryPrice` proportional share) instead of the shared `defaultRecoveryReserve`, so post-default claims cannot consume defaulted-epoch claimants' allocations.

### Proof of Concept
Foundry fork PoC outline (based on `test/foundry/IdleCreditVault.t.sol` harness):

```solidity
function testPostDefaultClaimDrainsRecoveryReserve() public {
    // 1. Users deposit, epoch starts, borrower defaults
    _depositWithUser(userA, 100e6);        // AA
    _depositWithUser(attacker, 100e6);     // AA
    vm.prank(manager); cdoEpoch.startEpoch();

    // userA requests withdraw during buffer (becomes defaulted receipt)
    vm.prank(userA); cdoEpoch.requestWithdraw(0, AA); // full balance

    // borrower fails to repay at stopEpoch -> _handleBorrowerDefault
    _stopCurrentEpochExpectingDefault();

    // 2. manager finalizes default with 50% recovery
    uint256 recovered = /* half of totalBasis */;
    deal(address(underlying), recoverySource, recovered);
    vm.prank(recoverySource); underlying.approve(address(strategy), recovered);
    vm.prank(manager); cdoEpoch.finalizeDefault(recovered, recoverySource);
    assertLt(strategy.defaultRecoveryPrice(), 1e18); // haircut < 100%

    uint256 reserveBefore = strategy.defaultRecoveryReserve();

    // 3. attacker requests a post-default withdraw of all tranches
    vm.prank(attacker); cdoEpoch.requestWithdraw(0, AA);
    uint256 receipt = strategy.postDefaultRequests(attacker);
    assertGt(receipt, 0);

    // 4. attacker claims at par from the recovery reserve
    vm.prank(attacker); cdoEpoch.claimWithdrawRequest();
    assertEq(strategy.defaultRecoveryReserve(), reserveBefore - receipt);
    // overdraw = receipt * (1 - defaultRecoveryPrice) taken from defaulted claimants

    // 5. userA (or subsequent post-default requesters) can no longer claim
    vm.prank(userA);
    vm.expectRevert(); // reserve exhausted / underflow
    cdoEpoch.claimWithdrawRequest();
}
```

Note: the exact revert point depends on reserve sizing (finalization counts active basis in `totalBasis`, so the reserve is exhausted only after post-default claims consume the active-LP share); the invariant violation — reserve decreasing faster than `recoveryPrice`-weighted basis — is verifiable by asserting `defaultRecoveryReserve < pendingBasis * defaultRecoveryPrice / 1e18` after step 4 even when some claims still succeed.

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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L686-692)
```text
    uint256 reserveAmount = _recoveredAmount + prefundedReserve + defaultRecoveryReserve;
    // Recovery can be above par if the recovered funds exceed the computed basis.
    uint256 recoveryPrice = reserveAmount * RECOVERY_FULL / totalBasis;

    defaultRecoveryFinalized = true;
    defaultRecoveryReserve = reserveAmount;
    defaultRecoveryPrice = recoveryPrice;
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
