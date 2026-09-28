### Title
Post-default instant withdraw requests drain `defaultRecoveryReserve` at the finalized haircut — missing `defaultRecoveryFinalized` guard in `requestInstantWithdraw` — (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`requestWithdraw` explicitly guards the post-default state (reverting unless the user has no open receipts and routing the request into `postDefaultRequests`, lines 247-258), but `requestInstantWithdraw` performs no equivalent check. Like CVE-2024-53063, where `dvb_device_open()` relied on implicit checks done by an earlier registration path, `requestInstantWithdraw` relies on the epoch number never colliding with `defaultRecoveryEpoch` — an assumption that is false after `finalizeDefaultRecovery`, because `epochNumber` is frozen at the defaulted epoch.

### Finding Description
In `contracts/strategies/idle/IdleCreditVault.sol`:

- `finalizeDefaultRecovery` sets `defaultRecoveryFinalized = true`, `defaultRecoveryEpoch = epochNumber`, and `defaultInstantWithdrawsFinalized = pendingInstantWithdraws != 0` (lines 690-696). `epochNumber` is only incremented in `deposit` during `stopEpoch` (line 610), which never runs again after default, so `epochNumber` remains equal to `defaultRecoveryEpoch`.
- `requestInstantWithdraw` (lines 356-375) unconditionally executes `instantWithdrawsRequestsByEpoch[_user][epochNumber] += _amount`, i.e. it writes the new request into `instantWithdrawsRequestsByEpoch[user][defaultRecoveryEpoch]`, and mints the user a 1:1 strategy-token receipt.
- `claimInstantWithdrawRequest` (lines 380-393) first calls `_claimDefaultedInstantWithdrawRequest`, which reads `instantWithdrawsRequestsByEpoch[_user][defaultRecoveryEpoch]` (line 844) and pays `claimBasis * defaultRecoveryPrice / RECOVERY_FULL` out of `defaultRecoveryReserve` via `_transferDefaultRecovery` (lines 853-855, 912-917).

A post-default instant request is therefore paid from the isolated recovery reserve that was sized, during finalization, only for pre-default claimants (`reserveAmount = _recoveredAmount + prefundedReserve + defaultRecoveryReserve` split over `totalBasis`, lines 686-688). Every post-default instant claim decrements `defaultRecoveryReserve` (line 915) for basis that was never part of `totalBasis`, so legitimate defaulted-epoch claimants at the tail of the queue either get less than `defaultRecoveryPrice` or have their claim revert on an empty reserve — direct insolvency of the recovery distribution.

### Impact Explanation
Each post-default instant request of size `A` consumes `A * defaultRecoveryPrice` of the reserve earmarked for pre-default receipt holders and instant claimants. For a reserve `R` and recovery price `p`, an attacker (any tranche-token holder requesting an instant withdraw through the CDO) can burn `R / p` of their own position to fully drain the reserve, permanently freezing or haircutting the remaining unclaimed recovery of all other defaulted-epoch claimants. The invariant "one receipt, one haircut-adjusted payout, reserve only for basis accounted at finalization" is broken.

### Likelihood Explanation
Requires `pendingInstantWithdraws != 0` at `finalizeDefaultRecovery` so `defaultInstantWithdrawsFinalized` is true and the `_claimDefaultedInstantWithdrawRequest` branch is reachable (line 382). This happens whenever unfunded instant requests exist at the defaulted epoch, a normal state when a borrower defaults mid-epoch with a partially covered instant queue. The attacker's claim path also needs the CDO to route post-default instant requests; nothing in `requestInstantWithdraw` itself blocks them, and the strategy has no `defaulted()` check in this entry point.

Note: I could not fully verify the IdleCDOEpochVariant side that forwards user instant-withdraw calls post-default; if the CDO hard-blocks instant requests once `defaulted()` is true, the external reachability narrows, but the strategy-level guard is still missing relative to `requestWithdraw`, and any CDO path (including the prefunded variant) that still emits `requestInstantWithdraw` reproduces the drain.

### Recommendation
Mirror the `requestWithdraw` treatment in `requestInstantWithdraw`: when `defaultRecoveryFinalized` is true, either revert or record the receipt under a post-default bucket that is paid 1:1 from newly funded underlying rather than `defaultRecoveryReserve`. At minimum, add `if (defaultRecoveryFinalized) revert NotAllowed();` (or write to a separate epoch key) so post-default instant receipts can never alias `instantWithdrawsRequestsByEpoch[user][defaultRecoveryEpoch]`.

### Proof of Concept
```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.10;

import "forge-std/Test.sol";
import {IdleCreditVault} from "../contracts/strategies/idle/IdleCreditVault.sol";

contract PostDefaultInstantDrainTest is Test {
    // fork: mainnet, pinned block before fix
    // Setup: vault with an epoch running; users A (victim) and B (attacker)
    // hold tranche positions; A has an unfunded instant withdraw request.

    function test_PostDefaultInstantRequestDrainsRecoveryReserve() public {
        // 1. Epoch running. Victim A calls requestInstantWithdraw(A_amt) via CDO.
        //    Borrower funds only part of the instant queue => pendingInstantWithdraws > 0.
        // 2. Borrower defaults (honest). Owner calls _handleBorrowerDefault;
        //    CDO calls finalizeDefaultRecovery(recovered, source).
        //    => defaultRecoveryFinalized = true,
        //       defaultRecoveryEpoch = epochNumber (unchanged forever after),
        //       defaultInstantWithdrawsFinalized = true,
        //       defaultRecoveryReserve = R, defaultRecoveryPrice = p < 1e18.

        // 3. Attacker B calls requestInstantWithdraw(A_amt) via the CDO.
        //    Guard expected: revert. Actual: succeeds and writes
        //    instantWithdrawsRequestsByEpoch[B][defaultRecoveryEpoch] += A_amt
        //    even though B was never part of defaultPendingClaimBasis().

        // 4. B calls claimInstantWithdrawRequest(B).
        //    _claimDefaultedInstantWithdrawRequest pays B: A_amt * p / 1e18
        //    straight from defaultRecoveryReserve.

        // 5. Victim A calls claimInstantWithdrawRequest(A).
        //    _transferDefaultRecovery underflows / reserve is depleted =>
        //    A's legitimate defaulted-epoch claim is frozen or reduced below p.

        // Assert postconditions:
        // assertEq(vault.defaultRecoveryReserve(), expectedReserve - drain);
        // assertLt(payoutToA, A_claimBasis * p / 1e18);
    }
}
```