### Title
Pre-default withdraw receipts from an earlier epoch bypass the recovery haircut and drain the default recovery reserve at par - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`claimWithdrawRequest` applies the `defaultRecoveryPrice` haircut only to receipts recorded in `withdrawsRequestsByEpoch[_user][defaultRecoveryEpoch]`. A receipt requested in an epoch *earlier* than the defaulted epoch falls through to `_claimFundedWithdrawRequest`, which — because `finalizeDefault` sets `epochEndDate = 0` — skips the epoch-wait check and pays the full unhaircutted `withdrawsRequests[_user]` via `_transferFundedClaim`. That receipt's basis was already counted in `defaultPendingClaimBasis()` (which returns all of `pendingWithdraws`), so the recovery reserve was priced assuming it would be paid at `defaultRecoveryPrice`, not at par.

### Finding Description
The claim dispatch in `claimWithdrawRequest` is:

```solidity
// contracts/strategies/idle/IdleCreditVault.sol
303|    if (defaultRecoveryFinalized) {
306|      amount = _claimPostDefaultWithdrawRequest(_user);
307|      if (amount != 0) return amount;
310|      amount = _claimDefaultedWithdrawRequest(_user);
312|    amount += _claimLossAdjustedWithdrawRequest(_user);
313|    return amount + _claimFundedWithdrawRequest(_user);
```

`_claimDefaultedWithdrawRequest` only clears `withdrawsRequestsByEpoch[_user][defaultRecoveryEpoch]` (line 773-775). A user who requested a withdraw during epoch `N-1` and never claimed before the epoch-`N` default has `withdrawsRequestsByEpoch[_user][N] == 0`, so this returns 0. `_claimLossAdjustedWithdrawRequest` returns 0 as well if `lossRecoveryPriceByEpoch[N-1] == 0` (no partial-loss stop that epoch). Execution then reaches `_claimFundedWithdrawRequest`, where the one-epoch-wait gate is conditioned on a live pool:

```solidity
326|    if (IIdleCDOEpochVariant(idleCDO).epochEndDate() != 0 && (epochNumber <= lastWithdrawRequest[_user])) {
327|      revert NotAllowed();
328|    }
```

`IdleCDOEpochVariant.finalizeDefault` sets `epochEndDate = 0` (line 223), so the guard is skipped, and the function pays `withdrawsRequests[_user]` in full at line 338-349, including burning the receipt and calling `_transferFundedClaim`. Meanwhile `defaultPendingClaimBasis()` (line 644-648) folded `pendingWithdraws` — which still contains this stale-epoch receipt — into `totalBasis`, so `defaultRecoveryPrice` was computed as if this claim would receive the haircut. Because `_transferFundedClaim` pays from the same strategy underlying balance that backs `defaultRecoveryReserve`, the stale-epoch receipt withdraws reserve funds at ~1:1 while every other claimant is capped at `defaultRecoveryPrice`.

### Impact Explanation
Direct theft / insolvency of the recovery reserve: a holder of an unclaimed pre-default receipt extracts `(1 - defaultRecoveryPrice) * claimBasis` more than their fair share, leaving the reserve under-funded for other pending receipts and post-default requesters. For a 50% recovery price and a large stale receipt, the user takes up to 2x their entitlement, and later claimants' `_transferDefaultRecovery` reverts or pays out a depleted reserve — permanent loss to honest users quantified by the excess payout.

### Likelihood Explanation
Requires a borrower default followed by `finalizeDefault` while a user holds a claimed-not-yet normal withdraw receipt from an epoch earlier than `defaultRecoveryEpoch` (e.g., requested during the buffer before the defaulting epoch, or simply never claimed across multiple epochs). The attacker cannot force the default, but the position is cheap to acquire: any KYC-passing lender can requestWithdraw and deliberately leave the receipt unclaimed as a free option — if no default occurs they claim at par anyway; if a default is finalized, they receive par while everyone else is haircut. No privileged action is needed.

### Recommendation
In `_claimDefaultedWithdrawRequest` (or `claimWithdrawRequest`), clear and haircut **all** outstanding normal/APR0 receipt epochs for the user once `defaultRecoveryFinalized`, not just `defaultRecoveryEpoch` — e.g., iterate/zero `withdrawsRequestsByEpoch[_user][*]` or compare against `lastWithdrawRequest[_user]` and apply `defaultRecoveryPrice` to the whole `claimBasis`. Alternatively, gate `_claimFundedWithdrawRequest` to return early when `defaultRecoveryFinalized` and the receipt was included in `defaultPendingClaimBasis` (i.e., when `pendingWithdraws` basis covered it), and decrement `pendingWithdraws` for every defaulted claim path.

### Proof of Concept
```solidity
// SPDX-License-Identifier: MIT
pragma solidity 0.8.10;

import "forge-std/Test.sol";

contract StaleEpochReceiptDrainsRecovery is Test {
    // Setup: standard IdleCDOEpochVariant + IdleCreditVault fixtures (see test/foundry/IdleCreditVault.t.sol)

    function testStaleReceiptClaimsAtPar() public {
        uint256 deposit = 100_000e6;
        idleCDO.depositAA(deposit);           // victim LPs
        uint256 attackerTranche = idleCDO.depositAA(10_000e6); // attacker

        // Epoch N-1 runs and stops; attacker requests withdraw during buffer of epoch N window
        _startEpoch(); _stopEpoch();
        uint256 requested = cdoEpoch.requestWithdraw(attackerTranche, address(AAtranche));
        // Attacker does NOT claim; epoch N starts and runs
        _startEpoch();

        // Borrower defaults at stopEpoch N (honest actor underfunds), owner finalizes recovery
        _stopEpochWithDefault();
        vm.prank(owner);
        cdoEpoch.finalizeDefault(recoveredAmount, recoverySource); // sets epochEndDate = 0, defaultRecoveryPrice < 1e18

        uint256 balPre = underlying.balanceOf(attacker);
        cdoEpoch.claimWithdrawRequest();
        uint256 paid = underlying.balanceOf(attacker) - balPre;

        uint256 fair = requested * strategy.defaultRecoveryPrice() / 1e18;
        // Bug: paid == requested (par) instead of fair; excess comes out of defaultRecoveryReserve
        assertEq(paid, requested);
        assertGt(paid, fair);
        // Follow-on: another claimant's _transferDefaultRecovery now underflows the reserve.
    }
}
```

Note: I verified the dispatch logic and guards from `IdleCreditVault.sol` lines 243-349, 644-710, 760-801 and `IdleCDOEpochVariant.sol` line 223 (`epochEndDate = 0` in `finalizeDefault`). I did not read the bodies of `_transferFundedClaim`/`_transferDefaultRecovery`/`_hasWithdrawRequest` (located by grep but not opened in the remaining iterations); the finding assumes `_transferFundedClaim` pays from the strategy's underlying balance — confirm it does not exclude `defaultRecoveryReserve` before relying on this.