Let me check how `lossRecoveryPriceByEpoch` is keyed and whether requests in two epochs can be pending under one loss price.### Title
Single-slot `lastWithdrawRequest` pointer lets multi-epoch pending receipts evade the loss haircut and claim unfunded requests at par - ([File: contracts/strategies/idle/IdleCreditVault.sol](contracts/strategies/idle/IdleCreditVault.sol))

### Summary
The analog of the LienToken `point.position` staleness bug lives in `IdleCreditVault.sol`: `lastWithdrawRequest[_user]` is a single scalar that stores only the *latest* request epoch, while loss data (`lossRecoveryPriceByEpoch`) and per-epoch receipt amounts (`withdrawsRequestsByEpoch`, `apr0Users.principalEpoch`) are keyed per epoch. When a user holds pending withdraw receipts across more than one epoch and a `stopEpochWithDuration` loss is applied to the aggregate `pendingWithdraws` basis, `_claimLossAdjustedWithdrawRequest` only clears the epoch the pointer currently references. All other pending receipts then fall through to `_claimFundedWithdrawRequest` and are paid **at par**, even though they were only partially funded — breaking the "one receipt, one haircut" invariant and draining funds reserved for other claimants.

### Finding Description
`requestWithdraw` records `lastWithdrawRequest[_user] = currentEpoch` and accumulates `withdrawsRequestsByEpoch[_user][currentEpoch]` [1](#0-0) . The re-request guard only inspects `lossRecoveryPriceByEpoch[lastWithdrawRequest[_user]]` — i.e., the *single* epoch the pointer holds [2](#0-1) . When the borrower under-funds pending withdrawals, `collectWithdrawFunds` writes exactly one `lossRecoveryPriceByEpoch[epochNumber]` computed over the *aggregate* `pendingBasis`, covering receipts from all outstanding request epochs [3](#0-2) . On claim, `_claimLossAdjustedWithdrawRequest` resolves only `lastWithdrawRequest[_user]` and clears only `withdrawsRequestsByEpoch[_user][lossEpoch]` [4](#0-3) ; receipts stored under other epochs remain in `withdrawsRequests` and are then paid in full by `_claimFundedWithdrawRequest` [5](#0-4) .

### Impact Explanation
A user (any KYC-passing lender / tranche holder, callable only via the honest IdleCDO path — no privileged collusion required) can be paid more underlying than was actually funded:

1. Epoch N (running, normal APR): user requests withdraw `A`. `pendingWithdraws = A`, `withdrawsRequestsByEpoch[u][N] = A`, `lastWithdrawRequest[u] = N`.
2. Epoch N ends; borrower repays interest but does **not** fund pending receipts at all (`collectWithdrawFunds` not called or called with 0 via the `else` branch, `pendingBasis - _amount`). `pendingWithdraws` still `A`, no `lossRecoveryPriceByEpoch` written.
3. Epoch N+1 (running): user requests withdraw `B`. The guard reads `lossRecoveryPriceByEpoch[N] == 0` → passes. `pendingWithdraws = A+B`, `lastWithdrawRequest[u] = N+1`.
4. Epoch N+1 ends with a realized loss; `collectWithdrawFunds` funds only `p·(A+B)` and writes `lossRecoveryPriceByEpoch[epochNumber] = p < 1`. Both `A` and `B` were used in the haircut basis.
5. User calls `claimWithdrawRequest`: `_claimLossAdjustedWithdrawRequest` clears only the epoch-N+1 receipt (`B`) at price `p`; the epoch-N receipt `A` remains in `withdrawsRequests` and `_claimFundedWithdrawRequest` transfers `A` **at par** from the vault's funded balance.

Result: the user extracts `B·p + A` while only `p·(A+B)` was funded — an overpay of `A·(1−p)` taken from underlyings meant for other receipt holders, i.e., direct theft / insolvency for later claimants. The same stale-pointer gap applies to `apr0Users[_user].principalEpoch` under `_clearWithdrawClaimForEpoch`, which clears an APR0 bucket only when `principalEpoch == _claimEpoch` [6](#0-5) . `_transferFundedClaim`'s reserve guard only protects `defaultRecoveryReserve`, not other users' funded claims [7](#0-6) .

### Likelihood Explanation
Requires: (a) an epoch ending without funding pending receipts — possible whenever the borrower repays interest but not withdrawals, or `stopEpochWithDuration` funds a partial amount via `previewLossAdjustedWithdrawFunds`; (b) a second request in a later epoch — permitted whenever the *pointed* epoch has no recorded loss price. No malicious privileged role is needed; honest borrower/manager sequencing suffices. The magnitude scales with the earlier-epoch receipt size and `1−p`.

**Caveat:** I could not verify the exact ordering between `epochNumber` increment and `collectWithdrawFunds` inside `IdleCDOEpochVariant.stopEpochWithDuration` (grep confirmed references but line context wasn't retrieved). The finding does not depend on that ordering — it only needs unfunded receipts persisting across two request epochs — but the PoC should confirm the epoch key actually written matches `epochNumber` at collect time.

### Recommendation
Replace the single `lastWithdrawRequest` pointer with per-epoch accounting, mirroring the report's fix direction:

- Track a set/bitmap of epochs with outstanding receipts per user (or iterate `withdrawsRequestsByEpoch` epochs), and in `requestWithdraw` revert if *any* outstanding receipt epoch has `lossRecoveryPriceByEpoch != 0`.
- In `_claimLossAdjustedWithdrawRequest`, apply `lossRecoveryPriceByEpoch[e]` to **every** outstanding epoch's claim basis, not just `lastWithdrawRequest`; alternatively, record per-epoch claim bases at collect time (e.g., `lossBasisByEpoch[epoch][user]`) so the haircut cannot be selectively applied.
- When `_clearWithdrawClaimForEpoch` resets `lastWithdrawRequest`, repoint it to the next-oldest outstanding receipt epoch rather than `0`.

### Proof of Concept
Foundry fork PoC outline against `IdleCreditVault` + `IdleCDOEpochVariant`:

```solidity
// Epoch N: user deposits, then requestWithdraw(A) via CDO.withdraw
// -> pendingWithdraws == A, lastWithdrawRequest[u] == N
vm.prank(manager); cdo.stopEpoch();            // borrower pays interest only; pendingWithdraws stays A
// Epoch N+1:
cdo.depositAA / withdraw path:
vault.requestWithdraw(B, u, B);                // guard passes: lossRecoveryPriceByEpoch[N] == 0
// stopEpochWithDuration with loss so previewLossAdjustedWithdrawFunds -> pendingToFund = p*(A+B)
cdo.stopEpochWithDuration(0, lossAmount);      // writes lossRecoveryPriceByEpoch, funds only p*(A+B)
uint256 before = underlying.balanceOf(u);
cdo.claimWithdrawRequest / vault.claimWithdrawRequest(u);
// assert received == B*p + A  (A paid at par) while only p*(A+B) was transferred in
assertGt(underlying.balanceOf(u) - before, (A + B) * p / RECOVERY_FULL);
```

The assertion on the excess over `p·(A+B)` demonstrates the haircut escape and quantified overpay.

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L261-271)
```text
    uint256 lossEpoch = lastWithdrawRequest[_user];
    uint256 lossRecoveryPrice = lossRecoveryPriceByEpoch[lossEpoch];
    if (
      lossRecoveryPrice != 0 &&
      (withdrawsRequestsByEpoch[_user][lossEpoch] != 0 ||
      (apr0Users[_user].principal != 0 && apr0Users[_user].principalEpoch == lossEpoch))
    ) {
      // A loss-adjusted receipt must be claimed before opening a later request, otherwise
      // `lastWithdrawRequest` would stop pointing to the epoch that stores its haircut.
      revert NotAllowed();
    }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L282-293)
```text
    lastWithdrawRequest[_user] = currentEpoch;
    // APR=0 requests keep separate accounting and settle interest at stopEpoch.
    // `_amount` here is the post-management-fee principal bucket for that flow.
    if (unscaledApr == 0 && !isClosed) {
      _requestWithdrawApr0(_amount, _user);
    } else {
      // increase the withdraw requests for the user
      // we record both per-user (old, kept for compatibility) and per-epoch so
      // on finalization we can distinguish "default-epoch pending receipts"
      // from old funded receipts.
      withdrawsRequests[_user] += _amount;
      withdrawsRequestsByEpoch[_user][currentEpoch] += _amount;
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L338-349)
```text
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L413-421)
```text
    uint256 pendingBasis = pendingWithdraws;
    if (_amount < pendingBasis) {
      // Legacy receipts do not have per-epoch ownership data, so they can only be fully funded.
      if (!defaultRecoveryInitialized) revert NotAllowed();
      uint256 lossRecoveryPrice = _amount * RECOVERY_FULL / pendingBasis;
      // Avoid storing a zero price, which is indistinguishable from "no loss-adjusted epoch".
      if (lossRecoveryPrice == 0) revert NotAllowed();
      pendingWithdraws = 0;
      lossRecoveryPriceByEpoch[epochNumber] = lossRecoveryPrice;
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L789-800)
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
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L821-831)
```text
    Apr0UserData storage apr0User = apr0Users[_user];
    if (apr0User.principal != 0 && apr0User.principalEpoch == _claimEpoch) {
      if (_isClearingApr0) {
        uint256 apr0Principal = apr0User.principal;
        uint256 totalApr0Principal = apr0TotalPrincipal;
        // prepareStopEpochWithApr0 may already close the global APR0 bucket before default finalization.
        apr0TotalPrincipal = apr0Principal >= totalApr0Principal ? 0 : totalApr0Principal - apr0Principal;
      }
      apr0User.principal = 0;
      apr0User.principalEpoch = 0;
    }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L897-906)
```text
  function _transferFundedClaim(address _user, uint256 _amount) internal {
    if (_amount == 0) return;
    uint256 reserve = defaultRecoveryReserve;
    if (reserve != 0) {
      uint256 balance = underlyingToken.balanceOf(address(this));
      // This should be unreachable when accounting is consistent. Keep the guard so old funded
      // receipts can never spend underlyings reserved for default recovery claimants.
      if (balance < reserve || balance - reserve < _amount) revert NotAllowed();
    }
    underlyingToken.safeTransfer(_user, _amount);
```
