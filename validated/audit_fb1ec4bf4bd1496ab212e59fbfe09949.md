### Title
Mid-epoch deposits ignore `bufferPeriod` management fees, over-minting tranche shares to late joiners - (File: contracts/IdleCDOEpochVariant.sol)

### Summary
`depositDuringEpoch` prices new tranche shares against an expected end-of-epoch NAV, crediting the depositor interest for `remaining + buffer` time but subtracting management fees only for `remaining` time. Because `_accrueManagementFee` continues charging management fees on live NAV through the buffer period, the minted share count is systematically too high whenever `bufferPeriod > 0` and `managementFee > 0`. An unprivileged, KYC-passing lender deposits late in a running epoch and is minted more tranche tokens than the fair discounted amount, diluting existing holders at `stopEpoch`/`_updateAccounting`. This mirrors the Spartan Protocol bug class: a flawed liquidity-share calculation lets a depositor extract value belonging to other pool participants.

### Finding Description
In `depositDuringEpoch` (contracts/IdleCDOEpochVariant.sol:656-733):

- The depositor's gross interest is computed for the time they actually participate, **including the full buffer**: `interest = _calcInterest(_amount) * (remaining + buffer) / (epochDuration + buffer)` (lines 691-697).
- But the net-of-fees credit uses `_calculateManagementFee(_amount, remaining)` — fee for `remaining` only (line 712), and similarly for existing holders `_calculateManagementFee(lastNAVAA + lastNAVBB, remaining)` (line 706).
- Management fees, however, accrue on live NAV continuously via `_accrueManagementFee` inside `_updateAccounting` (contracts/IdleCDOCreditVault.sol:222-223), which runs on every deposit/withdraw during the buffer period too. The depositor's `_amount` will be fee-assessed for `remaining + buffer`, not just `remaining`.
- The mint formula `_minted = (_amount + trancheInterest) * _trancheTotSupply / expectedFinal` (line 724) therefore overstates `trancheInterest` and produces `minted` corresponding to a `fee_remaining`-only price, while realized end-of-epoch NAV is net of `fee_remaining+buffer`.

Since `minted` is fixed at deposit time but the realized tranche NAV is lower than priced, each mid-epoch depositor redeems more than `amount + fairNetInterest` at epoch end; the shortfall is socialized onto pre-existing holders of the same tranche. Over-mint ≈ `managementFee/1e4 * _amount * bufferPeriod/(365 days)` per deposit, attacker-controlled and repeatable up to `_guarded` limits.

Note the same missing-buffer-fee appears in `trancheExpected` for existing holders, which raises `expectedFinal` slightly and partially offsets — but the depositor's own fee shortfall is not fully offset because `trancheInterest` enters the numerator directly while `expectedFinal` is dominated by `lastNAV`.

### Impact Explanation
Direct theft via dilution: an attacker lending late in an epoch (worst case `remaining → 0`, maximal `bufferPeriod`) is minted shares priced as if no management fee accrues during the buffer, then redeems principal + buffer interest at `stopEpoch` while paying only `remaining` fees. Quantified extraction per deposit ≈ `_amount * managementFee * bufferPeriod / (FULL_ALLOC * 365 days)` of other holders' NAV, e.g. 1% mgmt fee, 30-day buffer, 10M deposit → ~8.2k units of NAV stolen, repeatable each epoch.

### Likelihood Explanation
Requires: `isDepositDuringEpochDisabled == false`, `isAYSActive == false`, non-programmable borrower, `isEpochRunning`, `bufferPeriod > 0`, `managementFee > 0`, and a KYC-passing (`isWalletAllowed`) attacker — all realistic configurations (buffer periods exist to cover withdrawal settlement). No privileged collusion needed; owner/manager calls (`startEpoch`, `stopEpoch`) stay honest. Guards (`_skimDonatedAssets`, `_guarded`, tranche-supply check) do not prevent it.

### Recommendation
In `depositDuringEpoch`, compute both fee terms over the actual participation window `remaining + buffer` (matching the interest window), i.e. `_calculateManagementFee(_amount, remaining + buffer)` and `_calculateManagementFee(lastNAVAA + lastNAVBB, remaining + buffer)`, or document/verify that management fee accrual is intentionally frozen during `bufferPeriod` and align `_accrueManagementFee` accordingly.

### Proof of Concept
Foundry fork PoC sketch:

```solidity
// Setup: mgmtFee = 5%, perfFee = 0, epochDuration = 30d, bufferPeriod = 30d,
// scaled APR set via setAprsWithBuffer; AA-only or AA+BB pool, isAYSActive = false.
idleCDO.depositAA(1_000_000e6);                 // honest depositor
cdoEpoch.setIsAYSActive(false);                 // owner
startEpoch();
vm.warp(epochEndDate - 1 days);                 // attacker joins late
cdoEpoch.setIsDepositDuringEpochDisabled(false); // owner
uint256 minted = cdoEpoch.depositDuringEpoch(1_000_000e6, AATranche); // attacker

// Fair minted should charge mgmt fee on (remaining + buffer) = 31 days of fee;
// contract charges only ~1 day. Compare:
uint256 fairInterest = _calcInterest(amount) * (remaining + buffer) / (epochDuration + buffer);
uint256 fairNet = fairInterest - mgmtFee(amount, remaining + buffer);
uint256 fairMinted = (amount + fairNet) * supply / expectedFinal_withBufferFee;
assertGt(minted, fairMinted);                   // over-mint

// Warp past epochEnd, stopEpoch with borrower repaying principal+interest:
stopEpoch(apr, 0);
// Attacker's claim = minted * priceAA exceeds amount + net(fair) fees;
// honest AA holder's virtualBalance < NAV0 + fairShare → value extracted ≈
// mgmtFee * amount * bufferPeriod / (FULL_ALLOC * 365 days).
```