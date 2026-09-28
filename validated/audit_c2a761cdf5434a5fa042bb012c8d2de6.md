### Title
Splitting `requestWithdraw` into dust-sized requests rounds `_totalWithdrawFees` to zero, letting a lender evade all withdrawal fees - (File: contracts/IdleCDOEpochVariant.sol)

### Summary
`IdleCDOEpochVariant.requestWithdraw` charges an upfront management fee and a net performance fee via `_totalWithdrawFees`, which relies on integer-division rounding in `_calculateManagementFee` (`nav * managementFee * duration / 3153600000000`) and `_netGainAfterFees` (`gain - gain * fee / FULL_ALLOC`). There is no minimum withdrawal amount, so an unprivileged KYC'd tranche holder can decompose one large withdrawal into many small requests, each of which computes a fee that rounds to 0, paying no fees at all while receiving the same aggregate principal-plus-interest.

### Finding Description
In `requestWithdraw` (contracts/IdleCDOEpochVariant.sol:739-791), each request computes:

```solidity
uint256 principal = _underlyings;
(uint256 interest, int256 diff) = _calcInterestWithdrawRequest(_underlyings, _tranche);
uint256 totalFees = _totalWithdrawFees(principal, interest);
_underlyings = principal + interest - totalFees;
pendingWithdrawFees += totalFees;
```

`_totalWithdrawFees` (lines 900-906) returns `_calculateManagementFee(_principal, _withdrawRequestManagementFeeDuration())` plus the performance fee embedded in `_netGainAfterFees` (lines 889-894: `gain - gain * fee / FULL_ALLOC`). Both are floor-divisions:

- Management fee is 0 when `principal < 3153600000000 / (managementFee * duration)`. With `managementFee = 1_000` (1%) and `duration ≈ epoch + buffer ≈ 3e6 s`, any request below ~1e6 wei (~1 unit of a 6-decimal underlying like USDC) accrues zero upfront management fee, even though the receipt will sit outside live NAV for a full epoch.
- Performance fee is 0 when `interest < FULL_ALLOC / fee` (e.g. interest < 10 wei at `fee = 10_000`).

`requestWithdraw` accepts any nonzero `_amount`; `IdleCreditVault.requestWithdraw` (contracts/strategies/idle/IdleCreditVault.sol:243-246) only early-returns on `_amount == 0` and mints the receipt unconditionally. Neither `isWalletAllowed`, `_skimDonatedAssets`, nor `_updateAccounting` blocks repeated small requests. The resulting receipts are aggregated per user (`withdrawsRequests[_user] += _amount`), so the attacker performs one `claimWithdrawRequest` after the epoch settles and receives the full gross amount.

Quantified example: a lender holding 10,000 USDC of AA tranche requests withdrawal in 10,000 chunks of ~1 USDC. With a 1% management fee and ~35-day receipt duration, a single request would owe ~9.6 USDC upfront management fee; chunked, `pendingWithdrawFees` increases by 0 and the feeReceiver/owner collect nothing. On a low-fee L2 this is economically viable, exactly as in the referenced Inverter finding.

### Impact Explanation
Permanent loss of protocol revenue: management fees and performance fees that should flow to `feeReceiver`/`owner` via `pendingWithdrawFees` are silently zeroed by rounding. The loss scales with the withdrawn amount and the configured fees (up to `fee`% of interest plus `managementFee` annualized on principal for epoch+buffer duration), and is fully evadable rather than bounded by 1 wei, because each dust request independently underflows the fee formula.

### Likelihood Explanation
Requires only a KYC-passing tranche holder, an `allowAAWithdrawRequest`/`allowBBWithdrawRequest` flag enabled, and nonzero fee parameters — all normal operating conditions. No privileged action or timing dependence beyond making many calls during a withdraw window, which is cheap on L2 deployments the vault targets.

### Recommendation
Enforce a minimum `requestWithdraw` amount (e.g. a `minWithdrawRequest` set at initialization such that `_totalWithdrawFees(minAmount, minInterest) > 0`), or round fees up (`ceilDiv`) in `_calculateManagementFee` and the performance-fee term of `_netGainAfterFees` so dust requests can never be fee-free.

### Proof of Concept
Foundry fork PoC sketch (against `IdleCreditVault.t.sol` harness):

```solidity
function testDustWithdrawsEvadeFees() external {
    uint256 amount = 10_000 * ONE_SCALE; // 6-decimal underlying
    _setFeeParams(TL_MULTISIG, 10_000, FULL_ALLOC, cdoEpoch.managementFee()); // 10% perf fee
    _setManagementFee(1_000); // 1% mgmt fee

    uint256 trancheAmount = idleCDO.depositAA(amount);
    _transferBurnedTrancheTokens(address(this), true);

    // Baseline: single request books fees
    uint256 single = cdoEpoch.requestWithdraw(trancheAmount / 2, address(AAtranche));
    uint256 feesSingle = cdoEpoch.pendingWithdrawFees();
    assertGt(feesSingle, 0);

    // Reset by redeploying state is omitted for brevity; instead compare per-unit fees:
    // Chunked: many ~1e6-wei underlying requests each compute 0 mgmt fee
    uint256 chunk = 1e6 * ONE_TRANCHE_TOKEN / cdoEpoch.tranchePrice(address(AAtranche));
    uint256 feesBefore = cdoEpoch.pendingWithdrawFees();
    for (uint i; i < 100; ++i) {
        cdoEpoch.requestWithdraw(chunk, address(AAtranche));
    }
    assertEq(cdoEpoch.pendingWithdrawFees() - feesBefore, 0, "dust requests paid no fee");
}
```

Each iteration exercises `requestWithdraw` → `_totalWithdrawFees` → `_calculateManagementFee` returning 0 and `_netGainAfterFees` applying a 0 performance fee, while `IdleCreditVault.requestWithdraw` mints the receipt normally; `claimWithdrawRequest` after `stopEpoch` pays the full gross sum.