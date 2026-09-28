### Title
Already-claimed instant-withdraw receipts are never cleared from `instantWithdrawsRequestsByEpoch`, so a same-epoch borrower default double-counts paid-out basis in the recovery accounting - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary

`requestInstantWithdraw` records the receipt basis both in the aggregate `instantWithdrawsRequests[user]` and in `instantWithdrawsRequestsByEpoch[user][epochNumber]` / `instantWithdrawClaimsByEpoch[epochNumber]` [1](#0-0) . However, `claimInstantWithdrawRequest` only clears the aggregate — it never zeroes the per-epoch mappings [2](#0-1) . This mirrors CVE-2016-9273's pattern: a count (`td_nstrips` ⇄ per-epoch claim basis) is left stale after the pass that consumed it, and a later pass (default finalization) re-reads the stale count.

### Finding Description

- An attacker (any KYC-passed lender) requests an instant withdraw while the epoch is running. The basis is stored in `instantWithdrawsRequestsByEpoch[user][E]` and `instantWithdrawClaimsByEpoch[E]`, and `pendingInstantWithdraws` is increased [3](#0-2) .
- The honest manager calls `getInstantWithdrawFunds` after `instantWithdrawDelay`; the borrower funds the receipts, `collectInstantWithdrawFunds` reduces `pendingInstantWithdraws`, and `allowInstantWithdraw` is enabled [4](#0-3) .
- The attacker claims at par via `claimInstantWithdrawRequest`: the aggregate `instantWithdrawsRequests[user]` is burned and zeroed, but `instantWithdrawsRequestsByEpoch[user][E]` and `instantWithdrawClaimsByEpoch[E]` keep the stale basis [5](#0-4) .
- The same epoch later defaults (e.g. `stopEpoch` fails to pull funds → `_handleBorrowerDefault`) [6](#0-5) . Default finalization treats `instantWithdrawClaimsByEpoch[defaultEpoch]` as outstanding claim basis — the test at `test/foundry/IdleCreditVault.t.sol:4463-4472` shows prefunded instant receipts are expected inside `defaultPendingClaimBasis`/reserve sizing, i.e. the stale, already-paid basis is counted again [7](#0-6) .
- Post-default, `_claimDefaultedInstantWithdrawRequest` reads the stale per-epoch basis as `claimBasis` and computes `instantWithdrawsRequests[user] -= claimBasis`, which underflows (aggregate is already 0) and permanently reverts that user's defaulted-epoch claim path [8](#0-7) .

Broken invariant: the "pending claim basis" used for recovery pricing includes principal that already left the vault at par, so `defaultRecoveryReserve`/`defaultRecoveryPrice` are computed over an inflated denominator — an out-of-bounds read on a stale counter, same class as the tiffsplit `td_nstrips` mutation.

### Impact Explanation

- If finalization uses the inflated basis to size `defaultRecoveryPrice`, every honest defaulted-epoch claimant (normal, APR0, and instant receipts) receives a smaller haircutted payout than they should — direct, quantified dilution equal to the attacker's already-claimed instant amount divided by total basis.
- The portion of `defaultRecoveryReserve` earmarked for the stale basis can never be claimed: the attacker's `_claimDefaultedInstantWithdrawRequest` reverts on the `instantWithdrawsRequests[user] -= claimBasis` underflow, so that reserve is permanently locked in the strategy, freezing part of the recovered funds.
- Existing guards do not stop it: no flag, epoch gate, or `NotAllowed` check clears or validates the per-epoch instant basis on the funded-claim path; the stale entry is invisible until a same-epoch default occurs.

### Likelihood Explanation

Requires an instant-withdraw request that is funded and claimed at par, followed by a borrower default within the same epoch before `epochNumber` increments. Instant withdraws and defaults are both supported flows (covered by the test suite), and the attacker controls only the timing of their own claim; the default itself is triggered by honest manager/borrower actions, which is within the threat model. Unverified caveat: I could not confirm within the available context the exact lines of `finalizeDefault`/`defaultPendingClaimBasis` that consume `instantWithdrawClaimsByEpoch`; the test asserts prefunded instant basis is included in recovery accounting, which is consistent with the bug, but the precise reserve-vs-price dilution path should be confirmed against the full `finalizeDefault` implementation.

### Recommendation

In `claimInstantWithdrawRequest`, clear `instantWithdrawsRequestsByEpoch[_user][epochNumber]` and decrement `instantWithdrawClaimsByEpoch` for every receipt settled at par, mirroring how `_clearWithdrawClaimForEpoch` clears `withdrawsRequestsByEpoch` for normal requests [9](#0-8) . Alternatively, in `_claimDefaultedInstantWithdrawRequest`, saturate `claimBasis` to `min(basis, instantWithdrawsRequests[_user])` and reconcile `instantWithdrawClaimsByEpoch` at finalization against `pendingInstantWithdraws` plus actually-unclaimed funded receipts.

### Proof of Concept

Foundry fork PoC outline (against `test/foundry/IdleCreditVault.t.sol` harness):

```solidity
function testStaleInstantBasisDoubleCountedOnDefault() external {
    address attacker = makeAddr("attacker");
    _depositWithUser(attacker, 10_000 * ONE_SCALE, true);
    idleCDO.depositAA(10_000 * ONE_SCALE); // honest LP stays for comparison

    _startEpochAndCheckPrices(0);
    _stopEpochAndCheckPrices(0, initialProvidedApr / 2, _expectedFundsEndEpoch());

    // attacker requests instant withdraw -> basis recorded in epoch E
    vm.prank(attacker);
    uint256 basis = cdoEpoch.requestWithdraw(0, address(AAtranche));

    _startEpochAndCheckPrices(1);
    // manager pulls instant funds successfully; allowInstantWithdraw = true
    deal(defaultUnderlying, borrower, IdleCreditVault(address(strategy)).pendingInstantWithdraws());
    vm.warp(block.timestamp + cdoEpoch.instantWithdrawDelay() + 1);
    vm.prank(manager);
    cdoEpoch.getInstantWithdrawFunds();

    // attacker claims at par; per-epoch basis is NOT cleared
    vm.prank(attacker);
    cdoEpoch.claimInstantWithdrawRequest();
    IdleCreditVault vault = IdleCreditVault(address(strategy));
    uint256 epoch = vault.epochNumber();
    assertEq(vault.instantWithdrawsRequestsByEpoch(attacker, epoch), basis, "stale basis remains");

    // borrower defaults in the same epoch (e.g. stopEpoch pull fails)
    // then finalizeDefault(...) runs:
    // assert: defaultPendingClaimBasis includes `basis` again although it was paid at par
    // assert: vault.claimInstantWithdrawRequest(attacker) reverts on
    //         instantWithdrawsRequests[attacker] -= claimBasis underflow
}
```

Expected results: `instantWithdrawsRequestsByEpoch[attacker][epoch] == basis` after a full par claim; after `finalizeDefault`, the defaulted-epoch instant claim basis still contains the attacker's paid amount, diluting `defaultRecoveryPrice` for all honest claimants, and the attacker's `claimInstantWithdrawRequest` reverts permanently on the aggregate underflow, stranding the corresponding slice of `defaultRecoveryReserve`.

### Citations

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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L815-836)
```text
    uint256 normalAmount = withdrawsRequestsByEpoch[_user][_claimEpoch];
    if (normalAmount != 0) {
      withdrawsRequestsByEpoch[_user][_claimEpoch] = 0;
      // The aggregate may also include older funded receipts; clear only this epoch's piece.
      withdrawsRequests[_user] -= normalAmount;
    }
    Apr0UserData storage apr0User = apr0Users[_user];
    if (apr0User.principal != 0 && apr0User.principalEpoch == _claimEpoch) {
      if (_isClearingApr0) {
        uint256 apr0Principal = apr0User.principal;
        uint256 totalApr0Principal = apr0TotalPrincipal;
        // prepareStopEpochWithApr0 may already close the global APR0 bucket before default finalization.
        apr0TotalPrincipal = apr0Principal >= totalApr0Principal ? 0 : totalApr0Principal - apr0Principal;
      }
      apr0User.principal = 0;
      apr0User.principalEpoch = 0;
    }
    if (lastWithdrawRequest[_user] == _claimEpoch) {
      // The cleared epoch was the latest request marker. Any remaining normal/APR0 receipt
      // is older and already funded, so it can continue to the funded-claim path.
      lastWithdrawRequest[_user] = 0;
    }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L844-855)
```text
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
```

**File:** contracts/IdleCDOEpochVariant.sol (L563-592)
```text
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
  }

  /// @notice Handle borrower default
  function _handleBorrowerDefault(uint256 funds) internal {
    defaulted = true;
    // Do not reopen instant claims here. They remain disabled when funding is pending;
    // successful full funding is the only path that enables them before finalization.

    if (isProgrammableBorrower) {
      IProgrammableBorrower(_borrower()).onDefault();
    }

    // deposits should be already prevented
    if (!paused()) {
      _pause();
    }

    // stop the current epoch
    isEpochRunning = false;
```

**File:** test/foundry/IdleCreditVault.t.sol (L4463-4472)
```text
    uint256 activeBasis = cdoEpoch.getContractValue() + cdoEpoch.expectedEpochInterest() - cdoEpoch.pendingWithdrawFees();
    uint256 totalBasis = activeBasis + creditVault.defaultPendingClaimBasis();
    uint256 targetReserve = totalBasis * recoveryRatio / ONE_TRANCHE;
    uint256 recovered = targetReserve - prefundedInstant;
    deal(defaultUnderlying, manager, recovered);
    vm.startPrank(manager);
    IERC20Detailed(defaultUnderlying).approve(address(strategy), recovered);
    cdoEpoch.finalizeDefault(recovered, manager);
    vm.stopPrank();
    assertApproxEqAbs(creditVault.defaultRecoveryReserve(), targetReserve, 5, 'prefunded instant not reserved');
```
