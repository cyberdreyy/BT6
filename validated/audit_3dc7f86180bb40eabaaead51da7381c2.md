### Title
Unfunded instant-withdraw receipts are paid from other users' funded claims in `claimInstantWithdrawRequest` - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
A race-condition analog: `claimInstantWithdrawRequest` burns and pays out the *aggregate* `instantWithdrawsRequests[_user]` balance without checking which portion was actually funded by the borrower. A lender can stack a new, unfunded instant-withdraw request on top of an already-funded unclaimed receipt, then claim both at once, draining underlying that belongs to other users' funded claims held in the vault.

### Finding Description
`requestInstantWithdraw` mints strategy-token receipts and increases `instantWithdrawsRequests[_user]` with no requirement that a previously funded receipt be claimed first — unlike the post-default path which explicitly forces old claims to be collected before new requests (IdleCreditVault.sol:247-251). Funding is asynchronous: the borrower prefunds instant withdrawals at epoch start via `collectInstantWithdrawFunds`, which decreases `pendingInstantWithdraws` and pulls underlying into the vault (IdleCreditVault.sol:398-403).

`claimInstantWithdrawRequest` then does:

```
uint256 amount = instantWithdrawsRequests[_user];
_burn(_user, amount);
instantWithdrawsRequests[_user] = 0;
_transferFundedClaim(_user, amount);
```

(IdleCreditVault.sol:387-392)

It pays the full aggregate from vault-held underlying, treating *all* receipts as funded. The shared-state race is between the user's `claimInstantWithdrawRequest` transaction and the honest borrower's/manager's epoch-boundary funding: a receipt requested in epoch N is funded at the N+1 boundary, but a second receipt requested during N+1 is unfunded until the N+2 boundary — yet both are cleared and paid in a single claim.

### Impact Explanation
Concrete sequence (fixed-APR mode with `allowInstantWithdraw`, epoch running):

1. Attacker (KYC-passing lender) requests an instant withdraw of amount A during epoch N.
2. Honest epoch transition: `startEpoch` → borrower prefunds A via `collectInstantWithdrawFunds`; vault now holds A earmarked for the attacker plus other users' funded claims B.
3. During epoch N+1 (still running), attacker requests a second instant withdraw of amount B′ ≤ B. No guard prevents this.
4. Attacker calls `claimInstantWithdrawRequest` → burns A + B′ receipts and `_transferFundedClaim` pays A + B′ from vault balance.
5. Other users' later claims revert on insufficient balance → direct theft of A′ = B′ underlying and insolvency of the funded-claim reserve.

Invariant broken: "one receipt one funded payout" — an unfunded receipt is paid from another user's funded claim. Loss is bounded by the funded-but-unclaimed underlying sitting in the vault, i.e. up to the aggregate of all matured normal/instant claims.

### Likelihood Explanation
Requires `allowInstantWithdraw` enabled and idle funded claims in the vault — both normal operating conditions. The attacker only needs two ordinary user-level calls around an honest epoch transition; no privileged role is involved. Timing constraint (second request must land while epoch is running and before funding) is easily satisfiable since the claim window spans the whole epoch.

### Recommendation
Track funded vs unfunded instant receipts separately (per-epoch funded bitmap or a `fundedInstantWithdraws` counter credited only by `collectInstantWithdrawFunds`), and cap each claim at the funded portion — mirroring the per-epoch accounting already added via `instantWithdrawsRequestsByEpoch`/`instantWithdrawClaimsByEpoch`. Alternatively, revert in `requestInstantWithdraw` when `instantWithdrawsRequests[_user] != 0`, forcing users to claim funded receipts first, consistent with the post-default guard.

### Proof of Concept
Foundry fork PoC sketch:

```solidity
// epoch N running, allowInstantWithdraw = true
vm.prank(attacker);
cdoEpoch.requestInstantWithdraw(A, address(AAtranche)); // receipt A

// honest epoch boundary: borrower prefunds instant withdraws
vm.prank(manager); cdoEpoch.stopEpoch(apr, 0);
vm.prank(manager); cdoEpoch.startEpoch(); // collectInstantWithdrawFunds(A)

// other users also hold funded claims worth B in the vault
// attacker stacks an unfunded request in epoch N+1
vm.prank(attacker);
cdoEpoch.requestInstantWithdraw(B, address(AAtranche)); // receipt B, unfunded

// single claim burns A+B and pays A+B from vault balance
uint256 pre = underlying.balanceOf(attacker);
vm.prank(attacker);
cdoEpoch.claimInstantWithdrawRequest();
assertEq(underlying.balanceOf(attacker) - pre, A + B); // B stolen

// victim's funded claim now underflows / reverts
vm.prank(victim);
vm.expectRevert();
cdoEpoch.claimWithdrawRequest(); // or claimInstantWithdrawRequest
```

Note: I did not fully verify `_transferFundedClaim`'s internal balance cap (IdleCreditVault.sol:897+) — if it clamps the payout to `balance - defaultRecoveryReserve` rather than reverting, the theft still succeeds as long as other users' funded claims remain in the vault, since that reserve exclusion only protects the default-recovery bucket.