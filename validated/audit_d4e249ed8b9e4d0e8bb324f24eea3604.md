### Title
Post-default withdraw requests are paid at par from the fixed `defaultRecoveryReserve`, starving earlier defaulted-epoch claimants - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
Analog to "unsanitized keys in deletion": an unprivileged lender can inject *new* claim entries (`postDefaultRequests`) against a recovery pool whose size was fixed at finalization, and each such claim is paid **at par** from `defaultRecoveryReserve`. The reserve was sized only for the basis that existed at `finalizeDefaultRecovery`, so later par claims consume funds reserved for defaulted-epoch receipts, which then permanently revert on underflow.

### Finding Description
When the borrower defaults and `IdleCDOEpochVariant` calls `finalizeDefaultRecovery`, the strategy computes a fixed reserve:

```
reserveAmount = _recoveredAmount + prefundedReserve + defaultRecoveryReserve;
defaultRecoveryPrice = reserveAmount * RECOVERY_FULL / totalBasis;
```
where `totalBasis = activeBasis + defaultPendingClaimBasis()` covers only active LPs and receipts pending at that moment (lines 674–692).

After finalization, `requestWithdraw` takes the post-default branch:

```
_burn(msg.sender, _amount);
_mint(_user, _amount);
postDefaultRequests[_user] = _amount;
```
(lines 247–257). No underlying is added to the strategy — the CDO's strategy tokens are burned and an equivalent unbacked receipt is minted to the user. The comment says the amount is "already-haircut" because `virtualPrice` was lowered, but the minted receipt is still paid **1:1**:

```
function _claimPostDefaultWithdrawRequest(...) {
    amount = postDefaultRequests[_user];
    ...
    _burn(_user, amount);
    _transferDefaultRecovery(_user, amount);   // par payout
}
```
(lines 760–767), and `_transferDefaultRecovery` decrements the fixed `defaultRecoveryReserve` (lines 912–917).

Meanwhile defaulted-epoch claimants only receive `claimBasis * defaultRecoveryPrice / RECOVERY_FULL` (lines 782–783, 855). Since `defaultRecoveryPrice < 1` whenever recovery is partial, every post-default par payout drains more reserve per unit of basis than the reserve was funded for. `defaultRecoveryReserve -= _amount` uses checked arithmetic, so once post-default claims exhaust the reserve, all remaining defaulted claims (`_claimDefaultedWithdrawRequest`, `_claimDefaultedInstantWithdrawRequest`) revert permanently.

### Impact Explanation
Direct theft + permanent freezing of recovery funds. A KYC-passing lender who deposits/requests withdrawal after default finalization mints an unbacked receipt and withdraws underlying at par from a reserve that belongs pro-rata to defaulted-epoch receipt holders. Each unit claimed post-default removes ~`1/price` units of funded basis, so victims' claims revert on `defaultRecoveryReserve` underflow — their recovery is permanently unclaimable (the claim functions have no alternative payment path).

### Likelihood Explanation
Requires a borrower default with partial recovery (`defaultRecoveryPrice < 1`) plus the CDO still accepting withdraw requests after finalization (the code path at lines 247–257 explicitly supports this UX). The attacker only needs to be a lender able to call withdraw after default — an unprivileged role per the threat model. No privileged misbehavior needed; loss magnitude equals total post-default par payouts up to the full reserve.

### Recommendation
Either fund post-default requests with new underlying (have the CDO transfer `_amount` to the strategy alongside the minted receipt, or account post-default claims against a separate `postDefaultReserve` topped up per request), or pay `postDefaultRequests` at `defaultRecoveryPrice` instead of par so all claimants share the fixed reserve at the same ratio.

### Proof of Concept
Foundry fork PoC outline:

1. Vault with AA/BB lenders, epoch running. Lender L1 calls `requestWithdraw` → receipt in `withdrawsRequestsByEpoch[L1][E]`.
2. Manager `stopEpoch`, borrower fails to repay → `_handleBorrowerDefault`, then `finalizeDefaultRecovery(_recoveredAmount < basis, source)` → `defaultRecoveryPrice = 0.5e18`, `defaultRecoveryReserve = R`.
3. Attacker L2 (KYC'd lender still holding tranche tokens) calls `requestWithdraw` → hits the `defaultRecoveryFinalized` branch, gets `postDefaultRequests[L2] = amount` minted 1:1.
4. L2 calls `claimWithdrawRequest` → `_claimPostDefaultWithdrawRequest` pays `amount` at par, `defaultRecoveryReserve -= amount`.
5. Repeat with dust deposits until reserve is depleted; assert L1's `claimWithdrawRequest` reverts inside `_transferDefaultRecovery` (`defaultRecoveryReserve -= _amount` underflow), despite `defaultRecoveryPrice * claimBasis` being owed.

Uncertainty: whether the CDO-layer `withdrawAA/BB` entry points still route post-finalization requests is assumed from the strategy branch existing; confirm `IdleCDOEpochVariant.requestWithdraw` remains callable after default in the harness.