### Title
`requestWithdraw` reverts on arithmetic underflow when the upfront management fee exceeds principal plus projected interest, permanently freezing lender exits - (File: contracts/IdleCDOEpochVariant.sol)

### Summary
The external report (CVE-2025-2581) describes an integer underflow in a size calculation that produces an invalid (wrapped) result. The closest analog in this codebase is the withdrawal-request size calculation in `IdleCDOEpochVariant.requestWithdraw`, where `_underlyings = principal + interest - totalFees` can underflow because `_totalWithdrawFees` returns the full upfront management fee charged on the *principal* for a duration of `epochDuration` plus the remaining buffer, even when that fee exceeds `principal + interest`.

### Finding Description
In `requestWithdraw` (`contracts/IdleCDOEpochVariant.sol:772-778`):

```solidity
uint256 principal = _underlyings;
(uint256 interest, int256 diff) = _calcInterestWithdrawRequest(_underlyings, _tranche);
uint256 totalFees = _totalWithdrawFees(principal, interest);
// user is requesting principal + interest minus upfront management fee and net performance fee
_underlyings = principal + interest - totalFees;
```

`_totalWithdrawFees` (`IdleCDOEpochVariant.sol:900-906`) returns `_mgmtFee` verbatim whenever `_mgmtFee >= _interest`:

```solidity
uint256 _mgmtFee = _calculateManagementFee(_principal, _withdrawRequestManagementFeeDuration());
return _mgmtFee >= _interest ?
  _mgmtFee :
  _interest - _netGainAfterFees(_interest, _mgmtFee);
```

`_withdrawRequestManagementFeeDuration` (`IdleCDOEpochVariant.sol:925-931`) returns `epochDuration + (epochEndDate + bufferPeriod - block.timestamp)`, i.e. one full epoch plus the remaining buffer. The management fee is therefore charged on the principal over a duration that can substantially exceed one year of fee accrual equivalent when `epochDuration` and `bufferPeriod` are large, while `interest` is only the projected tranche interest.

When `managementFee * (epochDuration + remainingBuffer) / YEAR >= principal + interest`, the subtraction `principal + interest - totalFees` underflows and the whole transaction reverts (Solidity 0.8 checked arithmetic — the analog of an underflowing `malloc` size is a size/basis computation whose result wraps or reverts). Because both `principal` and `interest` scale linearly with the requested tranche amount, the revert occurs for *any* request size, including partial requests. The same underflow exists in the view function `maxWithdrawable` (`IdleCDOEpochVariant.sol:913`: `currentUnderlyings -= _calculateManagementFee(...)`), which confirms the fee is applied to the entire balance without a cap relative to principal.

The tranche token's `redeem`/`redeemUnderlying` paths in `IdleCreditVault` are explicit no-ops (`IdleCreditVault.sol:983-991`), so `requestWithdraw` is the sole exit path for a lender's tranche position.

### Impact Explanation
Once the configuration reaches a state where the upfront management fee on principal exceeds principal + projected interest (long `epochDuration`/`bufferPeriod` combined with a non-trivial `managementFee`, and/or a low/0 APR where `interest` ≈ 0), every call to `requestWithdraw` on that tranche reverts. All KYC-passed lenders holding that tranche have their principal frozen — they cannot request a normal withdrawal, cannot claim, and have no alternative redemption path in the vault. In the worst configuration (APR 0, high management fee, long epoch) this is effectively permanent freezing of all lender funds for that tranche. Loss equals the trapped tranche NAV.

Note: I could not fully confirm on-chain whether currently deployed pools use parameter combinations that already trigger this (managementFee, epochDuration, bufferPeriod are owner-set), so the severity is contingent on configuration, but the code path is unconditionally unsafe — nothing bounds `totalFees <= principal + interest`.

### Likelihood Explanation
- The attacker requirement is minimal: no attacker action is even needed — an ordinary lender calling `requestWithdraw` after an honest manager sets a long `epochDuration`/`bufferPeriod` (e.g. a 1-year credit line) with a moderate `managementFee` and a low or 0 APR hits the revert. In the APR0 mode explicitly supported by `IdleCreditVault` (`unscaledApr == 0`, `_requestWithdrawApr0`), `_calcInterestWithdrawRequest` still computes interest from the scaled `lastApr`, but with `setAprs(0,0)` interest is 0 while `_mgmtFee > 0`, so `principal + 0 - _mgmtFee` underflows whenever `mgmtFee*duration/YEAR > principal`, and even a small positive fee over a >1-year-equivalent duration triggers it.
- Existing guards do not stop it: `_skimDonatedAssets`, `_updateAccounting`, KYC, and the allow-flags all pass normally; there is no clamp `min(totalFees, principal + interest)`.
- Feasibility caveat: for typical short epochs (weeks) and modest fees the subtraction stays positive, so this requires either a long-duration pool or a high management fee. Since `managementFee` can be set up to `FULL_ALLOC` (100%), a fee of e.g. 30% with `epochDuration + buffer ≈ 4 years`, or a smaller fee with proportionally longer duration, crosses the threshold; at APR 0 the threshold is `mgmtFee * duration / YEAR > principal` alone.

### Recommendation
Cap the charged fee at the amount being withdrawn, e.g.:

```solidity
uint256 totalFees = _totalWithdrawFees(principal, interest);
if (totalFees > principal + interest) {
  totalFees = principal + interest; // or min(totalFees, principal)
}
_underlyings = principal + interest - totalFees;
```

and apply the same bound in `maxWithdrawable` (clamp `_calculateManagementFee` result to `currentUnderlyings`). Alternatively, charge the upfront management fee only on the projected *interest* or cap `managementFee * _withdrawRequestManagementFeeDuration()` at a fraction of principal, and add a regression test covering APR0 + long-duration requests.

### Proof of Concept
Foundry fork test sketch (extend `test/foundry/IdleCreditVault.t.sol` harness):

```solidity
function testRequestWithdrawUnderflowFreeze() external {
    // 100% management fee (or lower with proportionally longer epoch)
    _setFeeParams(TL_MULTISIG, 0, FULL_ALLOC, 100_000); // fee=0 perf, mgmtFee=100%
    vm.prank(owner);
    cdoEpoch.setIsAYSActive(false);
    // APR = 0 so projected interest is 0
    vm.prank(manager);
    IdleCreditVault(address(strategy)).setAprs(0, 0);

    uint256 amount = 10000 * ONE_SCALE;
    idleCDO.depositAA(amount);

    // request withdraw during buffer: duration = epochDuration + remaining buffer
    // mgmtFee = principal * 1.0 * duration/YEAR; with duration > 1 year this exceeds principal
    // even for shorter pools, any fee where mgmtFee > principal + interest reverts identically
    vm.expectRevert(stdError.arithmeticError); // panic: arithmetic underflow
    cdoEpoch.requestWithdraw(0, address(AAtranche)); // _amount = full balance
}
```

The revert is the wrapped subtraction at `IdleCDOEpochVariant.sol:776` (`principal + interest - totalFees`). Because every request size reverts proportionally and `redeem` is a no-op, all AA/BB lender principal in that tranche is frozen — a direct analog of the CVE-2025-2581 underflow in the withdrawal-size computation.