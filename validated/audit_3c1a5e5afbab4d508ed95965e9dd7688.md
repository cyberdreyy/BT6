### Title
Stale instant-withdraw receipt basis is never cleared on funded claims, inflating default recovery basis and permanently locking reserve - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
The kernel bug is a realloc error path that NULLs the new pointer while leaking the old allocation — state for the old buffer is dropped but never freed. The closest analog in idle-tranches is `claimInstantWithdrawRequest`: when a user claims a funded instant withdrawal, the function burns `instantWithdrawsRequests[_user]` and zeroes the aggregate, but never clears the per-epoch accounting `instantWithdrawsRequestsByEpoch[_user][epoch]` / `instantWithdrawClaimsByEpoch[epoch]` that was allocated at request time in `requestInstantWithdraw`. That stale basis is the "leaked old allocation": it persists into the default-recovery path, where `defaultPendingClaimBasis` counts it again and `_claimDefaultedInstantWithdrawRequest` can never settle it (it underflows on `instantWithdrawsRequests[_user] -= claimBasis`), permanently locking that share of `defaultRecoveryReserve` and diluting every other claimant's `defaultRecoveryPrice`.

### Finding Description
`requestInstantWithdraw` records receipt basis in three places: `instantWithdrawsRequests[_user]`, `instantWithdrawsRequestsByEpoch[_user][currentEpoch]`, and `instantWithdrawClaimsByEpoch[currentEpoch]` [1](#0-0) . The normal claim path only clears the first:

```solidity
uint256 amount = instantWithdrawsRequests[_user];
_burn(_user, amount);
instantWithdrawsRequests[_user] = 0;
_transferFundedClaim(_user, amount);
``` [2](#0-1) 

The per-epoch mappings are only decremented inside `_claimDefaultedInstantWithdrawRequest`, which runs only after default finalization [3](#0-2) . A user can reach the funded claim path mid-epoch whenever the strategy already holds underlying covering their request (partial prefunding: `collectInstantWithdrawFunds` leaves `pendingInstantWithdraws > 0` while funding part of the queue) [4](#0-3) . After the claim, `instantWithdrawsRequests[_user]` is 0 but `instantWithdrawsRequestsByEpoch[_user][epoch]` and `instantWithdrawClaimsByEpoch[epoch]` still hold the full requested basis.

If the borrower then defaults in that same epoch while `pendingInstantWithdraws != 0`, `defaultPendingClaimBasis()` adds `instantWithdrawClaimsByEpoch[epochNumber]` — including the already-paid amount — to the recovery basis [5](#0-4) . `finalizeDefaultRecovery` therefore computes `recoveryPrice = reserveAmount * RECOVERY_FULL / totalBasis` over a phantom-inflated `totalBasis` [6](#0-5) . Post-finalization, the stale entry can never be cleared: `_claimDefaultedInstantWithdrawRequest` reads the nonzero `instantWithdrawsRequestsByEpoch[_user][defaultEpoch]`, then executes `instantWithdrawsRequests[_user] -= claimBasis` on a zeroed aggregate, reverting on underflow [7](#0-6) . The share of the reserve priced for that phantom basis is locked forever, and every legitimate defaulted-epoch claimant is haircut by the inflated denominator.

### Impact Explanation
Unprivileged attacker (a KYC-passing lender holding tranche tokens → strategy-token receipts) can, in a single epoch: request an instant withdrawal, wait for partial funding, claim the funded portion, and leave a stale per-epoch basis. On a subsequent borrower default in that epoch, `defaultRecoveryPrice` is computed against a basis inflated by the attacker's already-paid amount, so (a) all other pending-receipt and active-LP claimants recover less than entitled, and (b) the reserve fraction allocated to the phantom basis is permanently frozen — it can never be claimed because the attacker's defaulted-claim path reverts on arithmetic underflow. Loss is quantified as `staleBasis * reserveAmount / totalBasis` of the recovery reserve, plus the dilution of all other claims.

### Likelihood Explanation
Requires: (1) instant withdrawals enabled and a pending remainder at the moment of claim, i.e., the CDO funded only part of the instant queue — this happens whenever liquidity is tight at stopEpoch; (2) a borrower default in the same epoch. Both are honest-manager/borrower-sequence conditions, not attacker-controlled defaults; the attacker only needs to time an ordinary `requestInstantWithdraw`/`claimInstantWithdrawRequest` pair through the CDO. No privileged action needed.

### Recommendation
In `claimInstantWithdrawRequest`, clear the per-epoch basis the same way the defaulted path does: iterate the request epochs for the user (or track `lastInstantRequestEpoch`), zero `instantWithdrawsRequestsByEpoch[_user][epoch]` and decrement `instantWithdrawClaimsByEpoch[epoch]` by the claimed amount before `_transferFundedClaim`. Alternatively, make the funded claim path route through `_clearWithdrawClaimForEpoch`-style logic so per-epoch ledgers always stay consistent with the aggregate.

### Proof of Concept
Foundry fork sketch (against `test/foundry/IdleCreditVault.t.sol` harness):

```solidity
// 1. Deposit as attacker via cdoEpoch.depositAA, startEpoch.
// 2. cdoEpoch.requestInstantWithdraw(x, AAtranche) -> mints receipt,
//    sets instantWithdrawsRequests[attacker] = x,
//    instantWithdrawsRequestsByEpoch[attacker][E] = x,
//    instantWithdrawClaimsByEpoch[E] = x.
// 3. Manager stops epoch; borrower funds only PART of pendingInstantWithdraws
//    (e.g., limited vault liquidity as in testProgrammableBorrowerStopEpochRevertsWhenVaultLiquidityUnavailable).
//    pendingInstantWithdraws = x - fundedPart.
// 4. Attacker calls cdoEpoch.claimInstantWithdrawRequest():
//    receives fundedPart (or x if fully funded-but-remainder-pending setup via a
//    second requester), instantWithdrawsRequests[attacker] = 0,
//    BUT instantWithdrawsRequestsByEpoch[attacker][E] still = x.
// 5. Borrower defaults in epoch E; manager calls _handleBorrowerDefault /
//    finalizeDefault with recoveredAmount.
// 6. finalizeDefaultRecovery: totalBasis includes instantWithdrawClaimsByEpoch[E]
//    (still counting attacker's claimed x) -> defaultRecoveryPrice is diluted.
// 7. Attacker calls claimInstantWithdrawRequest again -> reverts on
//    instantWithdrawsRequests[attacker] -= x underflow; the reserve share priced
//    for x is permanently locked. Other claimants receive less than 1:1 of
//    their funded basis.
```

Key assertion to demonstrate: after step 4, `strategy.instantWithdrawsRequestsByEpoch(attacker, E) != 0` while `strategy.instantWithdrawsRequests(attacker) == 0`, and after finalization, `defaultRecoveryPrice < reserveAmount * 1e18 / trueBasis` with an unclaimable reserve remainder.

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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L387-392)
```text
    uint256 amount = instantWithdrawsRequests[_user];
    // burn strategy tokens from user
    _burn(_user, amount);

    instantWithdrawsRequests[_user] = 0;
    _transferFundedClaim(_user, amount);
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L398-402)
```text
  function collectInstantWithdrawFunds(uint256 _amount) external {
    _onlyIdleCDO();
    if (_amount == 0) return;
    pendingInstantWithdraws -= _amount;
    underlyingToken.safeTransferFrom(idleCDO, address(this), _amount);
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L644-648)
```text
  function defaultPendingClaimBasis() public view returns (uint256 basis) {
    basis = pendingWithdraws;
    if (pendingInstantWithdraws != 0) {
      basis += instantWithdrawClaimsByEpoch[epochNumber];
    }
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L844-853)
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
```
