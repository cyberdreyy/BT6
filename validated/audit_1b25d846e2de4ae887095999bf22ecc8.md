### Title
Stale per-epoch instant-withdraw claim basis is never cleared on a funded claim, allowing double payout and diluted default recovery - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
The Samba CVE is an information-disclosure bug where residual, stale data is returned instead of fresh/zeroed data. The credit-vault analog is residual per-epoch claim accounting: `claimInstantWithdrawRequest` pays out a funded instant withdrawal but never clears `instantWithdrawsRequestsByEpoch[user][epoch]` or decrements `instantWithdrawClaimsByEpoch[epoch]`. That stale "residual" claim basis is later read by `_claimDefaultedInstantWithdrawRequest` and `defaultPendingClaimBasis` during default finalization, letting an already-paid claim be paid a second time and inflating the recovery basis.

### Finding Description
`requestInstantWithdraw` records receipt basis twice: per-user aggregate `instantWithdrawsRequests[_user]` and per-epoch `instantWithdrawsRequestsByEpoch[_user][currentEpoch]` plus `instantWithdrawClaimsByEpoch[currentEpoch]` [1](#0-0) .

On a successful (funded) claim, `claimInstantWithdrawRequest` only clears the aggregate `instantWithdrawsRequests[_user]`; both per-epoch mappings are left untouched [2](#0-1) . Contrast with normal withdraws, where `_clearWithdrawClaimForEpoch` zeroes `withdrawsRequestsByEpoch[_user][_claimEpoch]` on claim [3](#0-2) .

Two consequences when the same epoch later defaults:

1. Inflated recovery basis: `defaultPendingClaimBasis` adds `instantWithdrawClaimsByEpoch[epochNumber]` whenever `pendingInstantWithdraws != 0` [4](#0-3) . That bucket still includes receipts that were already fully paid, so `finalizeDefaultRecovery` computes `recoveryPrice = reserveAmount / totalBasis` against an inflated basis [5](#0-4) , diluting every legitimate claimant and leaving the reserve short (insolvency for later claimants).

2. Double payout: if the attacker places a new instant withdraw request in a subsequent epoch before the default finalizes, their `instantWithdrawsRequests[_user]` is non-zero again. After finalization, `claimInstantWithdrawRequest` calls `_claimDefaultedInstantWithdrawRequest`, which reads the stale `instantWithdrawsRequestsByEpoch[user][defaultEpoch]`, subtracts it from the new aggregate balance, burns receipt tokens, and transfers `claimBasis * defaultRecoveryPrice / 1e18` from `defaultRecoveryReserve` [6](#0-5)  — paying a claim that was already paid at par.

### Impact Explanation
Direct theft/insolvency of the isolated default-recovery reserve. An attacker who instant-withdraws early in an epoch, gets funded and claims, then re-requests a small instant withdrawal before finalization can drain recovery reserve proportional to their stale basis, or at minimum permanently dilute the recovery price so honest defaulted-epoch claimants receive less or nothing (last claimants' transfers revert due to depleted reserve).

### Likelihood Explanation
Requires only an unprivileged KYC-passing lender performing ordinary actions: instant withdraw → funded claim → new instant withdraw → borrower default in same epoch with a partially unfunded instant queue (`pendingInstantWithdraws != 0`, so `defaultInstantWithdrawsFinalized` is set). All attacker actions are user-permitted; the honest manager/borrower sequence (default, `finalizeDefaultRecovery`) triggers it. No privileged misbehavior needed.

### Recommendation
Mirror the normal-withdraw clearing discipline for instant requests: in `claimInstantWithdrawRequest`, zero `instantWithdrawsRequestsByEpoch[_user][currentOrRequestEpoch]` and decrement `instantWithdrawClaimsByEpoch[epoch]` for the claimed amount (tracking the request epoch per user if claims can span epochs), so funded claims cannot re-enter recovery accounting.

### Proof of Concept
A Foundry test extending `test/foundry/IdleCreditVault.t.sol`:

```solidity
function testStaleInstantBasisDoubleClaim() external {
    // Epoch running; attacker requests instant withdraw
    // 1) cdoEpoch.requestInstantWithdraw via tranche for attacker
    // 2) CDO funds it via collectInstantWithdrawFunds / startEpoch path
    // 3) attacker claimInstantWithdrawRequest() -> paid at par
    //    assert instantWithdrawsRequestsByEpoch[attacker][epoch] != 0 (stale)
    // 4) attacker makes a new (small) requestInstantWithdraw
    // 5) borrower defaults; manager finalizeDefault -> finalizeDefaultRecovery
    //    with defaultInstantWithdrawsFinalized == true
    // 6) attacker claimInstantWithdrawRequest() again:
    //    _claimDefaultedInstantWithdrawRequest pays claimBasis*recoveryPrice
    //    from defaultRecoveryReserve for the already-claimed basis
    // 7) assert reserve drained / other claimants' claims revert or are haircut
    //    below defaultRecoveryPrice entitlement
}
```

Key assertions: after step 3, `strategy.instantWithdrawsRequestsByEpoch(attacker, epoch)` remains non-zero while `instantWithdrawsRequests(attacker)` is zero — the residual data. After step 6, `defaultRecoveryReserve` decreases by a second payment on the same original receipt.

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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L644-649)
```text
  function defaultPendingClaimBasis() public view returns (uint256 basis) {
    basis = pendingWithdraws;
    if (pendingInstantWithdraws != 0) {
      basis += instantWithdrawClaimsByEpoch[epochNumber];
    }
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L688-692)
```text
    uint256 recoveryPrice = reserveAmount * RECOVERY_FULL / totalBasis;

    defaultRecoveryFinalized = true;
    defaultRecoveryReserve = reserveAmount;
    defaultRecoveryPrice = recoveryPrice;
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L842-855)
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
```
