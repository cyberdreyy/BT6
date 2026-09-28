### Title
Stale `lastWithdrawRequest` index lets a loss-adjusted receipt escape its haircut and drain funded claims - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`IdleCreditVault` tracks a user's pending withdrawal per epoch (`withdrawsRequestsByEpoch`) but resolves loss-adjusted claims through a single scalar, `lastWithdrawRequest[_user]`. When a borrower stop funds pending receipts at a loss, `collectWithdrawFunds` records a haircut in `lossRecoveryPriceByEpoch[epochNumber]`. If the user makes a second `requestWithdraw` in a later epoch before claiming, `lastWithdrawRequest` is overwritten, the loss-epoch lookup returns 0, and the entire aggregate `withdrawsRequests[_user]` (including the haircutted receipt) is paid at par via `_claimFundedWithdrawRequest`. The vault only ever received `pendingBasis - pendingLoss`, so the overpayment is taken from funds reserved for other claimants.

This mirrors CVE-2017-5550: an incorrect "release" of a bookkeeping slot (here the single-epoch pointer being overwritten) causes a later read to consume stale/wrong-index data, releasing more value than was funded.

### Finding Description
In `requestWithdraw`, the request epoch is recorded unconditionally: [1](#0-0) 

Loss funding is recorded per epoch: [2](#0-1) 

But the loss-adjusted claim path only inspects `lastWithdrawRequest[_user]` — the *latest* request epoch: [3](#0-2) 

If `lossRecoveryPriceByEpoch[lastWithdrawRequest[_user]] == 0` (because the newest request is in a clean epoch), the function returns 0 and the claim falls through to `_claimFundedWithdrawRequest`, which pays the full aggregate `withdrawsRequests[_user]` at par: [4](#0-3) 

The comment at lines 323-324 explicitly acknowledges that stacking requests is permitted ("if a user does not claim a withdraw request and instead requests another withdraw, he will have to wait for another epoch to claim both requests"), so there is no guard preventing the overwrite. `_clearWithdrawClaimForEpoch` only clears the specific epoch it is called with, so the haircutted epoch's basis remains in `withdrawsRequests[_user]` and is paid at 100%.

### Impact Explanation
Direct theft / insolvency. The borrower funded only `pendingBasis - pendingLoss` for the loss epoch. The attacker claims `claimBasis_lossEpoch` at par instead of `claimBasis_lossEpoch * lossRecoveryPrice / RECOVERY_FULL`, extracting `claimBasis_lossEpoch * (RECOVERY_FULL - lossRecoveryPrice) / RECOVERY_FULL` more than funded. That excess is paid out of the strategy's underlying balance, which backs other users' funded receipts and the default-recovery reserve; the last claimants are left underfunded (permanent loss, quantified by the attacker's share of `pendingLoss`).

### Likelihood Explanation
Requires a `stopEpochWithDuration`-style partial funding (borrower shortfall on pending withdrawals), which is a designed code path, not an edge case. The attacker is an ordinary KYC'd tranche holder who simply: requests a withdraw in the epoch that later realizes a loss, requests again in the next epoch, then claims once `epochNumber > lastWithdrawRequest`. No privileged cooperation is needed — borrower/manager only execute ordinary stopEpoch calls. The only mitigation is that a loss event must occur in the epoch of the attacker's first request, which the attacker cannot force but can opportunistically exploit (any user with a pending receipt in a loss epoch qualifies, including via a second small dust request made by anyone holding such a receipt).

### Recommendation
Do not key loss recovery off the mutable `lastWithdrawRequest`. Either iterate/search `withdrawsRequestsByEpoch` for any epoch with a non-zero `lossRecoveryPriceByEpoch` (or track a per-user list of loss epochs), or revert/`NotAllowed` in `requestWithdraw` when the user has an unclaimed receipt in an epoch with `lossRecoveryPriceByEpoch[epoch] != 0` until it is claimed. Alternatively, settle the loss-adjusted portion at request time inside `requestWithdraw` before recording the new epoch.

### Proof of Concept
Foundry fork PoC (sketch, in the style of `test/foundry/IdleCreditVault.t.sol`):

```solidity
// Setup: standard epoch variant, non-zero APR so normal (non-APR0) flow is used.
_idleCDOSetup(); // deposit via queue/direct, startEpoch, etc.

// Epoch N: attacker deposits and requests withdraw during running epoch.
address attacker = makeAddr('attacker');
uint256 tranches = _depositWithUser(attacker, 100_000 * ONE_SCALE);
cdoEpoch.requestWithdraw(tranches, address(AAtranche)); // lastWithdrawRequest = N

// stopEpoch with a realized loss on pending receipts:
// borrower repays less than pendingWithdraws -> collectWithdrawFunds sets
// lossRecoveryPriceByEpoch[N] = funded * RECOVERY_FULL / pendingBasis < RECOVERY_FULL
uint256 pending = creditVault.pendingWithdraws();
deal(defaultUnderlying, borrower, expectedFunds - pending / 2);
vm.warp(cdoEpoch.epochEndDate() + 1);
vm.prank(manager);
cdoEpoch.stopEpochWithDuration(apr, lossDuration /* triggers partial funding */);

// Epoch N+1 buffer: attacker makes a second (dust) request, overwriting
// lastWithdrawRequest to N+1 where lossRecoveryPriceByEpoch[N+1] == 0.
uint256 dustTranches = _depositWithUser(attacker, 1 * ONE_SCALE);
cdoEpoch.requestWithdraw(dustTranches, address(AAtranche));

// Run epoch N+1 to completion so both receipts are claimable.
_startEpochAndCheckPrices(0);
deal(defaultUnderlying, borrower, _expectedFundsEndEpoch());
vm.warp(cdoEpoch.epochEndDate() + 1);
vm.prank(manager);
cdoEpoch.stopEpoch(apr, 0); // funds epoch N+1 receipts fully

// Attacker claims: _claimLossAdjustedWithdrawRequest looks up epoch N+1 (no
// haircut) and _claimFundedWithdrawRequest pays the FULL aggregate at par,
// including the epoch-N receipt that was only partially funded.
uint256 balPre = underlying.balanceOf(attacker);
vm.prank(attacker);
cdoEpoch.claimWithdrawRequest();
uint256 got = underlying.balanceOf(attacker) - balPre;

uint256 lossEpochBasis = /* epoch-N request amount */;
uint256 expected = lossEpochBasis * creditVault.lossRecoveryPriceByEpoch(N) / RECOVERY_FULL
                 + /* epoch-N+1 dust amount */;
assertGt(got, expected, 'loss haircut escaped via stale lastWithdrawRequest');
// The delta `got - expected` is paid from the funded pool reserved for other claimants.
```

Key assertion: `got` exceeds the haircut-adjusted entitlement by exactly the attacker's share of `pendingLoss`, demonstrating insolvency of the funded-claim pool rather than a mere accounting glitch.

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L281-294)
```text
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
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L338-349)
```text
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
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L411-429)
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
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L789-800)
```text
  function _claimLossAdjustedWithdrawRequest(address _user) internal returns (uint256 amount) {
    uint256 lossEpoch = lastWithdrawRequest[_user];
    uint256 lossRecoveryPrice = lossRecoveryPriceByEpoch[lossEpoch];
    if (lossRecoveryPrice == 0) return amount;

    (uint256 claimBasis, uint256 burnAmount) = _clearWithdrawClaimForEpoch(_user, lossEpoch, false);
    if (claimBasis == 0) return amount;

    // pendingWithdraws was already cleared when the borrower funded the loss-adjusted amount.
    _burn(_user, burnAmount);
    amount = (claimBasis * lossRecoveryPrice) / RECOVERY_FULL;
    _transferFundedClaim(_user, amount);
```
