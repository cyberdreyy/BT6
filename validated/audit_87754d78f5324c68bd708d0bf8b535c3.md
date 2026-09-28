### Title
Stale per-epoch instant-withdraw receipt is dereferenced during default-recovery claiming, enabling double payout from the recovery reserve - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
The CVE analog: a resource handle is left in a stale state after successful use, and a later cleanup/release path dereferences it as if it were still valid. Here, `claimInstantWithdrawRequest` pays out a funded instant receipt but never clears `instantWithdrawsRequestsByEpoch[_user][epoch]` or decrements `instantWithdrawClaimsByEpoch[epoch]`. If a default is later finalized for that same epoch, `_claimDefaultedInstantWithdrawRequest` treats the already-paid receipt as a live defaulted claim and pays it again from `defaultRecoveryReserve`.

### Finding Description
`claimInstantWithdrawRequest` only zeroes the aggregate `instantWithdrawsRequests[_user]`; it leaves the per-epoch basis untouched at `contracts/strategies/idle/IdleCreditVault.sol:380-393`. When `finalizeDefaultRecovery` later runs for that epoch and `pendingInstantWithdraws != 0` (other users' instant requests unfunded), `defaultInstantWithdrawsFinalized` is set and `defaultRecoveryEpoch = epochNumber` (lines 690-696). On the next `claimInstantWithdrawRequest`, `_claimDefaultedInstantWithdrawRequest` reads the stale `instantWithdrawsRequestsByEpoch[_user][defaultEpoch]` and processes it as a defaulted claim (lines 842-856).

A direct replay reverts on the underflow at line 848 (`instantWithdrawsRequests[_user] -= claimBasis` when the aggregate is 0). However, the attacker can first make a fresh `requestInstantWithdraw`, which mints a new 1:1 receipt and re-increments `instantWithdrawsRequests[_user]` (lines 356-375) — that function has no `defaultRecoveryFinalized` gate. If the new request amount covers the stale basis, the stale claim clears, `_burn(_user, claimBasis)` consumes the fresh receipt tokens, and `_transferDefaultRecovery` pays `claimBasis * defaultRecoveryPrice` from the reserve for a receipt that was already paid at par. The fresh per-epoch receipt in `instantWithdrawsRequestsByEpoch[_user][newEpoch]` also remains recorded.

### Impact Explanation
Each execution steals `staleBasis * defaultRecoveryPrice / 1e18` underlying from `defaultRecoveryReserve`, directly reducing payouts owed to legitimate defaulted-epoch claimants (normal withdraw receipts, APR0 receipts, other instant receipts, and post-default requests all draw from the same reserve via `_transferDefaultRecovery`). This breaks the "one receipt one payout" and reserve-isolation invariants. If the attacker controlled a large funded instant receipt in the defaulted epoch (e.g., 1M USDC instant-withdrawn and claimed before default, with defaultRecoveryPrice of 0.5e18), they recover an extra ~500k USDC from the reserve.

### Likelihood Explanation
Requires: (1) attacker completes an instant withdraw in epoch N and claims it funded; (2) epoch N defaults and finalizes with `pendingInstantWithdraws != 0` (some other instant claims unfunded — plausible since instant funding is partial by design, see `_defaultPrefundedInstantReserve`); (3) the CDO still routes a post-default `requestInstantWithdraw` for the attacker to rebuild `instantWithdrawsRequests[_user]` and receipt token balance. Unprivileged tranche holders qualify as the attacker. I could not fully verify the CDO-side gating for instant requests after `defaulted()` in this pass; if `IdleCDOEpochVariant` blocks instant requests once defaulted, the underflow revert makes the stale read a permanent freeze of the attacker's claim path rather than theft — either outcome is a live bug rooted in the same stale-slot cleanup.

### Recommendation
Clear the per-epoch receipt on a successful funded instant claim: in `claimInstantWithdrawRequest`, delete `instantWithdrawsRequestsByEpoch[_user][epochNumber]` (or track the request epoch per user) and decrement `instantWithdrawClaimsByEpoch[epochNumber]` so finalized receipts can never be re-read by `_claimDefaultedInstantWithdrawRequest`. Additionally, gate `requestInstantWithdraw` (or at minimum `_claimDefaultedInstantWithdrawRequest`) so that basis in `instantWithdrawsRequestsByEpoch` for `defaultRecoveryEpoch` cannot exceed what remains unclaimed.

### Proof of Concept
```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.10;
import "forge-std/Test.sol";

// Assumes existing harness deploying IdleCDOEpochVariant + IdleCreditVault with USDC.
contract StaleInstantReceiptTest is Test {
    function test_staleInstantReceiptDoubleClaim() public {
        // 1. Epoch N running. Attacker requests instant withdraw X; manager funds it.
        vm.prank(attacker);
        cdo.withdrawInstant(X); // strategy.requestInstantWithdraw mints receipt to attacker
        fundInstantQueue(X);    // borrower/manager funds; collectInstantWithdrawFunds moves USDC

        // 2. Attacker claims funded receipt at par.
        vm.prank(attacker);
        cdo.claimInstantWithdrawRequest();
        // BUG: instantWithdrawsRequestsByEpoch[attacker][N] still == X
        assertEq(vault.instantWithdrawsRequestsByEpoch(attacker, vault.epochNumber()), X);

        // 3. Other users' instant requests remain unfunded: pendingInstantWithdraws > 0.
        // 4. Borrower defaults; manager finalizes recovery with recoveredAmount < basis.
        finalizeDefault(recoveredAmount); // defaultInstantWithdrawsFinalized == true, defaultEpoch == N

        // 5. Attacker makes a fresh instant request Y >= X to defeat the underflow at
        //    instantWithdrawsRequests[_user] -= claimBasis.
        vm.prank(attacker);
        cdo.withdrawInstant(Y);

        // 6. Claim again: stale basis X is paid a second time from defaultRecoveryReserve.
        uint256 reserveBefore = vault.defaultRecoveryReserve();
        vm.prank(attacker);
        cdo.claimInstantWithdrawRequest();
        assertEq(reserveBefore - vault.defaultRecoveryReserve(), X * vault.defaultRecoveryPrice() / 1e18);
        // Attacker received recovery for a receipt already paid at par; reserve is drained
        // pro-rata away from honest defaulted claimants.
    }
}
```