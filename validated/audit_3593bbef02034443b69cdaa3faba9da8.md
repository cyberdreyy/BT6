### Title
Claimed instant-withdraw receipts are never removed from `instantWithdrawsRequestsByEpoch`/`instantWithdrawClaimsByEpoch`, corrupting default-recovery basis and reserve accounting - ([File: contracts/strategies/idle/IdleCreditVault.sol])

### Summary
`IdleCreditVault.requestInstantWithdraw` records each request both in the aggregate `instantWithdrawsRequests[user]` and in the per-epoch maps `instantWithdrawsRequestsByEpoch[user][epoch]` / `instantWithdrawClaimsByEpoch[epoch]`. When the receipt is later claimed via `claimInstantWithdrawRequest`, only the aggregate counter is cleared — the per-epoch entries are left set. If a borrower default is finalized in the same epoch while other instant receipts remain unfunded, `defaultPendingClaimBasis` and `_defaultPrefundedInstantReserve` still count the already-paid amounts, inflating the recovery basis and overstating the prefunded reserve. This is the idle-tranches analog of the reported bug: a per-operation accounting adjustment is written up front but never unset on the success path, corrupting all subsequent accounting that reads it.

### Finding Description
In `requestInstantWithdraw` the strategy writes three pieces of state for the same receipt:

```solidity
instantWithdrawsRequests[_user] += _amount;
instantWithdrawsRequestsByEpoch[_user][currentEpoch] += _amount;
instantWithdrawClaimsByEpoch[currentEpoch] += _amount;
pendingInstantWithdraws += _amount;
``` [1](#0-0) 

On claim, only the aggregate is cleared; the two per-epoch counters remain:

```solidity
uint256 amount = instantWithdrawsRequests[_user];
_burn(_user, amount);
instantWithdrawsRequests[_user] = 0;
_transferFundedClaim(_user, amount);
``` [2](#0-1) 

These stale per-epoch values are read during default finalization. `defaultPendingClaimBasis` adds the *whole* epoch claim bucket — including already-claimed receipts — whenever any unfunded remainder exists:

```solidity
basis = pendingWithdraws;
if (pendingInstantWithdraws != 0) {
  basis += instantWithdrawClaimsByEpoch[epochNumber];
}
``` [3](#0-2) 

And `_defaultPrefundedInstantReserve` treats `instantClaims - pendingInstant` as cash still held in the strategy, even though the claimed portion was already transferred out:

```solidity
if (instantBasis > pendingInstant) {
  prefundedReserve = instantBasis - pendingInstant;
}
``` [4](#0-3) 

Both feed `finalizeDefaultRecovery`, which computes `recoveryPrice = reserveAmount * RECOVERY_FULL / totalBasis` and sets `defaultRecoveryReserve = _recoveredAmount + prefundedReserve + defaultRecoveryReserve` — a reserve number that exceeds the real token balance. [5](#0-4) 

### Impact Explanation
Two concrete harms, both reachable by an unprivileged KYC-passing lender holding tranche tokens:

1. **Recovery dilution / theft of unclaimed yield.** A user who already received a full payout keeps a phantom claim in `totalBasis`, permanently lowering `defaultRecoveryPrice` for every other claimant (active LPs, pending normal receipts, remaining instant receipts). The attacker is not required to claim twice — the dilution alone steals recovery value from honest claimants.
2. **Overstated reserve / frozen claims.** `defaultRecoveryReserve` includes `prefundedReserve` computed from already-paid-out funds. When later claimants call `_claimDefaultedWithdrawRequest`/`_claimDefaultedInstantWithdrawRequest`, `_transferDefaultRecovery` decrements the reserve and attempts `safeTransfer`; once the real balance is exhausted, remaining claims revert — permanently freezing the tail claimants' recovery share while `defaultRecoveryReserve` still shows a positive balance. [6](#0-5) 

### Likelihood Explanation
Requires a specific but realistic sequence in one epoch:

- Epoch N runs with instant-withdraw enabled (`disableInstantWithdraw == false`, non-programmable borrower) after an APR cut triggers `_isInstantWithdrawEnabled` requests.
- At the next `startEpoch`, only part of `pendingInstantWithdraws` is funded (`pendingInstant > totUnderlyings` or partial `collectInstantWithdrawFunds`), leaving `pendingInstantWithdraws != 0` while some users claim the funded part.
- The borrower then defaults before/at the next stop (`sendFundsToBorrower` catch, `getInstantWithdrawFunds` catch, or `stopEpoch` pull failure → `_handleBorrowerDefault`), and owner/manager calls `finalizeDefault` in the same `epochNumber`. [7](#0-6) 

Existing guards do not stop it: `_ensureDefaultRecoveryInitialized` only normalizes aggregate `pendingWithdraws`/`apr0TotalPrincipal` and explicitly never touches instant buckets (`Pending instant withdrawals are never cleared automatically`, line 923); `_hasWithdrawRequest` only checks normal/APR0 state, not claimed instant receipts.

### Recommendation
In `claimInstantWithdrawRequest`, clear the per-epoch accounting the same way `_claimDefaultedInstantWithdrawRequest` does: subtract the claimed amount from `instantWithdrawsRequestsByEpoch[_user][currentEpoch]` and `instantWithdrawClaimsByEpoch[currentEpoch]` for the epoch the receipt belongs to. Alternatively, track a separate `instantWithdrawFundedByEpoch` counter so `defaultPendingClaimBasis` and `_defaultPrefundedInstantReserve` only count still-outstanding receipts.

### Proof of Concept
Foundry fork/unit PoC sketch (setup mirrors `test/foundry/IdleCreditVault.t.sol`):

```solidity
function testClaimedInstantReceiptInflatesDefaultBasis() external {
    // enable instant withdraws (apr drop), non-programmable borrower
    // userA, userB deposit AA; epoch0 runs; APR is cut at stopEpoch(0,0)

    // buffer: userA and userB requestInstantWithdraw equal amounts X
    // startEpoch: only X funded (pendingInstant = X remains)
    cdoEpoch.startEpoch();

    // userA claims the funded X -> instantWithdrawsRequests[A] = 0
    // BUG: instantWithdrawsRequestsByEpoch[A][N] and
    //      instantWithdrawClaimsByEpoch[N] still equal 2X
    cdoEpoch.claimInstantWithdrawRequest();

    // borrower defaults mid-epoch (e.g. getInstantWithdrawFunds pull fails)
    // -> defaulted = true, epochNumber still N

    // finalizeDefaultRecovery: basis includes instantWithdrawClaimsByEpoch[N] = 2X
    // though only X is outstanding; prefundedReserve counts X already paid out.
    // Assert: defaultPendingClaimBasis() == pendingWithdraws + 2X  (expected X)
    // Assert: last recovery claims revert / recoveryPrice diluted vs par.
}
```

Key assertions: after `claimInstantWithdrawRequest`, `instantWithdrawClaimsByEpoch[epochNumber]` still includes the claimed amount; after `finalizeDefault`, `defaultRecoveryPrice` is depressed by the phantom basis and `defaultRecoveryReserve` exceeds `underlyingToken.balanceOf(strategy) + recovered`, causing tail claims to revert inside `_transferDefaultRecovery`.

### Citations

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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L387-392)
```text
    uint256 amount = instantWithdrawsRequests[_user];
    // burn strategy tokens from user
    _burn(_user, amount);

    instantWithdrawsRequests[_user] = 0;
    _transferFundedClaim(_user, amount);
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L685-692)
```text
    uint256 prefundedReserve = _defaultPrefundedInstantReserve();
    uint256 reserveAmount = _recoveredAmount + prefundedReserve + defaultRecoveryReserve;
    // Recovery can be above par if the recovered funds exceed the computed basis.
    uint256 recoveryPrice = reserveAmount * RECOVERY_FULL / totalBasis;

    defaultRecoveryFinalized = true;
    defaultRecoveryReserve = reserveAmount;
    defaultRecoveryPrice = recoveryPrice;
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L912-917)
```text
  function _transferDefaultRecovery(address _user, uint256 _amount) internal {
    if (_amount == 0) return;
    // Every defaulted or post-default claim consumes the isolated recovery reserve.
    defaultRecoveryReserve -= _amount;
    underlyingToken.safeTransfer(_user, _amount);
  }
```

**File:** contracts/IdleCDOEpochVariant.sol (L295-303)
```text
    try this.sendFundsToBorrower(_toBorrower) {
      // funds transferred correctly
      _startEpochProgrammableBorrower(_pendingWithdraws);
    } catch {
      // The borrower did not receive the funds, so keep the strategy-token backing in the strategy.
      _transferUnderlyings(address(_strategy), _toBorrower);
      _strategy.reserveDefaultRecovery(_toBorrower);
      _handleBorrowerDefault(_toBorrower);
    }
```
