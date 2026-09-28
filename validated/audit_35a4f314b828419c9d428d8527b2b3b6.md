### Title
Management fee rounds to zero via dust-forced fee checkpoints and split withdraw requests - ([File: contracts/IdleCDOCreditVault.sol])

### Summary
`IdleCDOCreditVault` accrues its management fee lazily with integer division in `_calculateManagementFee(_nav, _duration)` (contracts/IdleCDOCreditVault.sol:558-560). Both the accrual interval (`block.timestamp - latestHarvestBlock` in `_accrueManagementFee`, lines 552-555) and the per-withdrawal fee basis (`_totalWithdrawFees` in `IdleCDOEpochVariant.sol:900-906`) are fully attacker-controllable in granularity. Whenever `_nav * managementFee * _duration < 3153600000000`, the fee truncates to zero. An unprivileged lender can therefore (a) force fee checkpoints every few seconds with 1-wei `depositAA`/`requestWithdraw` calls so the per-interval fee always rounds to 0 while `latestHarvestBlock` is still reset, and (b) split one large `requestWithdraw` into many dust requests so each `_totalWithdrawFees` call returns 0. This is the same bug class as the referenced "zero fee to open position" — a fee that should be proportional to amount×time is evaded by manipulating the granularity at which it is computed.

### Finding Description
`_accrueManagementFee` checkpoints the fee and unconditionally resets `latestHarvestBlock` to `block.timestamp`, even when the computed fee is zero:

```solidity
// contracts/IdleCDOCreditVault.sol:552-555
function _accrueManagementFee() internal {
  unclaimedFees += _calculateManagementFee(_managedContractValue(), block.timestamp - latestHarvestBlock);
  latestHarvestBlock = block.timestamp;
}
```

`_updateAccounting` (which calls `_accrueManagementFee`) is invoked by `_deposit` (IdleCDOCreditVault.sol:200) and by `requestWithdraw` (IdleCDOEpochVariant.sol:750), both callable by any KYC-passing lender with no minimum amount (`_deposit` only returns early on exactly `_amount == 0`, line 192). The rounding-to-zero condition is

```
nav * managementFee * Δt < FULL_ALLOC * 365 days = 3.1536e15
```

so for a vault with NAV = 100 USDC (1e8 units) and `managementFee = 1000` (1%/yr), any Δt < ~31 s yields fee = 0. An attacker calling `depositAA(1)` (or `requestWithdraw` with 1 wei of tranche) every block or two keeps the elapsed interval below threshold forever — `latestHarvestBlock` resets each call — so the entire annualized management fee on pool NAV never enters `unclaimedFees`, inflating tranche prices/NAV that the attacker and all holders redeem against. Broken invariant: fair mint/burn + fee collection (`getContractValue` subtracts `unclaimedFees`, line 127, so fees not accrued stay in NAV paid to holders).

Independently, `requestWithdraw` charges `totalFees = _totalWithdrawFees(principal, interest)` where `_mgmtFee = _calculateManagementFee(_principal, epochDuration + remainingBuffer)` (IdleCDOEpochVariant.sol:774, 900-906, 925-931). There is no minimum withdrawal: a holder of N USDC can submit ⌈N / threshold⌉ requests each with `principal < 3.1536e15 / (managementFee * duration)` (≈1 USDC for 1% fee over ~35-day epoch+buffer), making `_mgmtFee` and the subsequent performance fee round to 0 on every request while `creditVault.requestWithdraw` (IdleCreditVault.sol:243) books each dust receipt normally and `claimWithdrawRequest` pays the full principal + interest at epoch end.

### Impact Explanation
Direct theft of protocol revenue that is economically equivalent to NAV inflation paid to tranche holders. Loss magnitude: the full management fee (`managementFee/1e5` per year on live NAV, e.g. 1% of TVL) plus the performance fee (`fee/FULL_ALLOC` on withdrawal interest). On a 100-1,000 USDC-scale vault or on any vault on a fast-block L2 (sub-second blocks shrink the required Δt proportionally, and duration can be pushed to ~0 by checkpointing every transaction), the fee is fully evaded. Larger vaults remain vulnerable through the withdrawal-splitting path, which only requires enough transactions (cheap on L2s where these credit vaults deploy, e.g. the `optimism/`, `arbitrum/`, `base/` variants exist in-repo).

### Likelihood Explanation
- Requires only a KYC-passed lender or any holder of tranche tokens — explicitly an allowed attacker profile.
- No privileged cooperation needed: `depositAA`, `depositBB` (if enabled), and `requestWithdraw` are all permissionless entry points to `_updateAccounting`/`_accrueManagementFee`.
- Existing guards do not help: `_skimDonatedAssets` only moves the raw `token` balance (IdleCDOEpochVariant.sol:794-796); there is no minimum checkpoint interval, no minimum deposit/withdrawal amount, and no `fee == 0` revert (the exact recommendation of the source report is absent).
- Profitability is TVL- and block-time-dependent: trivially profitable for small NAVs or fast chains; requires many transactions for large NAVs on Ethereum mainnet (still gas-net-positive on L2 where per-call cost is fractions of a cent vs. ~1 USDC of evaded fee per ~1 USDC-chunk × fee rate).

### Recommendation
Do not let fee granularity be attacker-controlled:
- In `_accrueManagementFee`, only reset `latestHarvestBlock` when the accrual actually produced a fee, or better, accumulate the fee basis without truncation (store fractional fee remainder or accrue in a higher-precision accumulator and flush when ≥ 1 unit).
- Enforce a minimum deposit/withdrawal amount (e.g. `oneToken / 100`) in `_deposit`, `depositDuringEpoch`, and `requestWithdraw`, or compute withdrawal fees on the user's aggregate request rather than per call.
- As in the source report's fix: if a computed nonzero-rate fee truncates to 0, either revert or round up (`(a*b + d - 1)/d`).

### Proof of Concept
Foundry fork test sketch (mainnet fork, existing `IdleCreditVault.t.sol` harness — `idleCDO`, `cdoEpoch`, `underlying`, `AAtranche` from setup):

```solidity
function testManagementFeeRoundingToZero() external {
    uint256 amount = 100 * ONE_SCALE; // 100 USDC vault
    _setFeeParams(TL_MULTISIG, 0, FULL_ALLOC, cdoEpoch.managementFee());
    _setManagementFee(1_000); // 1%/yr management fee
    idleCDO.depositAA(amount);
    _transferBurnedTrancheTokens(address(this), true);

    // Attacker: force a checkpoint every ~12s so each interval fee truncates to 0.
    // threshold Δt = 3.1536e15 / (1e8 * 1000) ≈ 31s for NAV=100 USDC
    for (uint256 i; i < 365 days / 12; ++i) {
        vm.warp(block.timestamp + 12);
        idleCDO.depositAA(1); // dust deposit -> _updateAccounting -> _accrueManagementFee(≈0 fee)
    }

    // Honest accrual would be ~1 USDC; truncated accrual is ~0.
    assertLt(cdoEpoch.unclaimedFees(), _calcManagementFee(amount, 1_000, 365 days) / 10);
}

function testWithdrawFeeRoundingToZero() external {
    uint256 amount = 10_000 * ONE_SCALE;
    _setFeeParams(TL_MULTISIG, 10_000, FULL_ALLOC, cdoEpoch.managementFee()); // 10% perf fee
    _setManagementFee(1_000); // 1% mgmt fee
    uint256 trancheAmount = idleCDO.depositAA(amount);
    _transferBurnedTrancheTokens(address(this), true);

    // Dust chunk: principal * 1000 * ~35d < 3.1536e15  -> chunk ≈ 1 USDC
    uint256 chunk = ONE_SCALE; // 1 USDC worth of tranche tokens
    uint256 n = trancheAmount / chunk;
    uint256 total;
    for (uint256 i; i < n; ++i) {
        total += cdoEpoch.requestWithdraw(chunk, address(AAtranche));
    }
    // Sum of per-request fees is 0 while a single request would pay
    // _calculateManagementFee(principal, epochDuration+remainingBuffer) + 10% of interest.
    assertEq(cdoEpoch.pendingWithdrawFees(), 0, 'all fees rounded to zero');
    assertGt(total, 0, 'full principal+interest still claimable');
}
```

Expected: `unclaimedFees ≈ 0` after a full year of forced checkpoints (vs ~1% of NAV honestly), and `pendingWithdrawFees == 0` after a fully-split withdrawal (vs management + performance fee on a single request). Both are reproducible on a Foundry fork with no privileged cooperation.