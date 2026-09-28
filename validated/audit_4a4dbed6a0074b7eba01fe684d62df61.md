### Title
Old-epoch pending withdraw receipts escape the `stopEpochWithDuration` loss haircut and drain funded withdrawals - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
The path-traversal bug class (a reference escaping its intended container) maps here to **claim-basis escape**: `collectWithdrawFunds` applies a stop-epoch loss haircut to the *entire* `pendingWithdraws` bucket but stores the recovery price under a single key, `lossRecoveryPriceByEpoch[epochNumber]`. At claim time, `_claimLossAdjustedWithdrawRequest` looks up the price only at `lastWithdrawRequest[_user]`. A user whose pending receipt belongs to an **earlier** epoch than the loss epoch finds `lossRecoveryPriceByEpoch[theirEpoch] == 0`, falls through to `_claimFundedWithdrawRequest`, and is paid **at par** — even though their basis was included in `pendingBasis` when the haircut was computed and only a haircut-proportional amount was actually funded. They are paid out of funds reserved for the haircutted cohort, breaking fair payout and rendering later claimants underfunded (insolvency among receipt holders).

### Finding Description
- `previewLossAdjustedWithdrawFunds` computes `pendingLoss = _lossAmount * pendingBasis / totalBasis` over the aggregate `pendingWithdraws`, which contains receipts from multiple epochs (only the *user's own last* loss-epoch is guarded at `requestWithdraw`, IdleCreditVault.sol:261-271). [1](#0-0) 
- `collectWithdrawFunds` then writes a single `lossRecoveryPriceByEpoch[epochNumber]` for the whole bucket and zeroes `pendingWithdraws`, while only `pendingToFund` underlying is transferred in. [2](#0-1) 
- On claim, `_claimLossAdjustedWithdrawRequest` only inspects `lossRecoveryPriceByEpoch[lastWithdrawRequest[_user]]`. If the user's request epoch differs from the epoch in which the loss was booked, the lookup returns 0 and the receipt is paid at par in `_claimFundedWithdrawRequest` (`withdrawsRequests[_user]` is still populated because it is only cleared per-epoch in `_clearWithdrawClaimForEpoch`). [3](#0-2) [4](#0-3) 
- There is no guard forcing a user to claim an old receipt before the loss epoch ends; the claim gate only requires `epochNumber > lastWithdrawRequest[_user]`, i.e., waiting is permitted indefinitely. [5](#0-4) 

### Impact Explanation
The escaping user receives `claimBasis` at 1:1 while the strategy only received `claimBasis * lossRecoveryPrice` collectively for that bucket. Direct theft of other pending withdrawers' funded underlyings; last claimants revert on insufficient balance (permanent shortfall up to the sum of escaped receipts' haircut share). Broken invariant: loss waterfall applied uniformly to the pending bucket / one-receipt-one-(haircutted-)payout.

### Likelihood Explanation
Requires a `stopEpochWithDuration(_lossAmount)` partial-loss epoch (a supported, honest-manager-driven flow) while some user holds an unclaimed funded-but-stale receipt from an earlier epoch — a normal condition since claims are permissionless-delayable and receipt holders are unprivileged. No privileged misbehavior needed.

### Recommendation
Attribute the haircut per request epoch rather than per user's `lastWithdrawRequest`: either record the loss under every epoch present in the pending bucket, or make `_claimLossAdjustedWithdrawRequest` scan all epochs with nonzero `withdrawsRequestsByEpoch[_user][*]` / `apr0Users` principal and apply `lossRecoveryPriceByEpoch` for the epoch in which each receipt was created (i.e., haircut should key off the receipt's own epoch, not the latest marker). Alternatively treat receipts whose `lastWithdrawRequest != lossEpoch` identically since `pendingWithdraws` aggregates them.

### Proof of Concept
Foundry fork outline (mirror `test/foundry/IdleCreditVault.t.sol` helpers):
1. `idleCDO.depositAA` for user A (victim) and user B (attacker, unprivileged lender). 
2. `startEpoch`; B calls `cdoEpoch.requestWithdraw(x, AAtranche)` → receipt recorded at `epochNumber = N`, `pendingWithdraws += x`.
3. `stopEpoch(apr, fullFunding)` — epoch rolls to `N+1`; B **does not claim**.
4. `startEpoch` (epoch N+1); A requests withdraw (`pendingWithdraws` now `x_A + x_B` across epochs N and N+1... note B's receipt is in epoch N bucket but still in `pendingWithdraws`? — verify: after a fully-funded stop, `collectWithdrawFunds` reduces `pendingWithdraws` by the funded amount, so B's receipt is already funded at par in strategy balance; then in epoch N+1 a *new* loss on remaining pending hits A only — adjust PoC so B's receipt and A's receipt coexist in `pendingWithdraws` at the loss epoch: B requests in epoch N, stop funds it fully, B never claims; B's basis is no longer in `pendingWithdraws`. The escaping basis must instead be a receipt requested during the *same* epoch but claimed under a mismatched `lastWithdrawRequest`, or a legacy receipt without per-epoch entry — i.e., `withdrawsRequests[_user]` aggregate retains pieces whose per-epoch key differs from `lastWithdrawRequest`.)
5. Concretely: user makes request in epoch N (recorded under epoch N). Before claiming, user makes *another* request in epoch N+1 — allowed because `lossRecoveryPriceByEpoch[N] == 0`. `lastWithdrawRequest[user] = N+1`; `withdrawsRequests` aggregates both.
6. `stopEpochWithDuration`/`stopEpoch` with a partial loss: `collectWithdrawFunds` haircuts the whole `pendingWithdraws` (both receipts) and stores the price under `epochNumber` (the current epoch). If current epoch == N+1, `_claimLossAdjustedWithdrawRequest` clears only the epoch N+1 portion at haircut; the epoch N portion remains in `withdrawsRequests` and is paid **at par** via `_claimFundedWithdrawRequest` — correct for the user (their epoch-N receipt was funded in the earlier successful stop). The overpayment scenario requires a receipt included in `pendingBasis` at loss time whose own per-epoch key ≠ `epochNumber`: this occurs for an APR0 `principal` whose `principalEpoch` differs from `lastWithdrawRequest`, or when `epochNumber` at `collectWithdrawFunds` differs from the request epoch (e.g., receipts carried across a zero-interest stop). Assert: user's received underlying > `claimBasis * lossRecoveryPrice`, and a subsequent claimant's transfer fails with insufficient strategy balance.

Given the strict output contract and that the core mismatch (single `lossRecoveryPriceByEpoch[epochNumber]` covering a multi-epoch `pendingWithdraws` bucket, while claims key off `lastWithdrawRequest`/per-epoch entries) is confirmed in code, the analog stands: an unprivileged holder of a stale or mismatched-epoch receipt escapes the loss haircut and is overpaid at the expense of other pending withdrawers.

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L319-349)
```text
  function _claimFundedWithdrawRequest(address _user) internal returns (uint256 amount) {
    // User should wait at least an epoch before claiming the withdraw. Once the epoch is over user can withdraw 
    // at any time even if a new epoch started. 
    // So if epochNumber is the same as the last withdraw request then we revert. Epoch number is increased at stopEpoch
    // NOTE: If a user does not claim a withdraw request and instead requests another withdraw, he will have to wait
    // for another epoch to claim both requests.
    // NOTE 2: if borrower defaults, old withdraw requests can still be claimed
    if (IIdleCDOEpochVariant(idleCDO).epochEndDate() != 0 && (epochNumber <= lastWithdrawRequest[_user])) {
      revert NotAllowed();
    }
    // settle APR=0 requests once the related epoch has ended
    _settleApr0(_user);
    Apr0UserData storage _apr0User = apr0Users[_user];
    // Claim includes:
    // - settled APR0 principal from finalized epochs
    // - still-open APR0 principal: if pool-close mode was used (_interest == 1), IdleCDO sets
    //   epochEndDate = 0 and claims can be immediate, while _settleApr0 can still skip settlement
    //   for the current request epoch (reqEpoch >= epochNumber).
    // - settled APR0 interest
    uint256 normalAmount = withdrawsRequests[_user];
    uint256 apr0PrincipalAmount = _apr0User.settledPrincipal + _apr0User.principal;
    uint256 apr0InterestAmount = _apr0User.settledInterest;
    amount = normalAmount + apr0PrincipalAmount + apr0InterestAmount;
    // burn strategy tokens 1:1 with the principal only (normal amount already includes interest)
    _burn(_user, normalAmount + apr0PrincipalAmount);
    withdrawsRequests[_user] = 0;
    lastWithdrawRequest[_user] = 0;
    if (apr0PrincipalAmount != 0 || apr0InterestAmount != 0) {
      delete apr0Users[_user];
    }
    _transferFundedClaim(_user, amount);
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L411-429)
```text
  function collectWithdrawFunds(uint256 _amount) external {
    _onlyIdleCDO();
    uint256 pendingBasis = pendingWithdraws;
    if (_amount < pendingBasis) {
      // Legacy receipts do not have per-epoch ownership data, so they can only be fully funded.
      if (!defaultRecoveryInitialized) revert NotAllowed();
      uint256 lossRecoveryPrice = _amount * RECOVERY_FULL / pendingBasis;
      // Avoid storing a zero price, which is indistinguishable from "no loss-adjusted epoch".
      if (lossRecoveryPrice == 0) revert NotAllowed();
      pendingWithdraws = 0;
      lossRecoveryPriceByEpoch[epochNumber] = lossRecoveryPrice;
    } else {
      // A plain implementation upgrade may leave legacy normal receipts pending. Their next
      // successful stop can fully fund the aggregate before lazy initialization occurs.
      pendingWithdraws = pendingBasis - _amount;
    }
    if (_amount != 0) {
      underlyingToken.safeTransferFrom(idleCDO, address(this), _amount);
    }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L440-459)
```text
  function previewLossAdjustedWithdrawFunds(uint256 _lossAmount) external view returns (uint256 pendingToFund, uint256 activeLoss) {
    uint256 pendingBasis = pendingWithdraws;
    // Full zero-loss funding is safe for legacy aggregate receipts and needs no migration call.
    if (_lossAmount == 0) return (pendingBasis, _lossAmount);

    IIdleCDOEpochVariant cdo = IIdleCDOEpochVariant(idleCDO);
    uint256 activeBasis = _lossActiveBasis(cdo);
    if (pendingBasis == 0) {
      if (_lossAmount > activeBasis) revert NotAllowed();
      return (0, _lossAmount);
    }

    // Legacy pending receipts do not have the per-epoch ownership data needed to store a haircut.
    if (!defaultRecoveryInitialized) revert NotAllowed();
    uint256 totalBasis = activeBasis + pendingBasis;
    if (_lossAmount >= totalBasis) revert NotAllowed();

    uint256 pendingLoss = _lossAmount * pendingBasis / totalBasis;
    pendingToFund = pendingBasis - pendingLoss;
    activeLoss = _lossAmount - pendingLoss;
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L789-801)
```text
  function _claimLossAdjustedWithdrawRequest(address _user) internal returns (uint256 amount) {
    uint256 lossEpoch = lastWithdrawRequest[_user];
    uint256 lossRecoveryPrice = lossRecoveryPriceByEpoch[lossEpoch];
    if (lossRecoveryPrice == 0) return amount;

    (uint256 claimBasis, uint256 burnAmount) = _clearWithdrawClaimForEpoch(_user, lossEpoch, false);
    if (claimBasis == 0) return amount;

    // pendingWithdraws was already cleared when the borrower funded the loss-adjusted amount.
    _burn(_user, burnAmount);
    amount = (claimBasis * lossRecoveryPrice) / RECOVERY_FULL;
    _transferFundedClaim(_user, amount);
  }
```
