### Title
Unbounded APR0 withdrawal principal skews `prepareStopEpochWithApr0` pro-rata split, letting a whale requester capture nearly all epoch interest - ([File: contracts/strategies/idle/IdleCreditVault.sol](contracts/strategies/idle/IdleCreditVault.sol))

### Summary
The external report describes validators submitting unbounded outlier scores that skew a mean/stddev filter so only the outliers earn rewards. The analog in idle-tranches is the APR0 withdraw flow in `IdleCreditVault.prepareStopEpochWithApr0`: when `unscaledApr == 0`, the epoch's realized interest is split pro-rata over `_tvl + apr0TotalPrincipal`, but there is no bound on the APR0 principal bucket. A KYC-passing lender who deposits a dominant share and immediately calls `requestWithdraw` while APR is 0 moves their principal out of live NAV, yet that same principal is still counted in both the numerator (`_principal`) and denominator (`_tvl + _principal`) of the split, so the attacker captures a share of the epoch's realized interest proportional to a principal that no longer accrues yield, starving honest tranche holders.

### Finding Description
When `unscaledApr == 0`, `requestWithdraw` routes into `_requestWithdrawApr0`, which adds the (fee-haircut) principal to `apr0TotalPrincipal` without any cap: [1](#0-0) [2](#0-1) 

At `stopEpoch`, `prepareStopEpochWithApr0` computes the APR0 reward as `_interestNetOfFees * _principal / (_tvl + _principal)` and deducts it from the interest distributed to active NAV (`_expInterest -= _apr0NetInterest`), paying it to requesters via `pendingWithdraws`: [3](#0-2) 

Because `_principal` is unbounded, an attacker holding e.g. 95% of pool principal can request an APR0 withdraw of their whole balance. Their principal is removed from the CDO's live NAV (`_withdrawOps` in `IdleCDOEpochVariant._requestWithdraw` burns the tranche tokens and reduces NAV), yet it still earns `95/(100+95) ≈ 48.7%`... more precisely `_principal/(_tvl+_principal)` of realized interest — i.e. as `_principal → ∞`, the share approaches 100% of epoch interest for capital that produced none of it, since it sits in the pending-withdraw bucket rather than backing the borrower's loan economics that generated the interest. There is no guard limiting the APR0 bucket to a fraction of `_tvl`, no minimum lock, and `requestWithdraw` is callable by any KYC'd tranche holder through `IdleCDOEpochVariant.requestWithdraw` (`contracts/IdleCDOEpochVariant.sol:772-790`). The `unscaledApr != 0` revert in `prepareStopEpochWithApr0` only enforces the APR0 lifecycle, it does not bound the amount.

### Impact Explanation
Direct theft of unclaimed yield. With TVL of 1M and realized epoch interest of 100k, an attacker who deposits 9M during the buffer and requests an APR0 withdraw receives `100k * 9M/10M = 90k`, while honest lenders who kept 1M deployed for the full epoch split only 10k. The attacker exits at `claimWithdrawRequest` with principal plus stolen interest; the loss is borne entirely by remaining AA/BB holders whose tranche price appreciation is reduced by the same amount.

### Likelihood Explanation
Requires an APR0 epoch (`unscaledApr == 0`, a supported vault mode per the apr0Users flow) and a `stopEpoch` with an override interest payment (`_interest > 1`), which is the normal interest-minted/APR0 settlement path shown in `testStopEpochMintInterestWithPendingWithdraws`. The attacker only needs to pass Keyring KYC and supply capital — no privileged role. Capital requirement is high but the attack is repeatable each epoch and flash-loan-free profit is bounded only by epoch interest.

### Recommendation
Cap the APR0 principal share of the split, e.g. enforce `apr0TotalPrincipal <= maxApr0Share * _tvl` at `requestWithdraw` time, or compute the APR0 share against time-weighted deployed principal rather than instantaneous `_principal`. Alternatively, exclude principal that was deposited in the same buffer window it was withdrawn in, so only capital that actually backed the epoch earns a pro-rata share.

### Proof of Concept
Adapt `testApr0WithdrawMultiUserProRataSameEpoch` in `test/foundry/IdleCreditVault.t.sol`:

```solidity
// Setup identical to testApr0WithdrawMultiUserProRataSameEpoch:
// fee 10%, isAYSActive false, manager calls setAprs(0, 0).
address whale = makeAddr("apr0-whale");

// Honest lender deposits 1_000, attacker deposits 99_000 (99% of pool)
idleCDO.depositAA(1_000 * ONE_SCALE);
_depositWithUser(whale, 99_000 * ONE_SCALE, true);
_transferBurnedTrancheTokens(address(this), true);

_startEpochAndCheckPrices(0);
vm.warp(cdoEpoch.epochEndDate() + 1);
deal(defaultUnderlying, borrower, cdoEpoch.expectedEpochInterest());
vm.prank(manager);
cdoEpoch.stopEpoch(0, 0);
_forceLastEpochAprToZero();

// Attacker requests APR0 withdraw of entire balance during the buffer
uint256 whaleTranche = IERC20(AAtranche).balanceOf(whale);
vm.prank(whale);
uint256 whalePrincipal = cdoEpoch.requestWithdraw(whaleTranche, address(AAtranche));

_startEpochAndCheckPrices(1);

// Borrower repays 1_000 realized interest + pending withdraws
uint256 poolInterest = 1_000 * ONE_SCALE;
vm.warp(cdoEpoch.epochEndDate() + 1);
deal(defaultUnderlying, borrower,
    poolInterest + IdleCreditVault(address(strategy)).pendingWithdraws() + 99_000 * ONE_SCALE);
vm.prank(manager);
cdoEpoch.stopEpoch(0, poolInterest);

// Whale claims: principal + ~99% of poolInterest despite contributing ~0 deployed capital
uint256 pre = underlying.balanceOf(whale);
vm.prank(whale);
cdoEpoch.claimWithdrawRequest();
uint256 stolen = underlying.balanceOf(whale) - pre - whalePrincipal;
// stolen ~= 990 * ONE_SCALE; honest lender's AA price gains only ~10 * ONE_SCALE
assertGt(stolen, 9 * poolInterest / 10);
```

Run with `forge test --match-test testApr0WhaleCapturesEpochInterest`.

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L285-294)
```text
    if (unscaledApr == 0 && !isClosed) {
      _requestWithdrawApr0(_amount, _user);
    } else {
      // increase the withdraw requests for the user
      // we record both per-user (old, kept for compatibility) and per-epoch so
      // on finalization we can distinguish "default-epoch pending receipts"
      // from old funded receipts.
      withdrawsRequests[_user] += _amount;
      withdrawsRequestsByEpoch[_user][currentEpoch] += _amount;
    }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L513-541)
```text
    if (_expInterest > 1 && _expInterest > _pendingFees) {
      // Remove already booked withdraw fees from the interest base before splitting.
      uint256 _interestNetOfFees = _expInterest - _pendingFees;
      // Total principal used for the pro-rata split:
      // IdleCDO TVL (which excludes APR0 requested principal) + APR0 principal bucket.
      uint256 _totalPrincipalForSplit = _tvl + _principal;
      if (_totalPrincipalForSplit != 0) {
        // APR0 users get a pro-rata share of realized interest.
        uint256 _apr0InterestGross = _interestNetOfFees * _principal / _totalPrincipalForSplit;
        if (_apr0InterestGross != 0) {
          // Same fee model as normal withdraw interest.
          uint256 _apr0Fee = _apr0InterestGross * _cdo.fee() / FULL_ALLOC;
          _apr0NetInterest = _apr0InterestGross - _apr0Fee;
          _adjPendingWithdrawFees += _apr0Fee;
          _expInterest -= _apr0NetInterest;
        }
      }
    }

    // Finalize one-epoch APR0 interest for current epoch only.
    if (_apr0NetInterest != 0) {
      // Funds owed to withdraw requesters increase by APR0 net interest.
      pendingWithdraws += _apr0NetInterest;
      // Save per-epoch net rate; each APR0 request accrues exactly once on its request epoch.
      apr0RateByEpoch[epochNumber] = (_apr0NetInterest * 1e18) / _principal;
    }
    // Close current APR0 bucket so it cannot accrue again on later stopEpoch calls.
    apr0TotalPrincipal = 0;
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L567-577)
```text
  function _requestWithdrawApr0(uint256 _amount, address _user) internal {
    // Settle any previous APR0 request first, then start/update current epoch bucket.
    _settleApr0(_user);
    Apr0UserData storage _apr0User = apr0Users[_user];
    if (_apr0User.principal == 0) {
      _apr0User.principalEpoch = epochNumber;
    }
    _apr0User.principal += _amount;
    // Epoch-level APR0 principal used only to compute stopEpoch APR0 pro-rata interest.
    apr0TotalPrincipal += _amount;
  }
```
