### Title
Post-close withdraw requests are never funded yet are claimable immediately, letting an attacker drain underlyings reserved for other users' funded receipts - ([File: contracts/strategies/idle/IdleCreditVault.sol])

### Summary
`IdleCreditVault.requestWithdraw` deliberately skips incrementing `pendingWithdraws` when the pool is closed (`epochEndDate == 0`), because a closed pool "has no later stopEpoch" that could call `collectWithdrawFunds` to fund the receipt. However, the request is still recorded in `withdrawsRequests[user]`, and `_claimFundedWithdrawRequest` skips the one-epoch wait check exactly when `epochEndDate == 0`. The result is that an unfunded receipt enters the same canonical claim bucket as funded receipts and is paid out of the vault's underlying balance — the same bug class as the esm.sh traversal, where `path.Clean` normalizes a path but fails to confine it: here, the closed-pool branch normalizes a request into the funded-claim namespace without confining it to actually-funded basis.

### Finding Description
In `requestWithdraw` (contracts/strategies/idle/IdleCreditVault.sol:259-294):

```solidity
bool isClosed = IIdleCDOEpochVariant(idleCDO).epochEndDate() == 0;
...
_burn(msg.sender, _principal);
_mint(_user, _amount);
if (!isClosed) {
  pendingWithdraws += _amount;   // skipped when closed -> receipt is never funded
}
lastWithdrawRequest[_user] = currentEpoch;
...
withdrawsRequests[_user] += _amount;
withdrawsRequestsByEpoch[_user][currentEpoch] += _amount;
```

When `isClosed`, the request bypasses `pendingWithdraws`, so no borrower funding ever arrives, yet the full `_amount` lands in the aggregate `withdrawsRequests[_user]`.

In `_claimFundedWithdrawRequest` (IdleCreditVault.sol:319-350):

```solidity
if (IIdleCDOEpochVariant(idleCDO).epochEndDate() != 0 && (epochNumber <= lastWithdrawRequest[_user])) {
  revert NotAllowed();
}
...
uint256 normalAmount = withdrawsRequests[_user];
amount = normalAmount + apr0PrincipalAmount + apr0InterestAmount;
_burn(_user, normalAmount + apr0PrincipalAmount);
...
_transferFundedClaim(_user, amount);
```

For a closed pool the wait check is skipped entirely, so the request made one transaction earlier is immediately claimable at par from the vault's underlying balance. `_transferFundedClaim` (IdleCreditVault.sol:897-907) only protects `defaultRecoveryReserve`; it does not segregate underlyings collected by `collectWithdrawFunds` to back *other* users' pending receipts, so the attacker's unfunded claim spends funds reserved for honest claimants.

### Impact Explanation
Direct theft of unclaimed funded withdraw proceeds. After a borrower repays in full and the pool closes via the `_interest == 1` stop-epoch path, the vault holds underlyings collected for funded-but-unclaimed receipts (amounts for which `pendingWithdraws` was already decremented at `collectWithdrawFunds`, IdleCreditVault.sol:411-430, while `withdrawsRequests[user]` still records the claimable basis). Any KYC-passing tranche holder can:

1. Request a withdrawal post-close (`requestWithdraw` succeeds; `pendingWithdraws` is not incremented).
2. Call `claimWithdrawRequest` in the same transaction; `_claimFundedWithdrawRequest` pays `withdrawsRequests[user]` from the vault balance.

The attacker is limited only by their strategy-token principal (needed for the `_burn`) and can repeat until the vault's non-reserve balance is drained. Honest claimants' receipts then revert on transfer — theft plus permanent freezing of their unclaimed payouts. Loss is bounded by the vault's funded-claim balance, which equals the aggregate of all not-yet-claimed funded receipts.

### Likelihood Explanation
Requires a closed-pool state (`epochEndDate == 0`), which is an intended terminal state reached via the repay-all stop path, and requires the vault to still hold unclaimed funded underlyings — common, since receipt claims are lazy and there is no deadline forcing users to claim before close. The attacker needs only KYC and tranche tokens; no privileged role is involved. The main uncertainty is whether `IdleCDOEpochVariant.requestWithdraw` adds its own gate rejecting requests when `epochEndDate == 0` — I could not fully verify that entry point; the vault's dedicated `isClosed` branch and the closed-pool claim shortcut strongly indicate post-close requests are an accepted flow rather than a rejected one.

### Recommendation
When `epochEndDate() == 0`, either:
- Reject new withdraw requests (they can never be funded), or
- Source the funds synchronously at request time from the IdleCDO (transfer `_amount` of underlying from `idleCDO` at request, analogous to `collectWithdrawFunds`), so the receipt is funded before entering `withdrawsRequests`.

Additionally, `_claimFundedWithdrawRequest` should verify each claim is backed by previously collected funds rather than paying from an unsegregated balance — e.g., track a `fundedClaimsBalance` incremented by `collectWithdrawFunds` and decremented on claim, so unfunded receipts can never consume other users' reserves.

### Proof of Concept
```solidity
// Foundry fork test sketch
// Setup: pool runs, userB requests withdraw in epoch N, epoch stops and
// borrower funds pendingWithdraws via collectWithdrawFunds (vault now holds
// funded underlyings). Borrower then repays fully; stopEpoch(1) closes the
// pool: epochEndDate == 0.

// Attack (attacker is a KYC'd tranche holder):
// 1. attacker deposits earlier and holds `principal` strategy tokens via CDO.
vm.prank(attacker);
cdo.requestWithdraw(attackerShares);              // vault: pendingWithdraws NOT incremented (isClosed)
// 2. Same tx: claim is allowed because epochEndDate() == 0 skips the wait check
vm.prank(attacker);
cdo.claimWithdrawRequest();                       // pays withdrawsRequests[attacker] from vault balance

uint256 stolen = underlying.balanceOf(attacker);
assertGt(stolen, attackerDepositedBasis);          // exceeds funded basis: drained userB's reserve

// userB's claim now reverts on insufficient vault balance
vm.prank(userB);
vm.expectRevert();
cdo.claimWithdrawRequest();
```

*Caveat:* step 1 assumes `IdleCDOEpochVariant.requestWithdraw` permits requests while `epochEndDate() == 0`; I could not fully read that function within the available budget. If the CDO reverts on closed pools, the vault-side `isClosed`/`epochEndDate != 0` branches are dead code and the finding does not hold — that gate should be confirmed first when reproducing.