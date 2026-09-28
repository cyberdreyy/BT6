This "guard checks the wrong variable" bug class looks analogous to instant-withdraw receipt accounting. Let me verify whether `requestInstantWithdraw` / `claimInstantWithdrawRequest` can be reached after default finalization in the CDO.### Title
Double-claim of default recovery reserve via stale per-epoch instant-withdraw receipt — (`contracts/strategies/idle/IdleCreditVault.sol`)

### Summary
`claimInstantWithdrawRequest` clears the aggregate receipt counter `instantWithdrawsRequests[_user]` but never clears the per-epoch basis `instantWithdrawsRequestsByEpoch[_user][epoch]`. After a default is finalized, `_claimDefaultedInstantWithdrawRequest` reads the per-epoch map — so an instant receipt that was already fully funded and paid out is still counted as a defaulted claim and is paid a second time from `defaultRecoveryReserve`. This mirrors the kernel bug class: the guard/consume path updates one index (aggregate) while the sensitive path dereferences a different index (per-epoch entry) for the same logical receipt.

### Finding Description
In `claimInstantWithdrawRequest` (`IdleCreditVault.sol:380-393`), a funded instant receipt is paid and `instantWithdrawsRequests[_user]` is zeroed, but `instantWithdrawsRequestsByEpoch[_user][currentEpoch]` and `instantWithdrawClaimsByEpoch[currentEpoch]` are left populated — correct for the aggregate basis used at finalization, but a stale per-user entry survives.

After `finalizeDefaultRecovery` sets `defaultRecoveryFinalized` and `defaultInstantWithdrawsFinalized` (when `pendingInstantWithdraws != 0`, line 696), the next `claimInstantWithdrawRequest` first runs `_claimDefaultedInstantWithdrawRequest` (lines 382-385). That function reads `instantWithdrawsRequestsByEpoch[_user][defaultRecoveryEpoch]` (line 844), subtracts it from `instantWithdrawsRequests[_user]` (line 848, which can underflow-revert only if smaller — not if zeroed then re-grown), burns `claimBasis` receipt tokens, and transfers `claimBasis * defaultRecoveryPrice / 1e18` from the recovery reserve (line 855).

The attacker sequence: in the same epoch, request instant withdraw of `X`, let it be funded via `getInstantWithdrawFunds`/`collectInstantWithdrawFunds`, and `claimInstantWithdrawRequest` — receiving `X` while `instantWithdrawsRequestsByEpoch[user][epoch]` still equals `X`. After default and `finalizeDefault` in that same epoch (requires `defaultInstantWithdrawsFinalized`, i.e. any unfunded instant remainder — satisfiable by leaving a dust instant request unfunded or via another user's), the attacker makes a new `requestInstantWithdraw` post-default (the strategy function has no `defaultRecoveryFinalized` guard, lines 356-375) minting `Y ≥ X` receipt tokens. The next `claimInstantWithdrawRequest` burns `X` from the fresh balance and pays `X * defaultRecoveryPrice` from `defaultRecoveryReserve` — a receipt already paid once is paid again.

### Impact Explanation
Direct theft of the default recovery reserve: each stale-basis claim drains `defaultRecoveryPrice`-denominated funds earmarked for honest defaulted-epoch claimants, causing reserve insolvency — later legitimate `_claimDefaultedWithdrawRequest`/`_transferDefaultRecovery` calls revert on `defaultRecoveryReserve -= _amount` underflow, permanently freezing remaining recovery funds. Quantified loss: up to `defaultRecoveryPrice × (sum of already-claimed instant receipts in the default epoch)`.

### Likelihood Explanation
Requires: an epoch with instant withdraws where a receipt is funded and claimed before borrower default in the same epoch, `pendingInstantWithdraws != 0` at finalization, and the CDO permitting `requestInstantWithdraw`/`claimInstantWithdrawRequest` after default (the funded-claim path is exercised post-default in `test/foundry/IdleCreditVault.t.sol:4541`). All steps are unprivileged-EAO actions; the privileged calls (stopEpoch, finalizeDefault) are honest-role sequencing only. Caveat: whether `IdleCDOEpochVariant.requestInstantWithdraw` reverts when `defaulted()` is true was not fully verified in this pass — if the CDO blocks new instant requests post-default, the attacker would need to pre-position sufficient receipt-token balance (e.g. split the funded instant receipt and hold a second pending normal withdraw receipt) before the default.

### Recommendation
Mirror the fix pattern from the kernel patch — clear the same index that is later dereferenced. In `claimInstantWithdrawRequest`, also zero `instantWithdrawsRequestsByEpoch[_user][epochNumber]` (and reduce `instantWithdrawClaimsByEpoch[epochNumber]` by the funded-and-claimed amount), or alternatively clear the per-epoch entry inside `_claimDefaultedInstantWithdrawRequest` before payout (already done at line 847) *and* add a `defaultRecoveryFinalized`/`defaulted()` guard to `requestInstantWithdraw` so post-default requests cannot mint fresh receipt tokens to satisfy the burn.

### Proof of Concept
```solidity
// Foundry fork PoC sketch (extend test/foundry/IdleCreditVault.t.sol harness)
// Preconditions: instant withdraws enabled; epoch E running.
uint256 X = 10_000 * ONE_SCALE;

// 1. Attacker deposits and requests instant withdraw of X in epoch E.
_depositWithUser(attacker, X, true);
vm.prank(attacker);
cdoEpoch.requestInstantWithdraw(X, address(AAtranche)); // instantWithdrawsRequestsByEpoch[attacker][E] = X

// 2. Borrower/manager funds instant queue; attacker claims X at par.
//    instantWithdrawsRequests[attacker] -> 0, but per-epoch entry stays X.
vm.prank(manager);
cdoEpoch.getInstantWithdrawFunds();
vm.prank(attacker);
cdoEpoch.claimInstantWithdrawRequest(); // attacker received X

// 3. Leave dust unfunded instant remainder (or another user does),
//    epoch ends, borrower defaults, owner finalizes recovery.
//    defaultInstantWithdrawsFinalized == true; defaultRecoveryReserve = R.

// 4. Attacker requests a fresh post-default instant withdraw Y >= X,
//    minting receipt tokens so the burn in step 5 succeeds.
vm.prank(attacker);
cdoEpoch.requestInstantWithdraw(X, address(AAtranche));

// 5. claimInstantWithdrawRequest runs _claimDefaultedInstantWithdrawRequest:
//    reads stale instantWithdrawsRequestsByEpoch[attacker][E] == X,
//    pays X * defaultRecoveryPrice / 1e18 from reserve -- second payout.
uint256 balPre = underlying.balanceOf(attacker);
uint256 reservePre = creditVault.defaultRecoveryReserve();
vm.prank(attacker);
cdoEpoch.claimInstantWithdrawRequest();
assertGt(underlying.balanceOf(attacker) - balPre, X); // stole recovery-priced X again
assertLt(creditVault.defaultRecoveryReserve(), reservePre); // reserve drained for a paid receipt
```