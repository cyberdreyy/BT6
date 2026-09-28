### Title
Vault loss exceeding gains is truncated to zero in `totalInterestDueNow`, hiding real principal impairment from tranche pricing — (File: contracts/strategies/idle/ProgrammableBorrower.sol)

### Summary
The referenced CVE concerns a value that must be sign-extended but is instead zero-extended: a quantity that is really negative (`~1` = −1 in 256-bit two's complement) is treated as a small unsigned number, producing a wildly wrong result. The direct analog in this codebase is `ProgrammableBorrower.totalInterestDueNow()`: the net epoch PnL — `vaultInterest + borrowerInterestAccruedNow() + bufferInterest − vaultLoss` — is an inherently signed quantity, but the function returns it as `uint256` and clamps any negative result to `0` (`ProgrammableBorrower.sol:330-334`). Because this is the single value `IdleCDOEpochVariant._stopEpoch` reads to price the epoch, a vault-sleeve loss larger than all accrued gains is reported as "zero interest" instead of a loss, and tranche NAV/prices are never marked down.

### Finding Description
In minted-interest programmable mode, `stopEpoch` pulls no cash (`_amountToPullFromBorrower = 0`), mints strategy tokens equal to `expectedEpochInterest` (i.e., `totalInterestDueNow`), and runs `_updateAccounting` (`IdleCDOEpochVariant.sol:376-436`). When `_vaultNetInterest()` yields a loss that exceeds `vaultInterest + borrowerInterestAccruedNow() + bufferInterest`, `totalInterestDueNow()` returns `0`, so:

1. Nothing is minted and nothing is burned — `priceAA`/`priceBB` and `lastNAVAA`/`lastNAVBB` stay at pre-loss values even though the facility's actual holdings (`underlyingToken.balanceOf + vault.convertToAssets`) are below the tranche NAV.
2. `onStopEpoch` then re-baselines: `bufferStartVaultAssets = _currentVaultAssets()` (the impaired value), `bufferedVaultDelta = 0` (`ProgrammableBorrower.sol:259-261`). On the next `startEpoch`, `onStartEpoch` computes `bufferedVaultDelta ≈ 0` and sets `epochStartVaultAssets` to the already-impaired total (`ProgrammableBorrower.sol:210-219`). The negative carry that a correct sign-extension of the delta would preserve is permanently erased — exactly like `2^64-1` being read instead of `2^256-1`.
3. The impairment only resurfaces when a pull exceeds actual cash: `onStopEpoch` returns `true` if `shortfall <= _currentVaultAssets()` even when the shortfall exceeds what full funding requires, and a real `transferFrom` failure routes to `_handleBorrowerDefault` (`ProgrammableBorrower.sol:240-245`, `IdleCDOEpochVariant.sol:398-404`).

Between the loss event and the eventual forced default, withdraw requests are funded at unimpaired prices. `collectWithdrawFunds` is called with `_pendingWithdraws` computed against stale, inflated NAV, and `onStopEpoch` withdraws only the cash it can. An LP who requests withdrawal in the epoch where the loss is hidden is paid at par from the under-collateralized borrower contract; once the hole is discovered (a later stop or close-pool), remaining claimants are haircut via `defaultRecoveryPrice`/`lossRecoveryPriceByEpoch`. The broken invariant is solvency/fair burn: "one receipt one payout" holds only for early claimants because the signed loss was truncated to unsigned zero.

### Impact Explanation
An unprivileged tranche-token holder (a KYC-passing lender) can exit at full virtual price after the vault sleeve has suffered a loss greater than epoch gains, receiving underlyings that economically belong to remaining LPs. Quantitatively, the attacker extracts `claimBasis` while the fair value is `claimBasis × (realAssets / reportedNAV)`; the excess — equal to the truncated negative delta distributed over the pool — is borne by later claimants, who either receive a `defaultRecoveryPrice < RECOVERY_FULL` haircut or find claims unpayable (permanent freezing of residual funds once the borrower contract is drained below pending basis). The signed-to-unsigned clamp converts a pool-wide loss into a first-come-first-served race.

### Likelihood Explanation
Requires (a) programmable-borrower mode with `isInterestMinted`, and (b) a vault-side loss exceeding `borrower interest + buffer interest`. Cause (b) is reachable without privileged action: the rules explicitly allow the attacker to be "a user of the programmable borrower's ERC4626 vault," and ERC4626 share price moves via realized bad debt, withdrawals that change `convertToAssets`, or donation-style manipulation depending on the vault — no owner/manager/guardian misbehavior is needed, only their routine `stopEpoch`/`startEpoch` calls around which the attack sequences. No existing guard stops it: `Default` reverts only trigger inside `_updateAccounting` when BB NAV is exhausted by an *accounted* loss, and here the loss is never put into accounting; `vaultLoss()` is observable but nothing propagates it into NAV. Note: `testTotalInterestDueNowFloorsAtZeroWhenVaultLossExceedsGains` asserts the clamp, indicating the floor itself is intentional — the defect is that no compensating mechanism applies the negative residual to tranche NAV before withdrawals are priced.

### Recommendation
Return a signed value (or an explicit `(interest, loss)` pair) from `totalInterestDueNow` and have `IdleCDOEpochVariant._stopEpoch` feed a net-negative epoch result into the existing loss machinery — e.g., treat `loss > totalGain` as an implicit `_lossAmount` routed through `_strategy.previewLossAdjustedWithdrawFunds` / `burnStrategyTokens` + `_forceUpdateAccounting` so the BB-first waterfall crystallizes it before any withdrawal is priced. At minimum, block or haircut `requestWithdraw`/`collectWithdrawFunds` funding while `vaultLoss() > totalGain`, analogous to the `lossRecoveryPriceByEpoch` path already used for `stopEpochWithDuration` losses.

### Proof of Concept
```solidity
// Foundry fork test (pattern follows test/foundry/ProgrammableBorrowerCreditVault.t.sol)
function testVaultLossTruncationLetsEarlyWithdrawerExitAtPar() external {
    uint256 amount = 10_000 * oneScale;
    vm.prank(owner);
    cdoEpoch.setIsInterestMinted(true);

    idleCDO.depositAA(amount);          // attacker LP
    // second honest LP deposits same size
    _depositAs(honestLP, amount);
    _startEpochAndCheckPrices(0);

    // Epoch runs with small borrower interest; vault sleeve then loses more
    // than accrued gains (attacker induces via vault bad debt, or warp +
    // negative PnL on Morpho market). Assert the signed value went negative
    // but the exposed value is truncated:
    vm.warp(cdoEpoch.epochEndDate() + 1);
    assertGt(programmableBorrower.vaultLoss(),
             programmableBorrower.borrowerInterestAccruedNow()
               + programmableBorrower.vaultInterestAccrued()
               + programmableBorrower.bufferInterest());
    assertEq(programmableBorrower.totalInterestDueNow(), 0); // negative delta -> 0

    // Honest stop: interest = 0, nothing burned, prices unchanged
    vm.prank(manager);
    cdoEpoch.stopEpoch(0, 0);
    assertEq(cdoEpoch.lastEpochInterest(), 0);
    assertApproxEqAbs(cdoEpoch.virtualPrice(address(aaTranche)),
                      ONE_SCALE, 1e15); // priced at par despite impairment

    // Attacker requests and later claims withdraw at full price;
    // borrower contract pays from impaired holdings.
    uint256 req = cdoEpoch.requestWithdraw(0, address(aaTranche));
    // ... after next start/stop funding cycle ...
    uint256 paid = _claimWithdraw();
    uint256 fairValue = req *
        programmableBorrower.totalUnderlying() /
        strategy.totalSupply();
    assertGt(paid, fairValue);          // attacker overpaid vs fair share

    // Later claimants forced into defaultRecoveryPrice haircut
    // once a pull exceeds real holdings -> _handleBorrowerDefault.
}
```