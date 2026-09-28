### Title
Claimed instant-withdraw receipt basis never cleared in `instantWithdrawsRequestsByEpoch`/`instantWithdrawClaimsByEpoch` enables recovery-reserve theft and permanent claim freeze after default - (contracts/strategies/idle/IdleCreditVault.sol)

### Summary
The external bug class — a flag/basis set during one flow that is never reset, so a later flow reverts or misbehaves — maps directly onto `IdleCreditVault.claimInstantWithdrawRequest`. When an instant-withdraw receipt is paid out at par, only the aggregate `instantWithdrawsRequests[_user]` is zeroed; the per-epoch basis `instantWithdrawsRequestsByEpoch[_user][epoch]` and the epoch total `instantWithdrawClaimsByEpoch[epoch]` are left stale [1](#0-0) . If the borrower defaults in the same epoch while other instant receipts remain unfunded, `finalizeDefaultRecovery` stores `defaultRecoveryEpoch = epochNumber` and `defaultInstantWithdrawsFinalized = true` [2](#0-1) , and `_claimDefaultedInstantWithdrawRequest` then reads the stale per-epoch basis of the already-paid user [3](#0-2) .

### Finding Description
`requestInstantWithdraw` records per-epoch accounting: `instantWithdrawsRequestsByEpoch[_user][currentEpoch] += _amount` and `instantWithdrawClaimsByEpoch[currentEpoch] += _amount` [4](#0-3) . The normal claim path burns the receipt and zeroes only the aggregate:

```solidity
// contracts/strategies/idle/IdleCreditVault.sol
uint256 amount = instantWithdrawsRequests[_user];
_burn(_user, amount);
instantWithdrawsRequests[_user] = 0;
_transferFundedClaim(_user, amount);
```

Neither `instantWithdrawsRequestsByEpoch[_user][epochNumber]` nor `instantWithdrawClaimsByEpoch[epochNumber]` is decremented — the analog of `finalizeOpen()` never resetting `_packetBurnType`.

Two consequences once `defaultRecoveryEpoch == epochNumber` (i.e., the user claimed and the default was finalized within the same epoch, which is possible because `epochNumber` only increments on `deposit` during `isEpochRunning` [5](#0-4) ):

1. **Reserve theft / receipt corruption.** `_claimDefaultedInstantWithdrawRequest` computes `claimBasis` from the stale `instantWithdrawsRequestsByEpoch[_user][defaultEpoch]` [6](#0-5) . If the user holds any new instant receipt (e.g. a fresh `requestInstantWithdraw` in the same epoch after claiming the first), `instantWithdrawsRequests[_user] -= claimBasis` silently consumes the *new* receipt's balance, `pendingInstantWithdraws` and `instantWithdrawClaimsByEpoch[defaultEpoch]` are decremented for an already-settled claim, and the user is paid `claimBasis * defaultRecoveryPrice` a second time out of `defaultRecoveryReserve` [7](#0-6)  — draining the reserve that funds genuinely defaulted victims.
2. **Permanent freeze.** If the user has no new instant receipt, `instantWithdrawsRequests[_user] -= claimBasis` underflows (0 - stale) and `claimInstantWithdrawRequest` always reverts, permanently freezing any subsequent instant claim for that user and leaving the user's stale basis unclaimable-but-counted.
3. **Basis inflation.** `defaultPendingClaimBasis` adds `instantWithdrawClaimsByEpoch[epochNumber]` [8](#0-7) , which still includes already-claimed receipts, mispricing `defaultRecoveryPrice` for every active LP and receipt holder.

### Impact Explanation
Direct theft of `defaultRecoveryReserve` by an unprivileged tranche holder who claims the same instant receipt twice (once at par, once at recovery price), plus permanent freezing of that user's future instant claims via underflow revert, plus diluted recovery for all defaulted-epoch claimants. Loss is quantified as the stale claim basis times `defaultRecoveryPrice`, bounded by the user's prior instant-withdraw amount.

### Likelihood Explanation
Requires only ordinary user actions in sequence around honest privileged calls: request instant withdraw → CDO collects funds → user claims → borrower defaults mid-epoch with another user's instant request still unfunded → `finalizeDefaultRecovery` runs → user claims again (or makes a small new instant request and claims). No malicious privileged role, no exotic mode; works whenever instant withdrawals coexist with a default in one epoch.

### Recommendation
In `claimInstantWithdrawRequest` (and equivalently in any path that pays an instant receipt), clear the per-epoch accounting the same way `_clearWithdrawClaimForEpoch` does for normal receipts [9](#0-8) :

```solidity
uint256 currentEpochBasis = instantWithdrawsRequestsByEpoch[_user][epochNumber];
// iterate/zero the user's per-epoch entries actually being paid
instantWithdrawsRequestsByEpoch[_user][epochNumber] = 0;
instantWithdrawClaimsByEpoch[epochNumber] -= currentEpochBasis;
instantWithdrawsRequests[_user] = 0;
```

Since a user's receipts may span epochs, the claim should clear each non-zero `instantWithdrawsRequestsByEpoch[_user][e]` (or track and clear the request epoch explicitly) and decrement `instantWithdrawClaimsByEpoch[e]` accordingly, so `defaultPendingClaimBasis` and `_claimDefaultedInstantWithdrawRequest` see only genuinely outstanding receipts.

### Proof of Concept
Foundry fork test sketch (based on `test/foundry/IdleCreditVault.t.sol` helpers, instant-withdraw variant):

```solidity
function testStaleInstantBasisDoubleClaim() external {
    _useInstantWithdrawVariant();
    uint256 amount = 1000 * ONE_SCALE;
    idleCDO.depositAA(amount);            // FASA-like KYC'd lender
    _startEpochAndCheckPrices(0);

    // epoch E: user requests instant withdraw and it gets funded + claimed
    cdoEpoch.requestInstantWithdraw(amount / 2, address(AAtranche));
    // borrower/manager fund instant queue (collectInstantWithdrawFunds via CDO)
    _fundInstantWithdraws();
    cdoEpoch.claimInstantWithdrawRequest();          // paid at par

    // stale state remains
    assertEq(strategy.instantWithdrawsRequestsByEpoch(address(this), strategy.epochNumber()), amount / 2);

    // second user requests instant withdraw that stays UNFUNDED
    // -> pendingInstantWithdraws != 0
    // borrower defaults mid-epoch; manager finalizes recovery
    _defaultAndFinalize();                           // defaultRecoveryEpoch == epochNumber

    // user makes a tiny new instant request in same epoch, then claims
    cdoEpoch.requestInstantWithdraw(1, address(AAtranche));
    uint256 balPre = underlying.balanceOf(address(this));
    cdoEpoch.claimInstantWithdrawRequest();

    // stale basis paid out of defaultRecoveryReserve again
    assertGt(underlying.balanceOf(address(this)) - balPre, 0);
    // ...and user's new receipt was consumed by the stale subtraction
}
```

Uncertainty: the exact CDO-side entry points used to fund instant withdrawals and declare default (`_fundInstantWithdraws`, `_defaultAndFinalize`) are placeholders for the existing test helpers in `test/foundry/IdleCreditVault.t.sol`; the strategy-side accounting flaw is confirmed directly in the code cited above.

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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L607-611)
```text
    if (IIdleCDOEpochVariant(idleCDO).isEpochRunning()) {
      // deposit done on stopEpoch (before setting the var to false) so we reset the counter
      totEpochDeposits = 0;
      epochNumber += 1;
    } else {
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L644-648)
```text
  function defaultPendingClaimBasis() public view returns (uint256 basis) {
    basis = pendingWithdraws;
    if (pendingInstantWithdraws != 0) {
      basis += instantWithdrawClaimsByEpoch[epochNumber];
    }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L693-696)
```text
    defaultRecoveryEpoch = epochNumber;
    // A non-zero pending instant bucket means current-epoch instant receipts were not fully funded
    // and must be paid through the same recovery ratio as normal pending receipts.
    defaultInstantWithdrawsFinalized = pendingInstantWithdraws != 0;
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
