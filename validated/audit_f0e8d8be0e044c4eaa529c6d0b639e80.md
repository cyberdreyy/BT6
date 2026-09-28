### Title
Claimed instant-withdraw receipts are never removed from the epoch claim table, so default finalization double-counts paid funds and corrupts the recovery price - ([File: contracts/strategies/idle/IdleCreditVault.sol](contracts/strategies/idle/IdleCreditVault.sol))

### Summary
The kernel bug publishes an object into a per-session table, then an error-path cleanup frees the object without removing the table entry, leaving a stale, still-usable reference. `IdleCreditVault` has the same shape: `requestInstantWithdraw` "publishes" each receipt into `instantWithdrawsRequestsByEpoch[user][epoch]` and `instantWithdrawClaimsByEpoch[epoch]`, but the funded claim path `claimInstantWithdrawRequest` burns the user-side receipt and zeroes only the aggregate `instantWithdrawsRequests[user]` — it never clears either per-epoch entry. The "freed" receipt stays visible to `defaultPendingClaimBasis` and `_defaultPrefundedInstantReserve`, so a later same-epoch default finalization counts already-paid funds again, inflating `defaultRecoveryPrice` toward par and overpaying early claimants until the reserve transfer underflows and bricks all remaining recovery claims.

### Finding Description
`requestInstantWithdraw` writes three ledgers: the user aggregate `instantWithdrawsRequests[_user]`, the per-epoch entry `instantWithdrawsRequestsByEpoch[_user][currentEpoch]`, and the global epoch total `instantWithdrawClaimsByEpoch[currentEpoch]` (lines 366–374).

The funded claim path clears only the aggregate:
```solidity
uint256 amount = instantWithdrawsRequests[_user];
_burn(_user, amount);
instantWithdrawsRequests[_user] = 0;
_transferFundedClaim(_user, amount);
```
(lines 387–392). `instantWithdrawsRequestsByEpoch[user][epoch]` and `instantWithdrawClaimsByEpoch[epoch]` keep the claimed amount forever. By contrast, the defaulted-epoch claim path `_claimDefaultedInstantWithdrawRequest` does clear both epoch entries (lines 847–853), confirming they are meant to track outstanding basis.

Because instant claims are payable inside the same epoch, a user can request an instant withdraw at `epochNumber == E`, have the CDO fund it via `collectInstantWithdrawFunds` (which only decrements `pendingInstantWithdraws`, line 401), and claim — while other users' instant receipts remain unfunded (`pendingInstantWithdraws > 0`).

If the borrower then defaults and `finalizeDefaultRecovery` runs while `epochNumber` is still `E`:

- `defaultPendingClaimBasis` adds `instantWithdrawClaimsByEpoch[E]` (lines 646–648), which still includes the already-paid amount `X`.
- `_defaultPrefundedInstantReserve` computes `instantBasis - pendingInstant` (lines 716–722); the inflated `instantBasis` adds the same phantom `X` to `reserveAmount`, treating the paid-out `X` as still held.
- `recoveryPrice = reserveAmount * RECOVERY_FULL / totalBasis` (line 688) is therefore pushed toward/above par: both numerator and denominator absorb the phantom `X`. This mints extra unbacked strategy tokens to the CDO via `activeFinalNAV` (line 704), understating the crystallized loss for active tranche holders, and sets `defaultRecoveryPrice` higher than the real payout capacity.
- Every subsequent `_transferDefaultRecovery` decrements `defaultRecoveryReserve` (line 915) faster than the real balance supports; when the ERC20 balance is exhausted the `safeTransfer` reverts, permanently bricking `_claimDefaultedWithdrawRequest`, `_claimDefaultedInstantWithdrawRequest`, and `_claimPostDefaultWithdrawRequest` for all remaining claimants.
- Additionally, the already-claimed attacker's stale `instantWithdrawsRequestsByEpoch` entry makes their own future `claimInstantWithdrawRequest` underflow at `instantWithdrawsRequests[_user] -= claimBasis` (line 848), permanently freezing any other funded instant receipts they hold.

The analogous "republish then free without removing the table entry" also exists in `_claimFundedWithdrawRequest`, which leaves `withdrawsRequestsByEpoch` populated (lines 338–345); it is not independently exploitable because `epochNumber` only advances at `stopEpoch`, so `defaultRecoveryEpoch` can never equal a funded normal receipt's stale epoch — but the instant path is reachable within one epoch and is the live instance.

### Impact Explanation
Direct insolvency and permanent freezing with quantifiable loss. Let `X` be instant withdrawals claimed before default in the same epoch while `pendingInstantWithdraws > 0`. At finalization the phantom `X` is added to both `reserveAmount` and `totalBasis`, moving `defaultRecoveryPrice` toward `RECOVERY_FULL` instead of the true ratio. Concretely, with active basis `A`, real pending/instant basis `B`, and real reserve `R`, the true price is `R/(A+B)` but the stored price is `(R+X)/(A+B+X)`. Early claimants (the attacker can be first) are paid at the inflated price; once the actual ERC20 balance runs out, all later claims revert, permanently locking `defaultRecoveryReserve` dust and unpaid recovery in the contract. The `activeFinalNAV` mint at line 704 also overstates CDO NAV by up to `activeBasis * X / (A+B+X)`, unbacked inflation of tranche claim basis.

### Likelihood Explanation
Requires (a) an instant withdraw epoch where the CDO funds only part of the instant queue (`pendingInstantWithdraws > 0` — explicitly contemplated by `_defaultPrefundedInstantReserve` and `defaultInstantWithdrawsFinalized`), (b) any user claims their funded instant receipt in the same epoch (the normal, documented flow), and (c) the borrower defaults before `epochNumber` advances. No privileged misbehavior is needed; the attacker is an ordinary unprivileged tranche holder whose mere claim creates the stale entry. Every honest actor (CDO, manager, borrower default via `stopEpochWithDuration`/default path) follows the documented sequence.

### Recommendation
Mirror the funded-claim cleanup already used for normal receipts: in `claimInstantWithdrawRequest`, after computing `amount`, locate and zero `instantWithdrawsRequestsByEpoch[_user][epochNumber]` (or track the receipt's request epoch so multi-epoch instant receipts clear the right slot) and decrement `instantWithdrawClaimsByEpoch[epoch]` by the claimed amount, exactly as `_claimDefaultedInstantWithdrawRequest` does. This removes the stale table entry at "free" time so `defaultPendingClaimBasis`, `_defaultPrefundedInstantReserve`, and `_claimDefaultedInstantWithdrawRequest` only ever see genuinely outstanding receipts.

### Proof of Concept
Foundry fork PoC sketch (extend `test/foundry/IdleCreditVault.t.sol` helpers):

```solidity
function testStaleInstantEntryInflatesDefaultRecovery() external {
    // running epoch E: userA deposits via AA tranche, userB deposits
    uint256 amtA = 100e6; uint256 amtB = 100e6;
    uint256 trA = _depositWithUser(userA, amtA);
    uint256 trB = _depositWithUser(userB, amtB);
    vm.prank(manager); cdoEpoch.startEpoch();

    // both request instant withdraws in epoch E
    vm.prank(userA); cdoEpoch.requestInstantWithdraw(trA, address(AAtranche));
    vm.prank(userB); cdoEpoch.requestInstantWithdraw(trB, address(AAtranche));
    uint256 epoch = strategy.epochNumber();

    // honest CDO funds only userA-sized portion -> pendingInstantWithdraws > 0
    deal(address(underlying), address(cdoEpoch), trA);
    vm.prank(manager); // getInstantWithdrawFunds/collectInstantWithdrawFunds path for trA
    cdoEpoch.getInstantWithdrawFunds(trA);

    // userA claims (funded) -> stale epoch entries remain
    vm.prank(userA); cdoEpoch.claimInstantWithdrawRequest();
    assertGt(strategy.instantWithdrawsRequestsByEpoch(userA, epoch), 0); // stale "table entry"
    assertGt(strategy.instantWithdrawClaimsByEpoch(epoch), trA);

    // borrower defaults in same epoch; finalize recovery
    // (drive _handleBorrowerDefault + finalizeDefaultRecovery with recovered R)
    ...
    // assert: defaultPendingClaimBasis includes userA's paid trA
    // assert: stored defaultRecoveryPrice > true (R)/(A+B), reserve underflow
    // -> later claimants' claimWithdrawRequest reverts on safeTransfer (frozen funds)
}
```

Key assertions: `instantWithdrawsRequestsByEpoch(userA, epoch)` remains nonzero after a fully-paid claim; `defaultRecoveryPrice` exceeds `reserveAmount_true / totalBasis_true`; a second claimant's recovery claim reverts or pays zero despite positive recorded basis.