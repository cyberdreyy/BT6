### Title
Post-default instant-withdraw requests drain the fixed default-recovery reserve - (contracts/strategies/idle/IdleCreditVault.sol)

### Summary
The external bug class is a validation crash caused by overlapping-but-inconsistent coverage (two proof records, only one signed). The credit-vault analog is `requestInstantWithdraw`: unlike `requestWithdraw`, which has an explicit `defaultRecoveryFinalized` branch routing new requests to the isolated `postDefaultRequests` bucket [1](#0-0) , `requestInstantWithdraw` has no post-default handling at all [2](#0-1) . A receipt created after default finalization lands in `instantWithdrawsRequestsByEpoch[user][epochNumber]` where `epochNumber == defaultRecoveryEpoch`, and is then paid out of the pre-sized `defaultRecoveryReserve` at `defaultRecoveryPrice` — diluting/stealing the haircut recovery meant for pre-default claimants.

### Finding Description
After `finalizeDefaultRecovery` sets `defaultRecoveryFinalized = true`, `defaultRecoveryPrice`, `defaultRecoveryReserve`, and `defaultRecoveryEpoch = epochNumber` [3](#0-2) . The reserve is sized exactly as `recovered + prefundedInstant + existing reserve` divided by the pre-default claim basis (`pendingWithdraws` plus the default-epoch instant claims) [4](#0-3) .

`requestInstantWithdraw` unconditionally burns the CDO's strategy tokens, mints a 1:1 receipt to the user, and records `instantWithdrawsRequestsByEpoch[_user][epochNumber] += _amount` and `instantWithdrawClaimsByEpoch[epochNumber] += _amount` [5](#0-4) . Since `epochNumber` is not advanced after default, the new receipt is filed under `defaultRecoveryEpoch` — the same bucket legitimate defaulted claimants use.

When the user later calls `claimInstantWithdrawRequest`, the `defaultRecoveryFinalized && defaultInstantWithdrawsFinalized` path invokes `_claimDefaultedInstantWithdrawRequest`, which reads `instantWithdrawsRequestsByEpoch[user][defaultEpoch]` and pays `(claimBasis * defaultRecoveryPrice) / RECOVERY_FULL` via `_transferDefaultRecovery`, which decrements the fixed `defaultRecoveryReserve` [6](#0-5) [7](#0-6) . No corresponding funds were added to the reserve when the post-default receipt was created — the two ledgers covering the same epoch are inconsistently backed, exactly the "covered by both records but signed/funded for only one" shape of the BIND bug.

### Impact Explanation
An attacker (any KYC-passing lender or strategy-token holder who acquires exposure before or after default) opens an instant-withdraw request post-finalization. Their burned strategy tokens are 1:1 minted back as a receipt, so the request costs them only the opportunity value of the haircut. On claim they withdraw `amount * defaultRecoveryPrice` from the isolated reserve, directly reducing what legitimate defaulted-epoch claimants can recover. Since `defaultRecoveryReserve` is fixed, every attacker claim is pro-rata theft from other claimants, and late honest claimants can be left with nothing (claim reverts on insufficient reserve). This is direct theft of recovery proceeds, quantified as `attackerReceipt * defaultRecoveryPrice / RECOVERY_FULL`.

A secondary path: if `defaultInstantWithdrawsFinalized` is false (instant queue fully funded pre-default), the new receipt inflates `instantWithdrawsRequests[_user]` and is paid at par via `_transferFundedClaim`, which is guarded by the reserve check — but the `_claimDefaultedInstantWithdrawRequest` path described above bypasses that guard entirely.

### Likelihood Explanation
Requires the pool to be defaulted and finalized — a real but infrequent state — and `pendingInstantWithdraws != 0` at finalization so `defaultInstantWithdrawsFinalized` is set (partially unfunded instant queue, a common mid-epoch condition). The attacker needs any strategy-token balance to burn; post-default `requestWithdraw` explicitly remains reachable for users, and the instant path has no equivalent gating or check (`_hasWithdrawRequest`/post-default branch absent). The only uncertainty is whether `IdleCDOEpochVariant.requestInstantWithdraw` independently reverts when `defaulted()` — if it does not, this is fully exploitable; the strategy code clearly assumes such requests may arrive since it guards nothing.

### Recommendation
Add a `defaultRecoveryFinalized` branch to `requestInstantWithdraw` mirroring `requestWithdraw`: either revert, or route the receipt to `postDefaultRequests`-style accounting so it is paid 1:1 only when explicitly backed — never into `instantWithdrawsRequestsByEpoch[user][defaultRecoveryEpoch]`. Alternatively, tag receipts with the epoch at request time and require `epochNumber != defaultRecoveryEpoch` before writing into `instantWithdrawClaimsByEpoch`.

### Proof of Concept
Foundry fork test (extends `test/foundry/IdleCreditVault.t.sol` helpers):

```solidity
function testPostDefaultInstantRequestDrainsReserve() external {
    // Setup: deposits, epoch running, user1 has a normal withdraw request pending
    _requestWithdrawWithUser(user1, tranches1);
    // Borrower underpays at epoch end -> default
    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.prank(owner); cdoEpoch.stopEpoch(0, 0);
    assertTrue(cdoEpoch.defaulted());
    // Manager finalizes with partial recovery R < basis
    vm.startPrank(manager);
    underlying.approve(address(strategy), recovered);
    cdoEpoch.finalizeDefault(recovered, manager);
    vm.stopPrank();

    uint256 reservePre = strategy.defaultRecoveryReserve();

    // Attacker acquires/holds tranche tokens, requests instant withdraw AFTER finalization
    deal(address(strategy), address(cdoEpoch), attackerAmt, true); // CDO holds strategy tokens
    vm.prank(attacker);
    cdoEpoch.requestInstantWithdraw(attackerAmt); // reaches strategy.requestInstantWithdraw

    // Receipt was filed under defaultRecoveryEpoch
    assertEq(strategy.instantWithdrawsRequestsByEpoch(attacker, strategy.defaultRecoveryEpoch()), attackerAmt);

    // Attacker claims defaulted-epoch recovery immediately
    uint256 balPre = underlying.balanceOf(attacker);
    vm.prank(attacker);
    cdoEpoch.claimInstantWithdrawRequest();
    uint256 stolen = underlying.balanceOf(attacker) - balPre;

    assertGt(stolen, 0);
    assertEq(strategy.defaultRecoveryReserve(), reservePre - stolen); // reserve shrunk by unpaid-for claim
    // Legitimate claimant's entitlement is now undercollateralized by `stolen`
}
```

The key assertions that pin the bug: the post-default receipt lands in the `defaultRecoveryEpoch` bucket, and `defaultRecoveryReserve` decreases without any matching reserve contribution at request time.

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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L356-375)
```text
  function requestInstantWithdraw(uint256 _amount, address _user) external {
    _onlyIdleCDO();
    _ensureDefaultRecoveryInitialized();
    // burn strategy tokens from cdo
    _burn(msg.sender, _amount);
  
    // mint equal amount of strategy tokens to the user as receipt, useful in case of default
    _mint(_user, _amount);

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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L644-649)
```text
  function defaultPendingClaimBasis() public view returns (uint256 basis) {
    basis = pendingWithdraws;
    if (pendingInstantWithdraws != 0) {
      basis += instantWithdrawClaimsByEpoch[epochNumber];
    }
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L690-696)
```text
    defaultRecoveryFinalized = true;
    defaultRecoveryReserve = reserveAmount;
    defaultRecoveryPrice = recoveryPrice;
    defaultRecoveryEpoch = epochNumber;
    // A non-zero pending instant bucket means current-epoch instant receipts were not fully funded
    // and must be paid through the same recovery ratio as normal pending receipts.
    defaultInstantWithdrawsFinalized = pendingInstantWithdraws != 0;
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
