### Title
Post-default withdraw requests drain the default recovery reserve at par, insolventing haircutted claimants - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
After a borrower default is finalized with `recoveryPrice < 1`, the recovery reserve is sized exactly to pay defaulted-epoch receipts at the haircut (`reserveAmount = recoveryPrice * totalBasis`). However, `requestWithdraw` still lets any tranche holder open a new *post-default* request that is paid **1:1** out of that same, already-fully-allocated reserve via `postDefaultRequests` / `_claimPostDefaultWithdrawRequest` / `_transferDefaultRecovery`. No new underlying is added to the reserve for these requests, so each post-default claim steals an equivalent amount from defaulted-epoch claimants, and the last claimants' claims revert permanently (checked `defaultRecoveryReserve -= _amount` underflow / insufficient balance).

### Finding Description
The bug class of the external report — an authenticated user with restricted access extracting aggregated data/value that should be restricted to them — maps onto the shared recovery reserve: aggregate funds set aside for one class of claimants (defaulted-epoch receipts, haircutted) become claimable at par by another class (post-default requesters) whose basis was never included in `totalBasis`.

Key path:

1. `finalizeDefaultRecovery` (IdleCreditVault.sol:661) computes `totalBasis = activeBasis + pendingBasis` and sets `defaultRecoveryReserve = reserveAmount` and `defaultRecoveryPrice = reserveAmount * 1e18 / totalBasis`. When recovery is partial, `defaultRecoveryPrice < 1e18` and the reserve is **fully allocated** to the pre-default claim basis — there is no surplus earmarked for future requests.
2. `requestWithdraw` post-default branch (lines 247–257) only requires the user to have no open requests, then burns the CDO's strategy tokens, mints the user a receipt of `_amount`, and sets `postDefaultRequests[_user] = _amount`. Critically, **no underlying transfer accompanies this** — the branch returns before `pendingWithdraws` is touched and nothing is added to `defaultRecoveryReserve`.
3. `claimWithdrawRequest` → `_claimPostDefaultWithdrawRequest` (lines 760–767) burns the receipt and pays `amount` **1:1** through `_transferDefaultRecovery` (lines 912–917), which decrements `defaultRecoveryReserve`.
4. Defaulted-epoch claimants are paid `claimBasis * defaultRecoveryPrice / RECOVERY_FULL` from the same reserve (`_claimDefaultedWithdrawRequest`, lines 772–784).

Since the reserve was sized as `recoveryPrice * totalBasis`, paying post-default requests at par overdrawing it is arithmetically guaranteed whenever `defaultRecoveryPrice < 1`: the last `sum(postDefaultRequests)` worth of haircutted claims can never be paid — `_transferDefaultRecovery` reverts on `defaultRecoveryReserve -= _amount` (Solidity 0.8 checked arithmetic), permanently freezing those claims.

Note the analogy detail: the comment "already priced after the haircut" refers to the *request* amount being computed from the post-haircut tranche `virtualPrice` in `IdleCDOEpochVariant.requestWithdraw` — i.e., the user surrenders tranche tokens whose post-default value is `_amount`. But the *payment* source is the old claimants' reserve, which never received the corresponding backing, so the payer-side haircut is missing.

### Impact Explanation
- **Direct theft / permanent freezing**: A post-default requester withdraws at 100% of face value while defaulted-epoch claimants can collectively only recover `defaultRecoveryPrice`%. Every post-default unit claimed converts haircutted claimants' recovery into a full-value payout for the requester; the tail claimants' receipts become unclaimable (revert), permanently freezing their recovery share.
- Quantified example: `totalBasis = 1000`, recovered `500` → `defaultRecoveryPrice = 0.5e18`, reserve = 500. An attacker holding post-default tranche tokens opens `requestWithdraw` for `postDefaultRequests = 100`, claims 100 at par. The remaining reserve (400) can now only honor 400 of the 500 owed to default-epoch claimants; the last claimant(s) lose 100 permanently.
- Invariant broken: "one receipt, one payout" — the reserve must satisfy `Σ claims ≤ recoveryPrice * totalBasis`; post-default payouts violate this by spending unbudgeted reserve.

### Likelihood Explanation
- Attacker profile: any tranche-token holder (KYC-passed lender), which is an allowed unprivileged role. The privileged actors (manager finalizing default, owner) are honest.
- Preconditions: a borrower default finalized by the CDO with `defaultRecoveryPrice < 1e18` (any partial recovery — the common case for defaults), and the attacker holding tranche tokens post-finalization. `requestWithdraw` is permissionless modulo `isWalletAllowed` and has no cap tying post-default requests to reserve surplus.
- The gate `_hasWithdrawRequest(_user)` only prevents users with *existing* receipts from double-requesting; it does not prevent the reserve drain itself.

### Recommendation
Track post-default requests as a separate, self-funded bucket instead of paying them from the recovery reserve. Options:
- In `requestWithdraw`'s post-default branch, pull the corresponding underlying backing (e.g., have the CDO transfer the recovered-equivalent amount) or add post-default requests to a new claim basis funded by an increased reserve.
- Alternatively, pay post-default requests at `defaultRecoveryPrice` like other claims, and require `defaultRecoveryReserve` to be incremented by the CDO when the post-default receipt is created.
- Add an invariant check in `_claimPostDefaultWithdrawRequest` that the reserve retains enough to cover remaining defaulted-epoch claims (`reserve - amount >= outstandingDefaultClaims * defaultRecoveryPrice / RECOVERY_FULL`).

### Proof of Concept
Foundry fork sketch against `IdleCreditVault.t.sol` harness:

```solidity
function testPostDefaultRequestDrainsRecoveryReserve() external {
    // 1. Deposit, run an epoch, then force borrower default:
    //    manager calls stopEpoch, getFundsFromBorrower fails -> _handleBorrowerDefault.
    idleCDO.depositAA(10_000 * ONE_SCALE);
    _startEpochAndCheckPrices(0);
    vm.warp(cdoEpoch.epochEndDate() + 1);
    // borrower has insufficient funds -> default
    vm.prank(manager);
    cdoEpoch.stopEpoch(0, 0);

    // 2. Owner/manager finalize recovery with partial funds (50%):
    //    finalizeDefaultRecovery(500, recoverySource) -> defaultRecoveryPrice = 0.5e18,
    //    defaultRecoveryReserve = 500 for totalBasis = 1000.

    // 3. Attacker (a different tranche holder with no prior requests):
    //    calls IdleCDOEpochVariant.requestWithdraw(attackerShares, AATranche).
    //    requestWithdraw mints postDefaultRequests[attacker] = amount, NO underlying added.

    // 4. Attacker calls claimWithdrawRequest -> _claimPostDefaultWithdrawRequest
    //    pays `amount` at PAR from defaultRecoveryReserve (500 -> 500 - amount).

    // 5. Original defaulted-epoch claimants now claim claimBasis * 0.5;
    //    total payouts exceed reserve -> last claimant's
    //    `defaultRecoveryReserve -= amount` underflows -> permanent revert.
}
```

Confidence note: this analysis relies on the visible accounting in `IdleCreditVault.sol` — `defaultRecoveryReserve` is only incremented in `finalizeDefaultRecovery`/`reserveDefaultRecovery`, and the post-default `requestWithdraw` branch demonstrably adds claims without funding. If the CDO-side post-default `requestWithdraw` path (not fully inspected) separately transfers underlying into the strategy for these receipts, the finding would need re-evaluation; the code comments ("backed by the reserve") suggest it does not.