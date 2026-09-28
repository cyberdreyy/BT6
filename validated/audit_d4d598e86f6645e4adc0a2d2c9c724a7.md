# Double-claim of instant-withdraw receipts: `claimInstantWithdrawRequest` never clears `instantWithdrawsRequestsByEpoch`, so the same receipt is paid again at the default-recovery haircut — (`contracts/strategies/idle/IdleCreditVault.sol`)

### Summary
The credit vault has two payout paths for the same instant-withdraw receipt — the funded claim (`claimInstantWithdrawRequest`, line 380) and the defaulted-epoch recovery claim (`_claimDefaultedInstantWithdrawRequest`, line 842). The funded path zeroes only the aggregate `instantWithdrawsRequests[_user]` counter but leaves `instantWithdrawsRequestsByEpoch[_user][epoch]` untouched. If the borrower defaults in the same epoch, `finalizeDefaultRecovery` sets `defaultRecoveryEpoch = epochNumber` and the same user can claim the identical receipt a second time at `defaultRecoveryPrice`, paid out of the shared recovery reserve. This mirrors the EigenPod bug where `withdrawBeforeRestaking` did not zero `nonBeaconChainETHBalanceWei`, letting `withdrawNonBeaconChainETHBalanceWei` pay the same balance twice.

### Finding Description
`requestInstantWithdraw` records the receipt in three places (lines 366-374):
- `instantWithdrawsRequests[_user]`
- `instantWithdrawsRequestsByEpoch[_user][epochNumber]`
- `instantWithdrawClaimsByEpoch[epochNumber]`

When the request is funded and claimed, `claimInstantWithdrawRequest` burns the receipt tokens and pays `instantWithdrawsRequests[_user]`, clearing only that aggregate:

```solidity
uint256 amount = instantWithdrawsRequests[_user];
_burn(_user, amount);
instantWithdrawsRequests[_user] = 0;
_transferFundedClaim(_user, amount);
```

It never touches `instantWithdrawsRequestsByEpoch[_user][epoch]` or `instantWithdrawClaimsByEpoch[epoch]`.

On default finalization, `_claimDefaultedInstantWithdrawRequest` reads exactly the stale per-epoch entry:

```solidity
claimBasis = instantWithdrawsRequestsByEpoch[_user][defaultEpoch];
...
_burn(_user, claimBasis);
_transferDefaultRecovery(_user, (claimBasis * defaultRecoveryPrice) / RECOVERY_FULL);
```

The only gate is `defaultInstantWithdrawsFinalized = pendingInstantWithdraws != 0` (line 696), which is satisfied whenever *any* instant request in that epoch remained unfunded at finalization — it does not require the caller's own receipt to be unfunded. `defaultRecoveryEpoch` is set to the current `epochNumber` (line 693), the same epoch key under which the attacker's already-claimed receipt is still recorded.

Attack sequence (running epoch N, instant withdraws enabled):
1. Attacker (KYC'd lender) calls `requestInstantWithdraw(X)`. Receipt recorded under epoch N.
2. Borrower funds part of the instant queue at `startEpoch`/`collectInstantWithdrawFunds` — enough to cover the attacker's X but leaving another user's request unfunded (`pendingInstantWithdraws > 0`).
3. Attacker calls `claimInstantWithdrawRequest` → receives X underlying; `instantWithdrawsRequestsByEpoch[attacker][N]` still equals X.
4. Borrower defaults at `stopEpoch`; `finalizeDefaultRecovery` runs with `defaultRecoveryEpoch = N` and `defaultInstantWithdrawsFinalized = true` because the other user's request is unfunded.
5. Attacker calls `claimWithdrawRequest`/`claimInstantWithdrawRequest` → `_claimDefaultedInstantWithdrawRequest` pays `X * defaultRecoveryPrice / RECOVERY_FULL` from the recovery reserve, on a receipt that was already fully paid and whose strategy tokens were already burned. The second `_burn(_user, claimBasis)` either burns other receipts or is a no-op if balance is zero is not enforced — it will burn whatever strategy tokens the user holds, but the payout is unconditional on the claim basis.

The invariant "one receipt, one payout" is broken, and `defaultRecoveryReserve` is drained by a basis that was never actually defaulted.

### Impact Explanation
Direct theft from the default-recovery reserve, quantified as `X * defaultRecoveryPrice / RECOVERY_FULL` per already-paid receipt. Every other defaulted-epoch claimant (normal withdraws, instant withdraws, active LPs via `defaultBBNav`) is diluted by that amount. The attacker controls X up to their deposited balance, so the theft scales linearly with their position and can be repeated across multiple epochs/accounts.

### Likelihood Explanation
Requires: instant withdraws enabled (`allowInstantWithdraw`), a partial instant-queue funding followed by a borrower default in the same epoch, and a nonzero recovery price. Defaults and partial prefunding are anticipated states explicitly handled by the code (`_defaultPrefundedInstantReserve`, `defaultInstantWithdrawsFinalized`), so this is a realistic rather than corner-case path. The attacker only needs to be an allowed wallet.

### Recommendation
In `claimInstantWithdrawRequest`, clear `instantWithdrawsRequestsByEpoch[_user][epochNumber]` (and decrease `instantWithdrawClaimsByEpoch[epochNumber]`) for any settled portion, mirroring how `_clearWithdrawClaimForEpoch` cleans up `withdrawsRequestsByEpoch` for normal requests. Alternatively, make `_claimDefaultedInstantWithdrawRequest` deduct only the still-unclaimed portion (i.e., min of per-epoch basis and remaining `instantWithdrawsRequests`), or track funded-vs-pending instant claims per epoch.

### Proof of Concept
Foundry fork test sketch (in `test/foundry/IdleCreditVault.t.sol` style):

```solidity
function testInstantDoubleClaimAfterDefault() external {
    // epoch running, allowInstantWithdraw = true
    _depositWithUser(attacker, 10_000e6, true);
    _depositWithUser(victim, 10_000e6, true);
    _startEpochAndCheckPrices(0);

    vm.prank(attacker); cdoEpoch.requestInstantWithdraw(5_000e6, AAtranche);
    vm.prank(victim);   cdoEpoch.requestInstantWithdraw(5_000e6, AAtranche);

    // borrower funds only attacker's share -> pendingInstantWithdraws = 5000
    // (collectInstantWithdrawFunds called for 5000 only)

    vm.prank(attacker); cdoEpoch.claimInstantWithdrawRequest(); // paid 5000

    // borrower defaults at stopEpoch of epoch 0; finalizeDefaultRecovery
    // sets defaultRecoveryEpoch = 0, defaultInstantWithdrawsFinalized = true

    uint256 balPre = underlying.balanceOf(attacker);
    vm.prank(attacker); cdoEpoch.claimInstantWithdrawRequest(); // defaulted path
    // attacker receives 5000 * defaultRecoveryPrice / RECOVERY_FULL again
    assertGt(underlying.balanceOf(attacker) - balPre, 0);
}
```

Key assertion: `instantWithdrawsRequestsByEpoch[attacker][0]` remains `5000e6` after the first successful claim, enabling the second payout from `defaultRecoveryReserve`.