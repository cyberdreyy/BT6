### Title
Instant-withdraw receipts bypass the `lossRecoveryPriceByEpoch` haircut after a lossy `stopEpochWithDuration` - ([File: contracts/strategies/idle/IdleCreditVault.sol](contracts/strategies/idle/IdleCreditVault.sol))

### Summary
The external bug is a wrong-constant / wrong-branch check that makes a state transition behave opposite to intent. The closest analog in this repo is the asymmetric loss handling between the two withdrawal paths: normal withdraw receipts are explicitly haircut via `lossRecoveryPriceByEpoch` when `stopEpochWithDuration` realizes a loss, but instant-withdraw receipts are always paid 1:1 in `claimInstantWithdrawRequest` with no recovery-price check, letting instant requesters exit at par and pushing the crystallized loss onto remaining claimants and tranche holders.

### Finding Description
In `collectWithdrawFunds`, when the borrower under-funds pending withdrawals (`_amount < pendingBasis`), the strategy stores a haircut `lossRecoveryPriceByEpoch[epochNumber] = _amount * RECOVERY_FULL / pendingBasis` and zeroes `pendingWithdraws` (`IdleCreditVault.sol:411-430`). Normal withdrawers are then forced through `_claimLossAdjustedWithdrawRequest`, which multiplies their claim basis by that price (`IdleCreditVault.sol:789-801`), and `requestWithdraw` even reverts if the user tries to open a new request before claiming the haircut receipt (`IdleCreditVault.sol:263-271`).

The instant path has no equivalent guard. `requestInstantWithdraw` records `instantWithdrawsRequests[_user]`, `instantWithdrawsRequestsByEpoch` and `pendingInstantWithdraws` (`IdleCreditVault.sol:356-375`), but nothing keys instant receipts to `lossRecoveryPriceByEpoch`. `claimInstantWithdrawRequest` simply burns the full `instantWithdrawsRequests[_user]` receipt and calls `_transferFundedClaim(_user, amount)` at par (`IdleCreditVault.sol:380-393`). `_transferFundedClaim` only protects `defaultRecoveryReserve`, which is zero outside default finalization (`IdleCreditVault.sol:897-907`), so the check does not stop the payout.

Result: a user whose instant request is only partially funded by `collectInstantWithdrawFunds` (or whose request epoch coincides with a lossy stop) still redeems 1:1 from strategy-held underlying, while normal requesters from the same epoch take the haircut. The overpayment is real underlying that belongs to the funded-claim pool: later claimants' `_transferFundedClaim` calls revert on insufficient balance, and/or the CDO's `getContractValue`-backed NAV is understated.

### Impact Explanation
Direct redistribution of a realized loss: instant-withdraw users recover up to 100% of their receipt while normal withdraw users of the same epoch recover only `lossRecoveryPrice` (e.g., 80% on a 20% loss). The excess paid out is stolen pro-rata from every other funded claimant and active LP — a quantified loss equal to `instantBasis * (1 - lossRecoveryPrice)`, and permanent freezing for the tail claimants whose claims can no longer be funded (transfer reverts on balance).

### Likelihood Explanation
Requires no privileged misbehavior: the attacker is any KYC'd lender with an instant-withdraw receipt outstanding across a `stopEpochWithDuration(_lossAmount)`/`collectInstantWithdrawFunds` sequence, which is an honest manager flow. Triggering only needs a partial loss epoch — a plausible credit event in this product — and the attacker simply calls the normal claim path before the haircut accounting catches up. The missing check is unconditional, not edge-case-dependent.

### Recommendation
Apply the same recovery-price accounting to instant receipts: either include `instantWithdrawsRequests`/`instantWithdrawClaimsByEpoch` in the `collectWithdrawFunds` haircut basis, or record a per-epoch instant recovery price and have `claimInstantWithdrawRequest` pay `amount * recoveryPrice / RECOVERY_FULL` for receipts created in a loss epoch. Add a regression test asserting that instant and normal receipts from the same lossy epoch recover identical ratios.

### Proof of Concept
```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.10;
import "forge-std/Test.sol";

contract InstantClaimSkipsHaircutTest is Test {
    // Setup: fork mainnet, deploy IdleCDOEpochVariant + IdleCreditVault strategy,
    // borrower/manager as configured. attacker = KYC'd AA lender, victim = KYC'd BB lender.

    function test_InstantClaimerPaidAtParWhileNormalClaimerIsHaircut() public {
        // --- buffer phase: both users deposit 100e6 USDC-equivalent ---
        // attacker.depositAA(100e6); victim.depositAA(100e6); // via IdleCreditVault
        // manager.startEpoch(duration); borrower draws funds -> epoch running

        // --- attacker opens an instant request, victim opens a normal request ---
        // attacker: cdo.requestInstantWithdraw? -> strategy.requestInstantWithdraw(50e6, attacker)
        // victim:   cdo.requestWithdraw -> strategy.requestWithdraw(50e6, victim, principal)

        // --- epoch stops with a loss; borrower underfunds ---
        // manager.stopEpochWithDuration(lossAmount, ...) such that
        // collectWithdrawFunds funds only 80% of pendingBasis ->
        // lossRecoveryPriceByEpoch[epoch] = 0.8e18
        // collectInstantWithdrawFunds funds attacker partially (or not at all)

        // --- victim claims: receives 50e6 * 0.8e18 / 1e18 = 40e6 ---
        // uint256 victimOut = cdo.claimWithdrawRequest(victim);
        // assertEq(victimOut, 40e6);

        // --- attacker claims the SAME-epoch instant receipt: receives full 50e6 ---
        // claimInstantWithdrawRequest never consults lossRecoveryPriceByEpoch;
        // _transferFundedClaim only guards defaultRecoveryReserve (== 0 here).
        // cdo.claimInstantWithdrawRequest(attacker);
        // uint256 attackerOut = usdc.balanceOf(attacker) - attackerBalBefore;
        // assertEq(attackerOut, 50e6); // paid at par instead of 40e6

        // --- broken invariant: same-epoch, same-loss receipts pay different ratios ---
        // The extra 10e6 paid to attacker is drained from the funded-claim pool,
        // so a later claimant's claim reverts on ERC20 transfer (insolvency),
        // or active LP NAV is overstated by the same amount.
    }
}
```

Key lines for the PoC harness: the haircut write at `collectWithdrawFunds` (`IdleCreditVault.sol:411-430`), the haircut application only in `_claimLossAdjustedWithdrawRequest` (`IdleCreditVault.sol:789-801`), and the unconditional par payout in `claimInstantWithdrawRequest` (`IdleCreditVault.sol:387-392`).

Caveat: I could not fully trace whether `collectInstantWithdrawFunds`/`getInstantWithdrawFunds` in `IdleCDOEpochVariantPrefunded` always fully prefunds instant receipts before any loss can be realized; if the CDO guarantees instant claims are funded before `stopEpochWithDuration` runs, the exposure narrows to the underfunded/prefunded-partial window and default-epoch overlap. The asymmetry itself — one path haircut, the other never checked — is directly visible in the strategy code.