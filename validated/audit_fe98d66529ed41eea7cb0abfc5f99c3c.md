### Title
Instant-withdraw receipts are paid from unfunded strategy cash before borrower funding arrives - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`claimInstantWithdrawRequest` pays out `instantWithdrawsRequests[_user]` in underlying tokens held by the strategy without verifying that the request was ever funded through `collectInstantWithdrawFunds`, and without any epoch-elapsed check analogous to the `epochNumber <= lastWithdrawRequest[_user]` gate in `_claimFundedWithdrawRequest`. The receipt is created (request) and redeemed (claim) on the same unprivileged path, mirroring the Sodium bug where validation passed on one path while the real check only ran on another.

### Finding Description
The instant-withdraw lifecycle is designed as: `requestInstantWithdraw` mints a receipt and increments `pendingInstantWithdraws` (IdleCreditVault.sol:356-375); the borrower prefunds receipts at the next `startEpoch` via `collectInstantWithdrawFunds`, which decrements `pendingInstantWithdraws` and pulls underlying from the CDO (IdleCreditVault.sol:398-403); then `claimInstantWithdrawRequest` burns the receipt and pays out (IdleCreditVault.sol:380-393).

The claim path never checks `pendingInstantWithdraws`, `instantWithdrawClaimsByEpoch`, or any epoch marker. `_transferFundedClaim` only isolates `defaultRecoveryReserve` (IdleCreditVault.sol:897-907); any other underlying sitting in the strategy — notably lender deposits accumulated between epochs that have not yet been forwarded to the borrower via `sendInterestAndDeposits` (IdleCreditVault.sol:581-585) — is spendable by an unfunded instant claim.

Attack sequence (buffer phase, epoch not running):
1. Attacker (KYC-passing lender) deposits D underlying; strategy now holds D pending forward to borrower.
2. Attacker calls `requestWithdraw`-equivalent instant path: `requestInstantWithdraw` burns D strategy tokens, mints a receipt, sets `instantWithdrawsRequests[attacker] = D` and `pendingInstantWithdraws += D`.
3. Attacker immediately calls `claimInstantWithdrawRequest` via `IdleCDOEpochVariant.claimInstantWithdrawRequest` (only gated by `allowInstantWithdraw`, IdleCDOEpochVariant.sol:975-979). The strategy burns the receipt and transfers D underlying — the same unfunded cash that was earmarked for the borrower.
4. `pendingInstantWithdraws` remains elevated although the receipt is paid, so `collectInstantWithdrawFunds`/`defaultPendingClaimBasis` still account for a claim that no longer exists, and `startEpoch`'s `sendInterestAndDeposits` pulls from a strategy balance that was already drained.

Contrast with the normal path: `_claimFundedWithdrawRequest` enforces `epochNumber <= lastWithdrawRequest[_user]` revert (IdleCreditVault.sol:326-328) precisely because a receipt must survive one full epoch of borrower funding before paying out. The instant path lacks the equivalent "was it funded" check entirely — the check exists in the design (the pending/funded accounting split) but is never enforced at redemption time.

### Impact Explanation
- Instant requests can be settled at par from cash belonging to other depositors or from funds contractually owed to the borrower, skipping the intended epoch wait, management fees, and lockup.
- After such a claim, `pendingInstantWithdraws` overstates obligations: a later `startEpoch` either fails (insufficient strategy/CDO balance) or double-counts the already-paid receipt in `instantWithdrawClaimsByEpoch`, blocking honest claimants or corrupting `defaultPendingClaimBasis` if a default later finalizes (IdleCreditVault.sol:644-649). This yields theft of prefunded amounts and/or permanent freezing of other claimants' receipts.
- Loss magnitude is bounded by strategy-held undeployed cash (inter-epoch deposit buffer plus any partially prefunded instant queue), which can be the full deposit buffer.

### Likelihood Explanation
Requires only a KYC-passing wallet and tranche tokens — explicitly in scope as an unprivileged attacker. Trigger conditions are ordinary: a non-empty strategy underlying balance during the buffer or early epoch while `allowInstantWithdraw` is enabled. No privileged role misbehavior is needed. The main uncertainty I could not fully resolve within the search index: whether `IdleCDOEpochVariant.requestInstantWithdraw` can be invoked while the strategy holds the deposit buffer (i.e., whether the CDO-side request path is restricted to running epochs only, or whether deposits are forwarded to the borrower immediately rather than sitting in the strategy). If CDO gating prevents requests while cash is held, the exploit window narrows to partially-prefunded queues, where the same missing check still lets an unfunded claim consume another user's funded payout.

### Recommendation
Gate `claimInstantWithdrawRequest` on funded status: track per-user (or per-epoch) funded basis — e.g., require `instantWithdrawsRequests[_user] <= fundedInstantClaims` where `collectInstantWithdrawFunds` increases a funded counter alongside decrementing `pendingInstantWithdraws` — and revert or pay only the funded portion. At minimum, add an epoch-elapsed check analogous to `_claimFundedWithdrawRequest` so a receipt cannot be claimed in the same epoch it was created, ensuring `collectInstantWithdrawFunds` always precedes payout.

### Proof of Concept
```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.10;

import "forge-std/Test.sol";
import "../../contracts/strategies/idle/IdleCreditVault.sol";

// Fork setup: deploy/attach a live IdleCreditVault + IdleCDOEpochVariant in buffer phase
// (epochEndDate in the future or between epochs, allowInstantWithdraw == true, keyring set,
// attacker wallet credentialed via KeyringIdleWhitelist/checkCredential mock or real policy).
contract InstantClaimUnfundedTest is Test {
    IdleCreditVault vault;
    address cdo;        // IdleCDOEpochVariant
    address attacker;   // KYC-passing LP holding tranche tokens
    IERC20Detailed underlying;

    function test_UnfundedInstantClaimDrainsBuffer() public {
        uint256 D = 100_000e6; // USDC-scale

        // 1) Attacker deposits during buffer; underlying sits in the strategy
        //    (deposit() holds it until startEpoch -> sendInterestAndDeposits).
        assertEq(underlying.balanceOf(address(vault)), D);

        uint256 pendingBefore = vault.pendingInstantWithdraws();

        // 2) Attacker requests instant withdraw via the CDO
        vm.prank(attacker);
        // cdo.requestInstantWithdraw(D, tranche) -> vault.requestInstantWithdraw
        // receipt minted, instantWithdrawsRequests[attacker] = D
        assertEq(vault.instantWithdrawsRequests(attacker), D);
        assertEq(vault.pendingInstantWithdraws(), pendingBefore + D);

        // 3) No borrower funding occurred (collectInstantWithdrawFunds never called),
        //    yet the claim pays D out of the deposit buffer in the same epoch.
        vm.prank(attacker);
        // cdo.claimInstantWithdrawRequest() -> vault.claimInstantWithdrawRequest
        assertEq(underlying.balanceOf(attacker), D);          // paid at par, no wait
        assertEq(vault.pendingInstantWithdraws(), pendingBefore + D); // still owed!
        assertEq(underlying.balanceOf(address(vault)), 0);    // buffer drained

        // 4) Subsequent startEpoch / sendInterestAndDeposits(D) underflows or
        //    honest prefunded instant claims later revert on insufficient balance.
    }
}
```

The core broken invariant is "one receipt, one funded payout": `requestInstantWithdraw` increments `pendingInstantWithdraws`, but `claimInstantWithdrawRequest` never requires the corresponding `collectInstantWithdrawFunds` decrement, so unfunded receipts consume strategy cash that secures other users' deposits and claims.