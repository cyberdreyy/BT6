### Title
Mid-epoch deposit over-mints tranche shares by crediting unearned buffer-period interest - ([File: contracts/IdleCDOEpochVariant.sol](contracts/IdleCDOEpochVariant.sol))

### Summary
Analogous to CVE-2018-10538 — where an unchecked `bytes_to_copy` size computation caused WavPack to allocate memory for the wrong amount — `depositDuringEpoch` computes the shares to mint using an interest figure that includes the **entire** `bufferPeriod`, even though the borrowed funds are only outstanding until `epochEndDate`. The minted share count is therefore systematically larger than the principal + interest that will actually flow back into the vault, leaving the last withdrawers undercollateralized.

### Finding Description
In `depositDuringEpoch` (IdleCDOEpochVariant.sol:656-733), a KYC-passing lender deposits mid-epoch and receives shares sized so they redeem `_amount + trancheInterest` at epoch end:

- `interest = _calcInterest(_amount) * (remaining + buffer) / (epochDuration + buffer)` where `remaining = epochEndDate - block.timestamp` and `buffer = bufferPeriod` (lines 691-697).
- `_calcInterest` returns interest for the full `epochDuration + buffer` horizon, so the depositor is credited interest covering `remaining + buffer`.
- The deposited underlyings are immediately sent to the borrower (`_transferUnderlyings(_borrower(), _amount)`, line 732), and `expectedEpochInterest += interest` records the credited amount as expected income (line 728).

The borrower holds these funds only for `remaining` (until `epochEndDate`, when repayment occurs and the withdrawal buffer begins). The `buffer` portion of the credited interest corresponds to a period during which the borrower no longer holds the mid-epoch depositor's funds — no interest accrues to the vault during the buffer on this money. Yet the share mint at line 724 bakes `trancheInterest` (which includes the buffer share) into the redemption value:

```solidity
_minted = (_amount + trancheInterest) * _trancheTotSupply / expectedFinal;
```

This is the vault analog of the WavPack bug: a size/quantity calculation (`bytes_to_copy` → `remaining + buffer` time weight) that produces an allocation larger than what actually backs it. The minted receipt promises a payout (`_amount + trancheInterest`) that exceeds the real inflow (`_amount + interest_for_remaining`).

Note on uncertainty: whether this is a deficit depends on the borrower's repayment terms. If the borrower contractually owes interest over `epochDuration + buffer` (i.e., repayment is only due after the buffer), the credit is consistent. However, the epoch ends at `epochEndDate` and `stopEpoch` pulls funds back then; nothing in `depositDuringEpoch` or the buffer accounting shows the borrower paying buffer-period interest on funds returned at `epochEndDate`. Under the natural reading (repayment at epoch end), each mid-epoch deposit over-promises by `trancheInterest * buffer / (remaining + buffer)`.

### Impact Explanation
Broken invariant: **one receipt one payout / solvency**. The sum of all tranche-token redemption values at epoch end exceeds the NAV that will actually be recovered from the borrower by the phantom buffer-interest component of every mid-epoch deposit. Concretely:

- Attacker deposits `_amount` at time `t` close to `epochEndDate` (small `remaining`), maximizing the ratio `buffer / (remaining + buffer)` — the credited-but-unearned interest fraction approaches `buffer / buffer = ~100%` of `_calcInterest(_amount)`.
- At epoch end, the attacker redeems `_amount + trancheInterest` while the vault only received `_amount + interest_for_remaining` from the borrower for that deposit.
- The shortfall is socialized: other tranche holders' `expectedFinal`-based price is diluted, and the last withdrawers in the epoch's claim flow (`claimWithdrawRequest` / `withdrawsRequestsByEpoch`) find the vault underfunded.

Guards that do **not** stop this: `isWalletAllowed` (attacker is a KYC-passing lender, in-scope), `isAYSActive`/`isProgrammableBorrower`/`isDepositDuringEpochDisabled` checks (the path is explicitly designed to be used when these are false and the epoch is running — an honest borrower/manager configuration), `_guarded` (only caps TVL), and `_skimDonatedAssets` (irrelevant). The `skipDefaultCheck` and default-revert paths trigger only on realized losses, not on this systematic over-mint.

Quantified loss per attack deposit: approximately `_amount * apr * buffer / 31536000` worth of underlying (the buffer-fraction of `_calcInterest(_amount)`), repeatable every epoch with attacker-sized deposits; with `buffer` large relative to `remaining`, the attacker can capture nearly a full buffer-period of interest per epoch while supplying funds for an arbitrarily small `remaining`.

### Likelihood Explanation
Medium. Requires the vault to run in fixed-APR, non-AYS, non-programmable mode with `isDepositDuringEpochDisabled == false` — a supported configuration, since `depositDuringEpoch` exists precisely for that mode. Attacker is an unprivileged KYC-passed lender. No privileged misbehavior needed; the borrower/manager/owner act honestly. The value extracted scales with deposit size and with how late in the epoch the deposit lands (small `remaining` maximizes the free buffer interest), so a whale lender can repeat this each epoch. The only mitigating factor is that honest managers may simply keep mid-epoch deposits disabled, which reduces exploitability in practice but does not fix the accounting error.

### Recommendation
Credit mid-epoch depositors only with interest for the time their funds are actually at risk. Change the scaling to exclude the buffer for the attacker-supplied leg, e.g. `interest = _calcInterest(_amount) * remaining / (epochDuration + buffer)` (or recompute `_calcInterest` over `remaining` directly), unless the borrower contract verifiably owes buffer-period interest on funds returned at `epochEndDate` — in which case document that invariant and assert it. Also add an epoch-end solvency check that `expectedEpochInterest` never exceeds interest actually repayable by the borrower.

### Proof of Concept
Foundry fork test sketch:

```solidity
// Setup: credit vault in fixed-APR mode, isAYSActive=false, isDepositDuringEpochDisabled=false,
// bufferPeriod > 0, epoch running, honest borrower, attacker = KYC'd lender.
// Assume epochDuration = 90 days, bufferPeriod = 7 days.

function testMidEpochOverMint() public {
    // 1. Epoch started; honest LPs deposited earlier. Warp to near epochEndDate:
    vm.warp(epochEndDate - 1 days); // remaining = 1 day, buffer = 7 days

    uint256 amount = 1_000_000e6; // USDC
    uint256 supplyBefore = IERC20(AATranche).totalSupply();
    uint256 navBefore    = cdo.lastNAVAA();

    // 2. Attacker deposits mid-epoch
    vm.prank(attacker);
    uint256 minted = cdo.depositDuringEpoch(amount, AATranche);

    // 3. Credited interest covers remaining + buffer = 8 days,
    //    but borrower holds the funds only 1 day.
    uint256 credited = cdo.expectedEpochInterest(); // increased by ~8 days of interest

    // 4. Warp past epochEndDate; honest borrower repays principal + 1 day interest.
    vm.warp(epochEndDate + 1);
    // borrower.repay() called via manager stopEpoch flow (honest).

    // 5. Attacker claims via claimWithdrawRequest / withdrawal queue:
    uint256 attackerOut = claim(attacker); // returns amount + ~8 days interest share
    // attackerOut > amount + interest earned on amount for 1 day

    // 6. Vault NAV < sum of all outstanding redemption values:
    assert(cdo.getContractValue() < totalOwedToWithdrawers());
    // Last claimers revert on insufficient balance or receive less than owed.
}
```

The core assertion: `expectedEpochInterest` after the deposit exceeds the interest the borrower can repay for the actual `remaining` holding period by `_calcInterest(_amount) * buffer / (epochDuration + buffer)`, and the attacker's minted shares redeem that phantom amount — directly mirroring WavPack's oversized `bytes_to_copy` producing an allocation the input never backed.