### Title
Funded instant-withdraw receipts keep their per-epoch claim basis, enabling a second payout after default finalization - ([File: contracts/strategies/idle/IdleCreditVault.sol])

### Summary
`IdleCreditVault.claimInstantWithdrawRequest` pays a funded instant-withdraw receipt and zeroes `instantWithdrawsRequests[_user]`, but never clears the per-epoch basis `instantWithdrawsRequestsByEpoch[_user][epoch]` nor decrements `instantWithdrawClaimsByEpoch[epoch]` / `pendingInstantWithdraws`. If the pool later defaults and recovery is finalized while other instant receipts remain unfunded, the same already-paid receipt is counted again in `totalBasis` (inflating the recovery reserve math via `_defaultPrefundedInstantReserve`) and the attacker can claim it a second time through `_claimDefaultedInstantWithdrawRequest` at `defaultRecoveryPrice`. This is the same bug class as the reference finding: one deposit/claim is validated twice against the same pool of funds because per-epoch and aggregate accounting are updated independently with no aggregate consistency check.

### Finding Description
In `requestInstantWithdraw` the strategy burns the CDO's strategy tokens, mints a receipt to the user, and records the claim in three places: `instantWithdrawsRequests[_user]`, `instantWithdrawsRequestsByEpoch[_user][currentEpoch]`, and `instantWithdrawClaimsByEpoch[currentEpoch]` plus `pendingInstantWithdraws` (IdleCreditVault.sol:356-375).

`claimInstantWithdrawRequest` (IdleCreditVault.sol:380-393) pays `instantWithdrawsRequests[_user]` at par via `_transferFundedClaim` but only resets the aggregate counter:

```solidity
uint256 amount = instantWithdrawsRequests[_user];
_burn(_user, amount);
instantWithdrawsRequests[_user] = 0;
_transferFundedClaim(_user, amount);
```

`instantWithdrawsRequestsByEpoch[_user][epochNumber]`, `instantWithdrawClaimsByEpoch[epochNumber]`, and `pendingInstantWithdraws` are left unchanged. By contrast, the default-claim path `_claimDefaultedInstantWithdrawRequest` (IdleCreditVault.sol:842-856) does clear all three — confirming the per-epoch basis is the authoritative record for post-default claims.

Two independent consumers then read the stale basis against the same underlying balance:

1. `_defaultPrefundedInstantReserve` (IdleCreditVault.sol:716-723) computes already-held backing as `instantWithdrawClaimsByEpoch[epochNumber] - pendingInstantWithdraws`. Because a paid claim never reduced `instantWithdrawClaimsByEpoch`, the paid-out amount is counted as still-reserved cash inside `finalizeDefaultRecovery` (line 685-688), inflating `reserveAmount`/`recoveryPrice` with phantom funds.
2. After `finalizeDefaultRecovery` sets `defaultRecoveryFinalized` and `defaultInstantWithdrawsFinalized` (true whenever `pendingInstantWithdraws != 0`, i.e., some *other* user's receipt stayed unfunded — line 696), `_claimDefaultedInstantWithdrawRequest` reads `instantWithdrawsRequestsByEpoch[_user][defaultRecoveryEpoch]`, which was never cleared, and pays `(claimBasis * defaultRecoveryPrice) / RECOVERY_FULL` from `defaultRecoveryReserve` (lines 844-855). It even decrements `pendingInstantWithdraws` again for the same basis.

Attack sequence (epoch phase: instant-withdraw-enabled / close-pool or prefunded mode, then borrower default):

1. Attacker (KYC-passed lender, unprivileged) calls `requestInstantWithdraw` in epoch N; the CDO funds it via `collectInstantWithdrawFunds`.
2. Attacker claims at par via `claimInstantWithdrawRequest` — legitimate payout #1. Per-epoch basis remains.
3. Another user's instant request stays unfunded (`pendingInstantWithdraws != 0`). Borrower fails to repay; `_handleBorrowerDefault` runs and owner/manager calls `finalizeDefaultRecovery`.
4. `_defaultPrefundedInstantReserve` counts the attacker's already-paid amount as reserve; `totalBasis` includes it via `defaultPendingClaimBasis` (line 646-648).
5. Attacker calls `claimInstantWithdrawRequest` again → `_claimDefaultedInstantWithdrawRequest` pays `claimBasis * defaultRecoveryPrice` — payout #2 on the same receipt, drawn from `defaultRecoveryReserve` owed to honest claimants.

### Impact Explanation
Direct theft of default-recovery funds: the attacker is paid twice for one receipt (once at par, once at the recovery ratio), and the phantom basis both overstates `defaultRecoveryPrice` (so other claimants' entitlements exceed actual cash) and drains `defaultRecoveryReserve`. Later legitimate claimants' `_transferDefaultRecovery` calls either pay less than entitled or revert on insufficient balance — permanent freezing/theft of unclaimed recovery proportional to the attacker's double-claimed amount (up to the full instant-withdraw size the attacker controls, bounded only by the pool's instant-liquidity in that epoch). This breaks the "one receipt, one payout" and solvency invariants.

### Likelihood Explanation
Requires: (a) instant withdrawals enabled (`allowInstantWithdraw`, set in request-all-funds/close-pool mode or prefunded configurations), (b) at least one instant receipt funded and claimed in the epoch, (c) another instant receipt left unfunded so `defaultInstantWithdrawsFinalized` is set, and (d) a borrower default followed by `finalizeDefaultRecovery`. Default is an honest-manager/borrower-driven event, not attacker-controlled, but the dormant stale basis persists indefinitely once created — any funded-then-claimed instant receipt in an epoch that later partially defaults is a live double-claim. No privileged collusion is needed by the attacker.

### Recommendation
In `claimInstantWithdrawRequest`, clear the per-epoch accounting for the claim epoch the same way `_claimDefaultedInstantWithdrawRequest` does: subtract the claimed amount from `instantWithdrawsRequestsByEpoch[_user][epochNumber]` and `instantWithdrawClaimsByEpoch[epochNumber]` (and reconcile `pendingInstantWithdraws` for the unfunded-remainder semantics), so that a paid receipt has zero residual basis. Additionally, in `finalizeDefaultRecovery`/`_defaultPrefundedInstantReserve`, derive the funded reserve from actual held underlying attributable to *outstanding* receipts rather than from `instantWithdrawClaimsByEpoch` minus `pendingInstantWithdraws`, or at minimum enforce `instantWithdrawClaimsByEpoch[epoch] == sum of unclaimed per-user basis`.

### Proof of Concept
```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.10;

import "forge-std/Test.sol";
// Assumes the repo's IdleCreditVault/IdleCDOEpochVariant Foundry base harness
// (test/foundry/IdleCreditVault.t.sol helpers: _startEpochAndCheckPrices,
//  _checkDefault, manager/owner/borrower actors, ONE_SCALE, idleCDO, cdoEpoch, strategy).

contract InstantReceiptDoubleClaimTest is Test /*, IdleCreditVaultBase */ {
    // Scenario sketch (adapted to repo harness):
    //
    // 1. Pool in close-pool / instant-withdraw-enabled mode
    //    (cdoEpoch.allowInstantWithdraw() == true; epochEndDate == 0 after
    //     stopEpoch(.., _interest = 1) or prefunded configuration).
    // 2. Attacker (whitelisted EOA) deposits AA, then requests instant withdraw
    //    of amount X via idleCDO.requestInstantWithdraw -> strategy.requestInstantWithdraw.
    //    Assert: instantWithdrawsRequestsByEpoch[attacker][epoch] == X.
    // 3. CDO funds the request: strategy.collectInstantWithdrawFunds(X) path via
    //    getInstantWithdrawFunds; attacker calls claimInstantWithdrawRequest.
    //    Assert: attacker received X underlying; instantWithdrawsRequests[attacker] == 0.
    //    BUG PRECONDITION: assert instantWithdrawsRequestsByEpoch[attacker][epoch] == X
    //                      and instantWithdrawClaimsByEpoch[epoch] still includes X.
    // 4. A second user requests instant withdraw Y that remains UNFUNDED
    //    (pendingInstantWithdraws == Y > 0).
    // 5. Borrower fails to repay -> _handleBorrowerDefault -> defaulted == true.
    //    Owner/manager calls strategy.finalizeDefaultRecovery(recovered, source).
    //    Assert: defaultInstantWithdrawsFinalized == true and
    //    defaultRecoveryPrice was computed with attacker's X in totalBasis
    //    (via defaultPendingClaimBasis/instantWithdrawClaimsByEpoch) and in
    //    prefundedReserve (via _defaultPrefundedInstantReserve).
    // 6. Attacker calls cdoEpoch.claimInstantWithdrawRequest(attacker) again.
    //    _claimDefaultedInstantWithdrawRequest reads stale basis X and pays
    //    X * defaultRecoveryPrice / 1e18 from defaultRecoveryReserve.
    //    Assert: attacker total received == X + X*recoveryPrice (double payout),
    //    and subsequent honest defaulted claims revert or are underpaid
    //    (defaultRecoveryReserve exhausted early).

    function testFundedInstantReceiptReclaimedAfterDefault() public {
        // Setup identical to test/foundry/IdleCreditVault.t.sol close-pool flows:
        // - idleCDO.depositAA by attacker and honest user
        // - enable instant withdraw path used by existing instant-withdraw tests
        // - follow steps 2-6 above with vm.prank(attacker/honest/owner/borrower)
        // Expected invariant violation:
        //   sum(claims paid) > underlying actually reserved + collected
    }
}
```

Caveat: I verified the stale-basis mechanism statically (funded claim path at IdleCreditVault.sol:380-393 clears none of the per-epoch counters, while the default claim path at 842-856 and the reserve accounting at 685-688/716-723 both re-read them). I could not fully trace the `IdleCDOEpochVariant` side that enables instant withdrawals (`allowInstantWithdraw`/`getInstantWithdrawFunds`) within the available iterations, so the PoC should confirm that a funded-then-claimed instant receipt can coexist with `pendingInstantWithdraws != 0` in the epoch that later defaults; if the CDO only enables instant requests when all are fully funded simultaneously, severity drops to the accounting-dilution (recovery-price inflation) half of the finding.