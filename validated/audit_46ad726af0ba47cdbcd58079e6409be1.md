### Title
Unfunded instant-withdraw receipts from earlier epochs are paid at par instead of the default recovery ratio — (`contracts/strategies/idle/IdleCreditVault.sol`)

### Summary
`IdleCreditVault` records instant-withdraw receipts both in an aggregate (`instantWithdrawsRequests`) and per epoch (`instantWithdrawsRequestsByEpoch`), mirroring the CVE-2024-28054 "multiple conflicting representations of the same object" bug class. At default finalization, `defaultPendingClaimBasis()` only treats `instantWithdrawClaimsByEpoch[epochNumber]` (the *current* epoch's instant receipts) as defaulted claims, implicitly assuming `pendingInstantWithdraws` belongs to a single epoch. If an attacker carries an unfunded instant receipt from an earlier epoch into the default epoch, finalization neither haircuts it nor reserves funds for it, yet `claimInstantWithdrawRequest` pays the non-default-epoch remainder at par through `_transferFundedClaim`. The earlier receipt is overpaid, draining recovery backing owed to other claimants. [1](#0-0) [2](#0-1) [3](#0-2) 

### Finding Description
- `requestInstantWithdraw` records the receipt under the *current* `epochNumber` in `instantWithdrawsRequestsByEpoch[user][epoch]` and `instantWithdrawClaimsByEpoch[epoch]`, while `pendingInstantWithdraws` is a single global unfunded remainder with no epoch attribution. [4](#0-3) 
- `defaultPendingClaimBasis()` adds only `instantWithdrawClaimsByEpoch[epochNumber]` when `pendingInstantWithdraws != 0`; `_defaultPrefundedInstantReserve()` also compares `pendingInstantWithdraws` only against the current epoch's basis. Any unfunded instant receipt recorded under an earlier epoch is excluded from both the haircut basis and the prefunded-reserve computation. [1](#0-0) [5](#0-4) 
- On claim, `_claimDefaultedInstantWithdrawRequest` clears only `instantWithdrawsRequestsByEpoch[user][defaultRecoveryEpoch]` and pays it at `defaultRecoveryPrice`. The remaining `instantWithdrawsRequests[user]` — which still contains the earlier epoch's unfunded amount `a` — is then paid at par via `_transferFundedClaim(user, amount)` in `claimInstantWithdrawRequest`. [2](#0-1) [6](#0-5) 

Reachability (attacker is a KYC'd lender only; all privileged calls are by the honest manager):
1. Epoch E buffer: attacker calls `cdoEpoch.requestWithdraw(0, tranche)`; the APR delta routes it to the instant bucket → `instantWithdrawsRequestsByEpoch[attacker][E] = a`, `pendingInstantWithdraws += a`.
2. `startEpoch`: CDO cash is less than `pendingInstant`, so `collectInstantWithdrawFunds` partially covers it and returns early without enabling `allowInstantWithdraw` (lines 279–290).
3. During epoch E the manager never calls `getInstantWithdrawFunds` (it is optional; the attacker cannot force it, but nothing requires it to succeed for the pool to operate). `stopEpoch` repays normally; `a` stays in `pendingInstantWithdraws`.
4. Epoch E+1 buffer: attacker makes a second instant request → `instantWithdrawsRequestsByEpoch[attacker][E+1] = b`, `pendingInstantWithdraws = a + b - funded`. (The existing test at `test/foundry/IdleCreditVault.t.sol:4485` confirms one user can hold instant receipts in two different epochs simultaneously.)
5. `startEpoch` again under-covers `pendingInstant`; manager calls `getInstantWithdrawFunds`, the borrower fails to send → `_handleBorrowerDefault` with `pendingInstantWithdraws != 0`.
6. `finalizeDefault`: `defaultPendingClaimBasis` = `pendingWithdraws + instantWithdrawClaimsByEpoch[E+1]` = `b` only; `a` is not in the recovery basis and not in `prefundedReserve` (which is `max(0, b - pendingInstant)`). `defaultInstantWithdrawsFinalized` is set true.
7. Attacker calls `claimInstantWithdrawRequest`: gets `b * defaultRecoveryPrice` from the reserve, then `a` **at par** via `_transferFundedClaim`, even though `a` was never funded and never haircut. [7](#0-6) [8](#0-7) [9](#0-8) 

### Impact Explanation
The recovery price is computed as `reserveAmount / totalBasis` where `totalBasis` excludes `a`. The attacker extracts `a` at 100% plus `b` at the recovery ratio, i.e. `(1 - recoveryPrice) * a` more than their fair share. Since the strategy's claimable balance was sized only for haircutted claims plus genuinely funded receipts, this overpayment is paid out of funds backing other claimants (funded instant receipts, post-default requests, or the recovery reserve), leaving the last claimants unpayable — direct theft plus partial insolvency of the recovery reserve, proportional to the attacker's earlier-epoch receipt size. Caveat: I could not fully read `_transferFundedClaim` (only lines 897–900 were indexed); if it rejects when payment would dip into `defaultRecoveryReserve` and insufficient non-reserve balance exists, the same accounting gap manifests as permanent freezing of the attacker's *and* subsequent claimants' payouts instead of theft — still a valid impact. [10](#0-9) [11](#0-10) 

### Likelihood Explanation
Requires: instant withdrawals enabled (`instantWithdrawAprDelta` set), CDO cash at `startEpoch` below `pendingInstant` (common when most TVL is lent out), and a borrower default at `getInstantWithdrawFunds` or `stopEpoch` while a multi-epoch instant backlog exists. All attacker actions are unprivileged `requestWithdraw`/`claimInstantWithdrawRequest` calls; no guard in `requestInstantWithdraw` blocks a second request while an earlier unfunded one exists (unlike the `requestWithdraw` loss-epoch guard at lines 263–271). Moderate likelihood. [12](#0-11) 

### Recommendation
Make the two representations agree: either include *all* epochs' instant receipts in `defaultPendingClaimBasis` (e.g. track unfunded basis globally rather than only `instantWithdrawClaimsByEpoch[epochNumber]`), or in `claimInstantWithdrawRequest` haircut every per-epoch entry that was unfunded at finalization rather than only `defaultRecoveryEpoch`. Alternatively, block new instant requests while the user (or the pool) has any unfunded instant receipt outstanding, mirroring the `lastWithdrawRequest` guard in `requestWithdraw`.

### Proof of Concept
Foundry fork PoC sketch (extend `test/foundry/IdleCreditVault.t.sol` harness):

```solidity
function testUnfundedPriorEpochInstantPaidAtPar() external {
    uint256 amount = 20_000 * ONE_SCALE;
    vm.prank(manager);
    cdoEpoch.setInstantWithdrawParams(cdoEpoch.instantWithdrawDelay(), 1000, false);

    _depositWithUser(attacker, amount, true);
    idleCDO.depositAA(amount); // second LP to consume recovery

    _startEpochAndCheckPrices(0);
    _stopEpochAndCheckPrices(0, initialProvidedApr / 2, _expectedFundsEndEpoch());

    // Epoch-1 instant request 'a' (buffer of epoch 1)
    vm.prank(attacker);
    uint256 a = cdoEpoch.requestWithdraw(0, address(AAtranche));
    _startEpochAndCheckPrices(1); // startEpoch partially prefunds; allowInstantWithdraw stays false
    // manager never calls getInstantWithdrawFunds during epoch 1 -> 'a' stays unfunded
    _stopEpochAndCheckPrices(1, initialProvidedApr / 4, _expectedFundsEndEpoch());

    // Epoch-2 instant request 'b'
    vm.prank(attacker);
    uint256 b = cdoEpoch.requestWithdraw(0, address(AAtranche));
    _startEpochAndCheckPrices(2);

    vm.warp(block.timestamp + cdoEpoch.instantWithdrawDelay() + 1);
    deal(defaultUnderlying, borrower, 0); // borrower fails -> default
    vm.prank(manager);
    cdoEpoch.getInstantWithdrawFunds();
    assertTrue(cdoEpoch.defaulted());

    // Finalize with recoveryRatio < 1
    uint256 recovered = /* (activeBasis + b) * ratio, minus prefunded */;
    deal(defaultUnderlying, manager, recovered);
    vm.prank(manager);
    underlying.approve(address(strategy), recovered);
    cdoEpoch.finalizeDefault(recovered, manager);

    uint256 balPre = underlying.balanceOf(attacker);
    vm.prank(attacker);
    cdoEpoch.claimInstantWithdrawRequest();
    // BUG: attacker receives a + b*ratio; expected is (a+b)*ratio
    assertGt(underlying.balanceOf(attacker) - balPre, (a + b) * ratio / 1e18);
}
```

Note: the exact lines of `_transferFundedClaim` beyond line 900 and `finalizeDefault`'s enabling of `allowInstantWithdraw` were not fully indexed, so the PoC's precise payout path (par payout vs. revert/freezing) should be confirmed against the full source; the accounting gap in `defaultPendingClaimBasis`/`_claimDefaultedInstantWithdrawRequest` itself is confirmed by the code and existing tests.

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L263-271)
```text
    if (
      lossRecoveryPrice != 0 &&
      (withdrawsRequestsByEpoch[_user][lossEpoch] != 0 ||
      (apr0Users[_user].principal != 0 && apr0Users[_user].principalEpoch == lossEpoch))
    ) {
      // A loss-adjusted receipt must be claimed before opening a later request, otherwise
      // `lastWithdrawRequest` would stop pointing to the epoch that stores its haircut.
      revert NotAllowed();
    }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L365-375)
```text
    // increase the instant withdraw requests for the user
    instantWithdrawsRequests[_user] += _amount;
    uint256 currentEpoch = epochNumber;
    // we record both per-user (old, kept for compatibility) and per-epoch so on
    // finalization we can distinguish "default-epoch pending instant receipts"
    // from old funded instant receipts.
    instantWithdrawsRequestsByEpoch[_user][currentEpoch] += _amount;
    instantWithdrawClaimsByEpoch[currentEpoch] += _amount;
    // increase the total instant withdraw requests
    pendingInstantWithdraws += _amount;
  }
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L644-649)
```text
  function defaultPendingClaimBasis() public view returns (uint256 basis) {
    basis = pendingWithdraws;
    if (pendingInstantWithdraws != 0) {
      basis += instantWithdrawClaimsByEpoch[epochNumber];
    }
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L685-700)
```text
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
    defaultBBNav = defaultBBNav * recoveryPrice / RECOVERY_FULL;
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L716-723)
```text
  function _defaultPrefundedInstantReserve() internal view returns (uint256 prefundedReserve) {
    uint256 pendingInstant = pendingInstantWithdraws;
    if (pendingInstant == 0) return prefundedReserve;
    uint256 instantBasis = instantWithdrawClaimsByEpoch[epochNumber];
    if (instantBasis > pendingInstant) {
      prefundedReserve = instantBasis - pendingInstant;
    }
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L897-900)
```text
  function _transferFundedClaim(address _user, uint256 _amount) internal {
    if (_amount == 0) return;
    uint256 reserve = defaultRecoveryReserve;
    if (reserve != 0) {
```

**File:** contracts/IdleCDOEpochVariant.sol (L279-294)
```text
    uint256 pendingInstant = _pendingInstant();
    uint256 totUnderlyings = _contractTokenBalance(token);
    uint256 _pendingWithdraws = _strategy.pendingWithdraws();
    _strategy.collectInstantWithdrawFunds(pendingInstant > totUnderlyings ? totUnderlyings : pendingInstant);

    // if there are more requests than the current underlyings we simply send all underlyings
    // to the IdleCreditVault contract
    if (pendingInstant > totUnderlyings) {
      // if borrower is programmable, notify epoch start even if no funds were sent
      _startEpochProgrammableBorrower(_pendingWithdraws);
      return;
    }
    // allow instant withdraws right away without waiting for the deadline
    allowInstantWithdraw = true;
    // and transfer the surplus to the borrower
    uint256 _toBorrower = totUnderlyings - pendingInstant;
```

**File:** contracts/IdleCDOEpochVariant.sol (L558-573)
```text
  function getInstantWithdrawFunds() external {
    _checkOnlyOwnerOrManager();
    // Check that programmable mode is disabled, the epoch is running and the deadline passed.
    _checkNotAllowed(isProgrammableBorrower || !isEpochRunning || block.timestamp < instantWithdrawDeadline);

    IdleCreditVault _strategy = IdleCreditVault(strategy);
    uint256 _instantWithdraws = _pendingInstant();
    // transfer funds for instant withdraw to this contract
    try this.getFundsFromBorrower(_instantWithdraws) {
      // transfer funds to IdleCreditVault and decrease pendingInstantWithdraws
      _strategy.collectInstantWithdrawFunds(_instantWithdraws);
      // allow instant withdraws
      allowInstantWithdraw = true;
    } catch {
      _handleBorrowerDefault(_instantWithdraws);
    }
```

**File:** test/foundry/IdleCreditVault.t.sol (L4500-4524)
```text

    uint256 userTrancheBal = IERC20Detailed(address(AAtranche)).balanceOf(instantUser);
    vm.prank(instantUser);
    claimData[0] = cdoEpoch.requestWithdraw(userTrancheBal / 2, address(AAtranche));

    _startEpochAndCheckPrices(1);
    vm.warp(block.timestamp + cdoEpoch.instantWithdrawDelay() + 1);
    _getInstantFunds();

    _stopEpochAndCheckPrices(1, initialProvidedApr / 4, _expectedFundsEndEpoch());

    vm.prank(instantUser);
    claimData[1] = cdoEpoch.requestWithdraw(0, address(AAtranche));

    IdleCreditVault creditVault = IdleCreditVault(address(strategy));
    _startEpochAndCheckPrices(2);
    uint256 pendingInstant = creditVault.pendingInstantWithdraws();
    assertGt(pendingInstant, 0, 'default instant request should remain unfunded');
    claimData[2] = claimData[1] - pendingInstant;

    vm.warp(block.timestamp + cdoEpoch.instantWithdrawDelay() + 1);
    vm.prank(manager);
    cdoEpoch.getInstantWithdrawFunds();
    _checkDefault();
    assertEq(cdoEpoch.allowInstantWithdraw(), false, 'mixed funded and unfunded instant claims should stay frozen');
```
