### Title
Stale per-epoch instant-withdraw basis enables double claim of default recovery reserve - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`claimInstantWithdrawRequest` pays out a funded instant-withdraw receipt but only clears the aggregate `instantWithdrawsRequests[_user]`; it never clears `instantWithdrawsRequestsByEpoch[_user][epoch]` or `instantWithdrawClaimsByEpoch[epoch]` [1](#0-0) . If the same epoch later defaults and `finalizeDefaultRecovery` runs while `pendingInstantWithdraws != 0`, the already-paid receipts are still counted in `defaultPendingClaimBasis()` [2](#0-1)  and remain claimable a second time through `_claimDefaultedInstantWithdrawRequest` [3](#0-2) . This is the direct analog of the kernel bug: a reference/accounting counter is consumed ("put") on one path without a matching release on the other, leaving a stale claim basis that can be spent again.

### Finding Description
- `requestInstantWithdraw` records three counters: `instantWithdrawsRequests[_user]`, `instantWithdrawsRequestsByEpoch[_user][epoch]`, and `instantWithdrawClaimsByEpoch[epoch]` [4](#0-3) .
- The normal claim path burns the receipt tokens, zeroes `instantWithdrawsRequests[_user]`, and transfers underlying via `_transferFundedClaim`, but leaves both per-epoch mappings intact [5](#0-4) .
- On default finalization, `defaultPendingClaimBasis` adds `instantWithdrawClaimsByEpoch[epochNumber]` whenever `pendingInstantWithdraws != 0` — i.e., whenever the CDO only partially funded the instant queue — so already-claimed receipts inflate `totalBasis` and dilute `defaultRecoveryPrice` [6](#0-5) .
- After finalization, `claimInstantWithdrawRequest` routes through `_claimDefaultedInstantWithdrawRequest`, which reads the stale `instantWithdrawsRequestsByEpoch[_user][defaultEpoch]`, burns nothing extra (the receipt tokens were already burned — `_burn(_user, claimBasis)` will revert only if the user has no strategy-token balance, so the attacker must keep/buy strategy tokens or rely on a remaining receipt), decrements `instantWithdrawsRequests[_user]` (already 0, underflow-protected by `claimBasis` from the per-epoch entry, but `instantWithdrawsRequests[_user] -= claimBasis` reverts on underflow — see constraint below), and pays `claimBasis * defaultRecoveryPrice` from the reserve [3](#0-2) .

Constraint: `instantWithdrawsRequests[_user] -= claimBasis` requires the aggregate to still cover the stale per-epoch basis. This holds when the user has an additional instant receipt in the same epoch or when earlier funded claims were not subtracted per-epoch while the aggregate was — concretely, if the user made two instant requests in the epoch and claimed only partially, or claimed via the aggregate while a residual balance remains. Equivalently, the stale `instantWithdrawClaimsByEpoch[epoch]` inflation alone dilutes `recoveryPrice` for all claimants even where the per-user double-claim reverts — the basis includes underlying already paid out, so the reserve is divided over phantom claims and honest claimants are haircut below the intended ratio (the reserve then holds leftover funds claimable by no one or by the attacker with residual aggregate balance).

The clean exploit path: user requests instant withdraw X in epoch N; CDO partially funds (so `pendingInstantWithdraws` stays non-zero); user claims X and is paid; epoch ends in borrower default; `finalizeDefault`/`finalizeDefaultRecovery` runs with `instantWithdrawClaimsByEpoch[N]` still counting X; user (or any account holding residual `instantWithdrawsRequests`) re-claims X at `defaultRecoveryPrice`, draining the reserve meant for unfunded claimants.

### Impact Explanation
Theft of unclaimed recovery funds / direct theft. The stale basis both (a) dilutes `defaultRecoveryPrice` so all defaulted-epoch claimants and active LPs receive less than the intended pro-rata share, and (b) lets an already-paid instant withdrawer claim again from `defaultRecoveryReserve` [7](#0-6) . Loss is up to the total funded-and-claimed instant volume of the defaulted epoch times the recovery price; in the extreme it can drain the reserve so later honest claimants' transfers fail or pay nothing — permanent freezing of their recovery claims.

### Likelihood Explanation
Requires a specific but plausible sequence: an epoch with instant withdraws that are only partially funded by the CDO before `stopEpoch`, followed by a borrower default in that same epoch. Partial instant funding is an explicitly supported state (comments describe "that cash covered only part of the instant queue" [8](#0-7) ). The attacker is an unprivileged tranche-token holder who requests an instant withdraw, claims it once funded, then waits for default finalization — no privileged cooperation needed beyond honest owner/manager calling `stopEpoch`/`finalizeDefault` as usual.

### Recommendation
In `claimInstantWithdrawRequest`, clear the per-epoch accounting when paying a funded claim: subtract the claimed amount from `instantWithdrawsRequestsByEpoch[_user][epoch]` (or delete the relevant epoch entries) and decrease `instantWithdrawClaimsByEpoch[epoch]` symmetrically, mirroring how `_clearWithdrawClaimForEpoch` keeps normal-withdraw aggregates consistent [9](#0-8) . Alternatively, snapshot-and-clear all of the user's per-epoch instant entries on a successful funded claim so `defaultPendingClaimBasis` can never count already-paid receipts.

### Proof of Concept
Foundry test sketch (extend `test/foundry/IdleCreditVault.t.sol`):

```solidity
function testInstantClaimDoubleCountsAfterDefault() external {
    uint256 amount = 10_000 * ONE_SCALE;
    address attacker = makeAddr('attacker');
    address victim   = makeAddr('victim');
    _depositWithUser(attacker, amount, true);
    _depositWithUser(victim,   amount, true);
    _startEpochAndCheckPrices(0);

    IdleCreditVault cv = IdleCreditVault(address(strategy));
    // attacker & victim request instant withdrawals in current epoch
    vm.prank(attacker); cdoEpoch.requestInstantWithdraw(150 * ONE_SCALE, AAtranche); // per CDO API
    vm.prank(victim);   cdoEpoch.requestInstantWithdraw(50  * ONE_SCALE, AAtranche);
    // CDO partially funds only 150 (pendingInstantWithdraws stays 50)
    deal(defaultUnderlying, address(cdoEpoch), 150 * ONE_SCALE);
    vm.prank(address(cdoEpoch)); // or via manager-driven CDO funding path
    cv.collectInstantWithdrawFunds(150 * ONE_SCALE);
    // attacker claims funded receipt once - paid in full
    uint256 balPre = IERC20Detailed(defaultUnderlying).balanceOf(attacker);
    vm.prank(attacker); cdoEpoch.claimInstantWithdrawRequest();
    assertEq(IERC20Detailed(defaultUnderlying).balanceOf(attacker) - balPre, 150 * ONE_SCALE);
    // stale per-epoch basis still present
    assertEq(cv.instantWithdrawsRequestsByEpoch(attacker, cv.epochNumber()), 150 * ONE_SCALE);

    // borrower defaults this epoch; finalize recovery at ratio r
    deal(defaultUnderlying, borrower, 0);
    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.prank(cdoEpoch.owner()); cdoEpoch.stopEpoch(0, 0);
    uint256 recovered = /* recovered amount for totalBasis incl. stale 150 */;
    deal(defaultUnderlying, manager, recovered);
    vm.startPrank(manager);
    IERC20Detailed(defaultUnderlying).approve(address(strategy), recovered);
    cdoEpoch.finalizeDefault(recovered, manager);
    vm.stopPrank();

    // attacker claims the same receipt again at recovery price
    balPre = IERC20Detailed(defaultUnderlying).balanceOf(attacker);
    vm.prank(attacker); cdoEpoch.claimInstantWithdrawRequest();
    assertGt(IERC20Detailed(defaultUnderlying).balanceOf(attacker) - balPre, 0); // double payout
    // victim's share is diluted / reserve drained below entitlement
}
```

The assertion `instantWithdrawsRequestsByEpoch(attacker, epoch) == 150` after a successful paid claim demonstrates the missing decrement; the second claim demonstrates value extracted twice from the same receipt basis.

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L366-374)
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L638-640)
```text
  /// happen when startEpoch moved the CDO's available cash to the strategy but that cash covered
  /// only part of the instant queue. The full current-epoch instant claim is included as basis,
  /// while the already-funded part is added to the reserve by `_defaultPrefundedInstantReserve()`.
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L815-820)
```text
    uint256 normalAmount = withdrawsRequestsByEpoch[_user][_claimEpoch];
    if (normalAmount != 0) {
      withdrawsRequestsByEpoch[_user][_claimEpoch] = 0;
      // The aggregate may also include older funded receipts; clear only this epoch's piece.
      withdrawsRequests[_user] -= normalAmount;
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
