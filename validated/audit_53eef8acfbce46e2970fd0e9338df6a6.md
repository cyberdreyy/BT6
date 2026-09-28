### Title
Mid-epoch deposit mints shares for unfunded interest and can force borrower default at `stopEpoch` - (File: contracts/IdleCDOEpochVariant.sol)

### Summary
`depositDuringEpoch` lets any KYC'd wallet inflate `expectedEpochInterest` by an attacker-chosen amount and immediately mints tranche shares that embed that not-yet-earned interest at a discounted price. The borrowed funds are forwarded to the borrower, but the epoch's repayment obligation grows without bound relative to what the borrower provisioned. When `stopEpoch` later tries `getFundsFromBorrower(expectedInterest + pendingWithdraws)`, the transfer fails and the whole vault is forced into `_handleBorrowerDefault`. The attacker keeps tranche shares claiming principal *plus* interest that was never paid, which then dilutes honest LPs both in active NAV and in `finalizeDefaultRecovery` claim basis. This mirrors CVE-2018-7587's bug class: a crafted, oversized user input triggers a resource obligation the system cannot fulfill, turning into a DoS with fund impact.

### Finding Description
In `depositDuringEpoch` (contracts/IdleCDOEpochVariant.sol:656-733):

```solidity
uint256 interest = _calcInterest(_amount) * (remaining + buffer) / (epochDuration + buffer);
...
_minted = (_amount + trancheInterest) * _trancheTotSupply / expectedFinal;
_mintShares(_tranche, msg.sender, _minted, _amount);
expectedEpochInterest += interest;
IdleCreditVault(strategy).mintStrategyTokens(_amount);
_transferUnderlyings(_borrower(), _amount);
```

Two compounding defects:

1. **Unbounded repayment obligation**: `expectedEpochInterest += interest` grows linearly with `_amount`, bounded only by the owner-set `_guarded` deposit limit. At `stopEpoch` the CDO pulls `_amountToPullFromBorrower + _pendingWithdraws` via `transferFrom` (line 408 → `getFundsFromBorrower`, line 550-553). An honest borrower sizes balance/allowance for the obligation that existed when the epoch started. A sufficiently large mid-epoch deposit makes the pull exceed that, the `try` reverts, and `_handleBorrowerDefault` fires (line 504): `defaulted = true`, paused, `isEpochRunning = false`, withdraw requests disabled — freezing **all** lender funds until owner/manager runs `finalizeDefault`.

2. **Unfunded interest baked into claims**: `_minted` includes `trancheInterest` the borrower never paid. On default, `finalizeDefaultRecovery` computes `activeBasis = balanceOf(idleCDO) + _defaultActiveInterestBasis(cdo)`, where the interest basis is `expectedEpochInterest - pendingFees` (IdleCreditVault.sol:674-679, 728-736). The attacker's phantom interest inflates `totalBasis`, lowering `defaultRecoveryPrice` for every claimant — while the attacker's own shares still redeem on the inflated basis. The attacker recovers ≈ `D + I*r` (deposit `D`, phantom interest `I`, recovery price `r`) having contributed only `D`, stealing `~I*r` of the reserve from honest LPs.

The existing guards do not stop it: `_skimDonatedAssets` only sweeps raw donations; `_guarded` is a cap, not per-depositor solvency; `_trancheTotSupply == 0` check only blocks first-deposit edge; KYC gating is expected — a KYC-passing lender is the attacker model.

### Impact Explanation
Temporary freezing of all vault funds (deposits paused, withdrawals disabled) until `finalizeDefault`, plus direct theft on partial recovery: the attacker's minted shares carry `trancheInterest` never funded by the borrower, diluting `defaultRecoveryPrice` and active tranche NAV for honest LPs. With a 1M pool, APR 10%, and a 10M mid-epoch deposit with `remaining+buffer ≈ epoch+buffer`, phantom interest ≈ 1M; at 50% recovery the attacker extracts ≈ 500k from honest claimants' share of the reserve.

### Likelihood Explanation
Requires `isDepositDuringEpochDisabled == false` — a supported, owner-enabled configuration exercised by the test suite (`setIsDepositDuringEpochDisabled(false)` in test/foundry/IdleCreditVault.t.sol). Attacker needs KYC and capital equal to the deposit (which is largely recoverable through tranche claims post-default, so net cost is low). The default trigger needs only that the pull exceed borrower allowance/balance — the attacker's interest is by definition unprovisioned, since it did not exist when the borrower committed to the epoch.

### Recommendation
- Cap `depositDuringEpoch` amounts (e.g., fraction of live NAV or `expectedEpochInterest` delta) so a single depositor cannot materially expand the borrower's repayment obligation.
- On default, exclude unfunded mid-epoch interest from the claim basis: track per-deposit promised interest and subtract the unpaid portion from `expectedEpochInterest`/active basis in `finalizeDefaultRecovery`, or mint the interest component only when `stopEpoch` successfully collects it.
- Consider requiring borrower-side acknowledgment (or strategy-side escrow) before crediting `expectedEpochInterest` for mid-epoch deposits.

### Proof of Concept
```solidity
// Foundry fork-style PoC (existing harness conventions in test/foundry/IdleCreditVault.t.sol)
function testMidEpochDepositForcesDefaultAndDilutes() external {
    // 1. Honest LPs deposit; AYS off so mid-epoch deposits are supported
    idleCDO.depositAA(1_000_000 * ONE_SCALE);
    vm.prank(owner); cdoEpoch.setIsAYSActive(false);
    _startEpochAndCheckPrices(0);
    uint256 interestPre = cdoEpoch.expectedEpochInterest();

    // 2. Attacker (KYC'd) deposits huge amount mid-epoch
    vm.prank(owner); cdoEpoch.setIsDepositDuringEpochDisabled(false);
    address attacker = makeAddr("attacker");
    uint256 D = 10_000_000 * ONE_SCALE;
    deal(defaultUnderlying, attacker, D);
    vm.startPrank(attacker);
    IERC20Detailed(defaultUnderlying).approve(address(cdoEpoch), D);
    uint256 minted = cdoEpoch.depositDuringEpoch(D, address(AAtranche));
    vm.stopPrank();

    // 3. Borrower approved only interestPre + principal obligations; pull fails
    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.prank(manager);
    cdoEpoch.stopEpoch(0, 0);          // getFundsFromBorrower reverts -> default
    assertTrue(cdoEpoch.defaulted());  // all funds frozen

    // 4. Attacker's minted shares embed unpaid interest; finalization dilutes
    uint256 recovered = /* partial recovery */;
    vm.prank(owner);
    cdoEpoch.finalizeDefault(recovered, recoverySource);
    // attacker redeems > D * r on shares claiming D + I
}
```