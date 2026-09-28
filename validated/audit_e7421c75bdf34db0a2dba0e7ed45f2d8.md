### Title
Funded instant-withdraw claims never clear `instantWithdrawsRequestsByEpoch`/`instantWithdrawClaimsByEpoch`, corrupting default-epoch claim accounting and freezing later receipts - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`IdleCreditVault.claimInstantWithdrawRequest` pays out a funded instant withdrawal and resets only the aggregate `instantWithdrawsRequests[_user]`, but leaves the per-epoch markers `instantWithdrawsRequestsByEpoch[_user][epoch]` and `instantWithdrawClaimsByEpoch[epoch]` untouched. Those per-epoch counters exist specifically so default finalization can "distinguish default-epoch pending instant receipts from old funded instant receipts" (comment at lines 367-371). Because already-paid receipts remain recorded, `_claimDefaultedInstantWithdrawRequest` later reads an inflated `claimBasis` for the defaulted epoch: the subtraction `instantWithdrawsRequests[_user] -= claimBasis` underflows and reverts, permanently freezing any real pending/post-default receipt of that user, while `instantWithdrawClaimsByEpoch[defaultEpoch]` and `pendingInstantWithdraws` are corrupted for all other claimants. This is the direct analog of the reported bug class: a withdrawal path that pays out but fails to zero the accounting that a later distribution pass relies on.

### Finding Description
In `claimInstantWithdrawRequest` (lines 380-393), after `_claimDefaultedInstantWithdrawRequest` optionally runs, the function burns `instantWithdrawsRequests[_user]`, sets it to 0 and transfers funds — but never clears `instantWithdrawsRequestsByEpoch[_user][epochNumber]` or decrements `instantWithdrawClaimsByEpoch[epochNumber]`/`pendingInstantWithdraws` for the paid amount (funding is sourced via `collectInstantWithdrawFunds`, which only reduces `pendingInstantWithdraws` at lines 398-403).

Per-epoch entries are only cleared inside `_claimDefaultedInstantWithdrawRequest` (lines 842-856), which computes `claimBasis = instantWithdrawsRequestsByEpoch[_user][defaultEpoch]`, then executes `instantWithdrawsRequests[_user] -= claimBasis` and `instantWithdrawClaimsByEpoch[defaultEpoch] -= claimBasis`.

Attack sequence (unprivileged KYC lender, running epoch N):
1. User calls `requestInstantWithdraw(X)` — receipt minted, `instantWithdrawsRequestsByEpoch[user][N] += X`, `instantWithdrawClaimsByEpoch[N] += X`.
2. Liquidity is available → `claimInstantWithdrawRequest` pays X, sets `instantWithdrawsRequests[user] = 0`. Per-epoch entries still hold X.
3. User calls `requestInstantWithdraw(Y)` in the same epoch — `instantWithdrawsRequests[user] = Y`, `byEpoch[N] = X + Y`, `claimsByEpoch[N] = X + Y`.
4. Epoch N ends in borrower default; finalization sets `defaultRecoveryEpoch = N` and `defaultInstantWithdrawsFinalized`.
5. User calls `claimInstantWithdrawRequest`: `claimBasis = X + Y`, then `instantWithdrawsRequests[user] -= claimBasis` computes `Y - (X + Y)` → underflow → revert. Every subsequent call reverts identically.

For comparison, the normal-withdraw funded claim (`_claimFundedWithdrawRequest`, lines 319-350) similarly leaves `withdrawsRequestsByEpoch` stale, but normal requests are only claimable after their epoch ends, so a claimed epoch index can never equal `defaultRecoveryEpoch`; the instant path is uniquely exposed because receipts are recorded and paid within the same epoch index that finalization later uses.

### Impact Explanation
- Permanent freezing: the affected user's legitimate pending receipt Y can never be claimed — `claimInstantWithdrawRequest` reverts on the underflow on every call, including after they open a `postDefaultRequests` receipt (which routes through `claimWithdrawRequest`, unaffected, but any instant receipt is bricked). The Y underlyings remain locked in the vault/reserve.
- Mispriced recovery for everyone: `instantWithdrawClaimsByEpoch[N]` retains the already-paid X, so default finalization treats paid receipts as unfunded default-epoch claims. This inflates the defaulted-epoch claim basis, diluting `defaultRecoveryPrice`/reserve allocation for genuinely pending claimants and corrupting `pendingInstantWithdraws` (the `claimBasis >= pending ? 0 : pending - claimBasis` clamp can zero it while real pending claims exist). Loss is quantified as the full frozen amount Y plus the pro-rata dilution of the defaulted-epoch recovery pool proportional to X.

### Likelihood Explanation
Requires only unprivileged actions: two instant-withdraw requests with a funded claim in between, all inside one running epoch, followed by a borrower default. Instant claims are specifically designed to succeed when new deposits provide liquidity mid-epoch, so steps 1-3 are ordinary usage, and defaults are a supported protocol state (`finalizeDefault`, `defaultRecoveryFinalized`). No privileged misbehavior, oracle manipulation, or timing edge is needed; the missing reset makes the revert deterministic once a default touches that epoch.

### Recommendation
Mirror the reset discipline used elsewhere (e.g., `userWithdrawalsEpochs` zeroing in `IdleCDOEpochQueue.claimWithdrawRequest`): in `claimInstantWithdrawRequest`, after computing `amount`, clear `instantWithdrawsRequestsByEpoch[_user][epochNumber]` and decrement `instantWithdrawClaimsByEpoch[epochNumber]` by the claimed amount so funded claims cannot be re-counted as pending at default finalization. Equivalent per-epoch cleanup should be applied in `_claimFundedWithdrawRequest` for `withdrawsRequestsByEpoch` to keep the "one receipt one payout" invariant consistent across paths.

### Proof of Concept
```solidity
// test/foundry/IdleCreditVaultStaleInstantClaim.t.sol
function testStaleInstantClaimBlocksDefaultClaim() external {
    uint256 amountWei = 10_000 * ONE_SCALE;
    _depositWithUser(user, amountWei, false);          // KYC'd lender deposits AA
    _startEpochAndCheckPrices(0);                      // epoch N running

    // 1) instant request X, claimed while liquidity exists
    vm.prank(user);
    uint256 x = cdoEpoch.requestWithdraw(0, address(AAtranche)); // instant mode
    vm.prank(user);
    cdoEpoch.claimInstantWithdrawRequest();            // paid; byEpoch[N] stays = X
    assertEq(strategy.instantWithdrawsRequests(user), 0);
    assertEq(strategy.instantWithdrawsRequestsByEpoch(user, N), x); // stale

    // 2) second instant request Y in same epoch, still pending
    _depositWithUser(user, amountWei, false);
    vm.prank(user);
    uint256 y = cdoEpoch.requestWithdraw(0, address(AAtranche));
    assertEq(strategy.instantWithdrawsRequestsByEpoch(user, N), x + y);

    // 3) epoch N defaults; owner finalizes with partial recovery
    _stopEpochAndCheckPrices(0, 0, 0);
    _finalizeDefault(recovered);                        // defaultRecoveryEpoch = N

    // 4) user can never claim Y: underflow on instantWithdrawsRequests -= (X+Y)
    vm.prank(user);
    vm.expectRevert();                                  // arithmetic underflow
    cdoEpoch.claimInstantWithdrawRequest();

    // also: instantWithdrawClaimsByEpoch[N] still counts the already-paid X,
    // inflating the default-epoch basis used for recovery distribution.
    assertEq(strategy.instantWithdrawClaimsByEpoch(N), x + y);
}
```

Caveat: I could not fully read `finalizeDefault`/`_handleBorrowerDefault` in this pass, so the exact statement assigning `defaultRecoveryEpoch` should be confirmed against the epoch index under which instant receipts are recorded; the stale-never-cleared writes at lines 372, 391 and the underflowing subtraction at line 848 are verified directly in `IdleCreditVault.sol`. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L366-375)
```text
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
