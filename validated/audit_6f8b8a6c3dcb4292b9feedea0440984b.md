### Title
APR0 interest reserved in `pendingWithdraws` but unclaimable due to `apr0RateByEpoch` rounding to zero - ([File: contracts/strategies/idle/IdleCreditVault.sol])

### Summary
`prepareStopEpochWithApr0` computes a per-epoch interest rate for APR0 withdraw requesters as `apr0RateByEpoch[epochNumber] = (_apr0NetInterest * 1e18) / _principal`. When `_principal` exceeds `_apr0NetInterest * 1e18` (easily reachable with an 18-decimal underlying or a large APR0 bucket), the rate rounds down to `0`, but `_apr0NetInterest` is still added to `pendingWithdraws`. The reserved interest is then owed by the vault yet no user can ever claim it via `_settleApr0` (`settledInterest += (_principal * _rate) / 1e18` = 0). This is the same bug class as the external report: an aggregate is booked while the per-share/per-principal distribution rounds to zero, permanently locking the reserved amount.

### Finding Description
In `prepareStopEpochWithApr0` (called by `IdleCDOEpochVariant.stopEpoch` for APR0 epochs), the code: [1](#0-0) 

adds the full `_apr0NetInterest` to `pendingWithdraws` whenever it is non-zero, and only then stores the derived rate. The guards `if (_apr0InterestGross != 0)` (line 522) and `if (_apr0NetInterest != 0)` (line 533) protect against zero *aggregate* interest, but not against the *rate* rounding to zero. In `_settleApr0`: [2](#0-1) 

`settledInterest` is derived solely from the stored rate, so when `apr0RateByEpoch[_reqEpoch] == 0`, every APR0 user settles with zero interest while the vault's `pendingWithdraws` was permanently increased.

`pendingWithdraws` is the vault's liability to withdraw requesters: it must be covered by funds repaid at `stopEpoch`, and it is excluded from the pool's distributable NAV. Since no user can ever withdraw the phantom portion, the corresponding tokens either remain locked in the vault forever (if `stopEpoch` transferred enough) or, worse, the inflated `pendingWithdraws` inflates the amount demanded from the borrower while only a subset is ever claimable — a broken solvency invariant (aggregate liability > sum of claimable claims).

Condition: `_apr0NetInterest != 0` AND `_apr0NetInterest * 1e18 < apr0TotalPrincipal`. For an 18-decimals underlying, a principal bucket of just `> 1e18` (1 token) makes rates of a few hundred wei round to 0; for realistic pools (e.g., 1M of an 18-dec token in the APR0 bucket), any `_apr0NetInterest < 1e6` is locked. The attacker surface is an ordinary user who calls `requestWithdraw` while `unscaledApr == 0`, growing `apr0TotalPrincipal` via `_requestWithdrawApr0` so that the honest `stopEpoch` override interest produces a nonzero-but-sub-1e18-scaled net interest.

### Impact Explanation
Permanently locked funds / accounting insolvency. Each affected epoch permanently inflates `pendingWithdraws` by up to `apr0TotalPrincipal / 1e18` wei of underlying with no possible claimant. The loss is bounded per epoch by `apr0TotalPrincipal * fee-Complement-share / 1e18`, but it is (a) non-dust when principal is large relative to realized interest — which is exactly the regime APR0 epochs create (override interest is arbitrary manager input), and (b) it corrupts the core liability invariant `pendingWithdraws == sum of claimable amounts`, which can make honest claim withdrawals of the *last* claimant revert for insufficient vault balance or leave tokens stranded. It is repeatable every epoch that satisfies the rounding condition.

### Likelihood Explanation
Medium. Requires an APR0 epoch (`unscaledApr == 0`) with open APR0 withdraw principal and a `stopEpoch` override `_expInterest` small enough relative to `apr0TotalPrincipal` that `(_interestNetOfFees * _principal / _totalPrincipalForSplit) * (1 - fee/FULL_ALLOC) * 1e18 < _principal`, i.e., net interest per principal < 1e-18. With large APR0 buckets and modest override interest this is routine rather than exotic, and no privileged misbehavior is needed — the honest manager's `stopEpoch` triggers it. No existing guard prevents it: `_skimDonatedAssets` only sweeps CDO-held donations, and the `!= 0` checks cover only the zero-interest case, not zero-rate.

### Recommendation
Revert or skip the APR0 allocation when the computed rate would be zero, mirroring the external fix — revert when the rounded-down value is 0 so the bookkeeping and the per-unit distribution cannot diverge:

```solidity
// contracts/strategies/idle/IdleCreditVault.sol
if (_apr0NetInterest != 0) {
    uint256 _rate = (_apr0NetInterest * 1e18) / _principal;
    require(_rate != 0, "apr0 rate rounds to 0");
    pendingWithdraws += _apr0NetInterest;
    apr0RateByEpoch[epochNumber] = _rate;
}
```

Alternatively, keep a per-epoch aggregate (`apr0InterestByEpoch`) and distribute pro-rata per user principal in `_settleApr0` rather than storing a 1e18-scaled rate, so no rounding-to-zero gap is created between the booked liability and claimable amounts.

### Proof of Concept
Foundry fork/unit PoC sketch (extend `test/foundry/IdleCreditVault.t.sol`):

```solidity
function testApr0RateRoundsToZeroLocksPendingWithdraws() external {
    // APR0 epoch with a large APR0 withdraw bucket
    vm.prank(owner);
    cdoEpoch.setIsAYSActive(false);
    vm.prank(manager);
    IdleCreditVault(address(strategy)).setAprs(0, 0);

    // Use an 18-decimal underlying deployment; deposit 2_000_000e18
    uint256 amount = 2_000_000 * 1e18;
    idleCDO.depositAA(amount);
    _startEpochAndCheckPrices(0);

    // stop epoch 0, force lastEpochApr to 0 so requestWithdraw goes APR0 path
    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.prank(manager);
    cdoEpoch.stopEpoch(0, 0);
    _forceLastEpochAprToZero();

    // user requests withdraw of half -> apr0TotalPrincipal ~= 1_000_000e18
    uint256 trancheReq = IERC20(AAtranche).balanceOf(address(this)) / 2;
    uint256 principal = cdoEpoch.requestWithdraw(trancheReq, address(AAtranche));

    _startEpochAndCheckPrices(1);

    // stop epoch 1 with override interest small vs principal:
    // netInterest ~= interest * principal/(tvl+principal); pick poolInterest so
    // _apr0NetInterest < apr0TotalPrincipal / 1e18  (e.g. poolInterest = 1e6 wei)
    uint256 poolInterest = 1e6;
    deal(defaultUnderlying, borrower, poolInterest + principal + amount);
    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.prank(manager);
    cdoEpoch.stopEpoch(0, poolInterest);

    IdleCreditVault vault = IdleCreditVault(address(strategy));
    uint256 rate = vault.apr0RateByEpoch(vault.epochNumber() - 1);
    uint256 pending = vault.pendingWithdraws();

    // rate rounded to 0 while pendingWithdraws was increased by _apr0NetInterest
    assertEq(rate, 0);
    assertGt(pending, principal); // includes phantom apr0 interest nobody can claim
}
```

Assert: `apr0RateByEpoch == 0` while `pendingWithdraws` exceeds the sum of claimable principal — the delta is permanently locked, reproducing the reported "unreachable rewards" on the credit-vault APR0 surface.

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L533-540)
```text
    if (_apr0NetInterest != 0) {
      // Funds owed to withdraw requesters increase by APR0 net interest.
      pendingWithdraws += _apr0NetInterest;
      // Save per-epoch net rate; each APR0 request accrues exactly once on its request epoch.
      apr0RateByEpoch[epochNumber] = (_apr0NetInterest * 1e18) / _principal;
    }
    // Close current APR0 bucket so it cannot accrue again on later stopEpoch calls.
    apr0TotalPrincipal = 0;
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L556-563)
```text
    // Move principal from "open APR0 bucket" to "settled bucket" (same principal, not duplicated).
    _apr0User.settledPrincipal += _principal;
    uint256 _rate = apr0RateByEpoch[_reqEpoch];
    if (_rate != 0) {
      // Convert per-epoch rate to claimable underlying interest.
      _apr0User.settledInterest += (_principal * _rate) / 1e18;
    }
    _apr0User.principal = 0;
```
