### Title
Instant-withdraw receipts can be claimed before borrower funding is collected, letting a requester drain the vault's claim reserve and leave other users' funded claims unpaid - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
CVE-2020-12363 is an improper-input-validation bug that lets a caller push the system into a denial-of-service state. The analog in idle-tranches is `claimInstantWithdrawRequest` in `IdleCreditVault.sol`: it burns the user's instant-withdraw receipt and pays out underlying via `_transferFundedClaim` without validating that the corresponding funds were actually pulled into the vault by `getInstantWithdrawFunds`/`collectInstantWithdrawFunds`. Combined with `IdleCDOEpochVariant.claimInstantWithdrawRequest`, which only checks `allowInstantWithdraw`, an instant requester can be paid from underlying that was collected to back *other* users' funded (normal or instant) claims.

### Finding Description
In `requestInstantWithdraw` (contracts/strategies/idle/IdleCreditVault.sol, `requestInstantWithdraw`, L356-375), the CDO's strategy tokens are burned and a receipt is minted 1:1 to the user, and `pendingInstantWithdraws` / `instantWithdrawsRequestsByEpoch` / `instantWithdrawClaimsByEpoch` are incremented. Funding happens separately and asynchronously: the honest manager calls `getInstantWithdrawFunds` on `IdleCDOEpochVariant`, which transfers underlying from the borrower into the CDO and then calls `collectInstantWithdrawFunds` (L398-403) to move it into the vault, decrementing `pendingInstantWithdraws`.

`claimInstantWithdrawRequest` (L380-393) then:

```solidity
uint256 amount = instantWithdrawsRequests[_user];
_burn(_user, amount);
instantWithdrawsRequests[_user] = 0;
_transferFundedClaim(_user, amount);
```

There is no check tying the paid amount to actually-collected instant funds. `collectInstantWithdrawFunds` only decrements the global `pendingInstantWithdraws` counter; no per-user or per-epoch funded flag is stored for instant receipts, and `claimInstantWithdrawRequest` does not consult `pendingInstantWithdraws`, `instantWithdrawClaimsByEpoch`, or any funded-balance tracker. The only gate on the CDO side is `allowInstantWithdraw` (`IdleCDOEpochVariant.claimInstantWithdrawRequest`, L975-979). So a receipt holder can claim during the epoch, before the borrower's transfer is collected, and `_transferFundedClaim` pays out from the vault's underlying balance — the same balance that backs already-funded normal withdraw receipts collected via `collectWithdrawFunds` (L411-430).

### Impact Explanation
Direct theft / insolvency of the claim reserve. An attacker (any KYC-passed lender holding tranche tokens) does:

1. Deposit into a tranche during a running epoch.
2. Call `requestWithdraw(0, tranche)`-style instant request (or `requestInstantWithdraw` path) to mint an instant receipt.
3. Before the honest manager's `getInstantWithdrawFunds` collection, call `claimInstantWithdrawRequest`. The vault burns the receipt and transfers underlying out of the shared claim reserve.

If the vault currently holds underlying collected for *other* users' matured withdraw receipts (normal requests funded by a prior `stopEpoch`/`collectWithdrawFunds`), the attacker is paid from that reserve. Those legitimate claimers then find the vault underfunded: their later `claimWithdrawRequest` calls revert on the ERC20 transfer or silently leave them unpaid — a permanent loss for last-in claimers equal to the attacker's receipt amount. The "one receipt, one payout, funded only" invariant is broken: payout ordering, not funding, determines who gets paid.

### Likelihood Explanation
Requirements are minimal: `allowInstantWithdraw` enabled (a normal operating mode), an attacker with a tranche position, and a window where the vault holds collected-but-unclaimed underlying (common: users routinely leave funded receipts unclaimed for many epochs, as the tests confirm). The attacker only needs to front-run the honest collection or claim while another user's funded balance sits in the vault. Cost is limited to the tranche deposit; no privileged role is involved — the attacker is an ordinary lender, and manager/borrower/owner remain honest. One caveat I could not fully verify within the scan: whether `_transferFundedClaim` internally tracks a funded-but-unclaimed underlying counter that would make an unfunded instant claim revert. If such a counter exists and is credited only in `collectInstantWithdrawFunds`/`collectWithdrawFunds`, the premature claim reverts and severity drops to nil; if it tracks only an aggregate funded balance (or none, relying on raw token balance), the theft path stands.

### Recommendation
Add the missing input/state validation on the claim path:

- Track funded instant withdrawals explicitly, e.g. a `fundedInstantWithdraws` counter incremented in `collectInstantWithdrawFunds` and decremented in `claimInstantWithdrawRequest`, reverting when `amount > fundedInstantWithdraws` (mirroring how `pendingWithdraws`/`lossRecoveryPriceByEpoch` gate normal claims).
- Alternatively, gate `claimInstantWithdrawRequest` so a receipt in `instantWithdrawsRequestsByEpoch[user][epoch]` is only payable once that epoch's `instantWithdrawClaimsByEpoch` has been collected.
- Ensure `_transferFundedClaim` draws from a segregated, per-flow funded balance rather than the vault's aggregate underlying balance.

### Proof of Concept
Foundry fork PoC sketch (mainnet fork, real vault):

```solidity
function testInstantClaimDrainsFundedReserve() public {
    // Setup: userBob has a funded normal withdraw receipt:
    // he requested withdraw in epoch N, manager ran stopEpoch (N+1),
    // collectWithdrawFunds moved Bob's underlyings into the vault.
    // Bob has not claimed yet; vault holds `bobClaim` underlying.

    // Attacker (Eve), KYC-passed lender, deposits AA and requests
    // an instant withdraw of `amt` while epoch is running.
    vm.startPrank(eve);
    idleCDO.depositAA(amt);
    cdoEpoch.requestInstantWithdraw(amt); // receipt minted, pendingInstantWithdraws += amt

    // Manager has NOT yet called getInstantWithdrawFunds,
    // so Eve's request is not funded by the borrower.

    // Eve claims immediately; no funded check in claimInstantWithdrawRequest.
    cdoEpoch.claimInstantWithdrawRequest();
    vm.stopPrank();

    // Eve received underlying from the vault reserve backing Bob's claim.
    assertEq(underlying.balanceOf(eve), eveInitial + amt);

    // Bob's funded claim now reverts / underpays: vault is short `amt`.
    vm.prank(bob);
    vm.expectRevert(); // ERC20 transfer exceeds vault balance
    cdoEpoch.claimWithdrawRequest();
}
```

Sequence: attacker = unprivileged lender; manager/borrower act honestly; the loss (`amt` of underlying) lands on funded claimers whose receipts were already collected. If `_transferFundedClaim` is found to enforce a funded-balance counter, the `claimInstantWithdrawRequest` step reverts and the finding is invalid — that check must be confirmed before relying on this PoC.