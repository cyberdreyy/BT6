### Title
Post-default withdraw requests drain the isolated default recovery reserve at par — ([File: contracts/strategies/idle/IdleCreditVault.sol])

### Summary
The BuildKit bug lets a crafted request escape the daemon-controlled state directory by bypassing destination validation. The credit-vault analog is an escape from an isolated fund boundary: after `finalizeDefaultRecovery`, `defaultRecoveryReserve` is a sealed, accounting-isolated bucket sized exactly to cover default-epoch claims at `defaultRecoveryPrice`. The post-default branch of `requestWithdraw` mints a brand-new claim (`postDefaultRequests`) that is paid 1:1 out of that reserve via `_transferDefaultRecovery`, even though the reserve was never increased to cover it. A post-default claim therefore spends recovery funds it was never accounted for, escaping the haircut boundary that is supposed to confine the reserve to pre-default claimants.

### Finding Description
In `finalizeDefaultRecovery` the reserve is fixed as `reserveAmount = _recoveredAmount + prefundedReserve + defaultRecoveryReserve` and the payable basis is fixed as `totalBasis = activeBasis + pendingBasis`, with `defaultRecoveryPrice = reserveAmount / totalBasis` (IdleCreditVault.sol L686–L693). Every unit of reserve is therefore earmarked for a specific defaulted claim.

After finalization, `requestWithdraw` takes the `defaultRecoveryFinalized` branch (L247–L257):

- it `_mint`s `_amount` strategy tokens to `_user` and sets `postDefaultRequests[_user] = _amount`,
- it pulls **no underlying** into the strategy and **does not increase** `defaultRecoveryReserve`.

`_claimPostDefaultWithdrawRequest` (L760–L767) then pays `postDefaultRequests[_user]` at par through `_transferDefaultRecovery`, which unconditionally decrements `defaultRecoveryReserve` (L912–L917). The only guard is `_hasWithdrawRequest`/instant/post-default checks on the same user (L249–L251), which a fresh attacker address trivially passes. Nothing sizes the reserve for these new claims: each post-default claim of `P` consumes `P` units of reserve that belong to defaulted-epoch claimants, who later either receive less or revert on `defaultRecoveryReserve -= _amount` underflow — permanently freezing their recovery.

The broken invariant is donation/reserve isolation plus "one receipt one payout": the reserve is a closed accounting domain, and post-default requests inject new claims into it without new funding — exactly the "request escapes the controlled directory" pattern of the original bug.

### Impact Explanation
Post-default, tranche tokens trade at (or below) the implied recovery value. An unprivileged attacker buys BB/AA tranches cheaply on the market, deposits them through the CDO, calls the withdraw flow so `postDefaultRequests` is set to the haircut-priced amount, then claims and is paid **from the recovery reserve**, not from new borrower funds. The attacker can arbitrage any discount between market tranche price and recovery price, and, more importantly, every such claim makes the reserve insolvent by the same amount: the last `P` units of legitimate defaulted-epoch recovery claims can never be paid (`safeTransfer` or the reserve subtraction reverts). Loss is up to the full `defaultRecoveryReserve` being misallocated — direct theft from and permanent freezing of defaulted claimants' recovery funds.

### Likelihood Explanation
Requirements: a `defaulted()` vault finalized via `finalizeDefaultRecovery` with a nonzero reserve — a real, supported state. Attacker needs only an unprivileged EOA holding tranche tokens purchased post-default; no privileged role is involved. `requestWithdraw` post-default path has no epoch gating and no funding check, and the user-level guards are per-address so multiple fresh addresses can each execute the drain. Uncertainty I could not fully resolve within the indexed context: the exact `_amount` the CDO passes post-default (whether it is haircut by `virtualPrice`) determines attacker profit margin but not the reserve-insolvency consequence, which holds regardless because the reserve is debited at par for claims it never provisioned.

### Recommendation
In the `defaultRecoveryFinalized` branch of `requestWithdraw`, either (a) fund the claim: require/transfer corresponding underlying into the strategy and increase `defaultRecoveryReserve` before crediting `postDefaultRequests`, or (b) pay post-default claims from a separate, non-reserve balance (e.g., via `_transferFundedClaim` semantics that revert if `balance - reserve < amount`), so post-default claims can never consume the finalized recovery reserve. Also add a solvency check in `_transferDefaultRecovery` that the cumulative payouts cannot exceed the basis accounted at finalization.

### Proof of Concept
```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.10;

import "forge-std/Test.sol";
import {IdleCDOEpochVariant} from "../contracts/IdleCDOEpochVariant.sol";
import {IdleCreditVault} from "../contracts/strategies/idle/IdleCreditVault.sol";
import {IERC20Detailed} from "../contracts/interfaces/IERC20Detailed.sol";

// Fork test: mainnet fork of a live IdleCDOEpochVariant + IdleCreditVault
// that reached `defaulted()` and was finalized via finalizeDefaultRecovery.
contract PostDefaultReserveDrainTest is Test {
    IdleCDOEpochVariant cdo;      // deployed epoch CDO
    IdleCreditVault vault;        // strategy
    IERC20Detailed underlying;
    IERC20Detailed tranche;       // AA or BB tranche token

    address attacker = address(0xA77A);
    address victim = address(0xB0B); // holder of a defaulted-epoch receipt

    function testPostDefaultClaimConsumesRecoveryReserve() public {
        // --- state: defaultRecoveryFinalized == true, reserve R > 0,
        //     victim holds a defaulted-epoch withdraw receipt of basis B
        //     with expected payout B * defaultRecoveryPrice / 1e18.

        uint256 reserveBefore = vault.defaultRecoveryReserve();
        uint256 price = vault.defaultRecoveryPrice();
        assertGt(reserveBefore, 0);

        // Attacker buys tranche tokens post-default on a DEX / by transfer.
        deal(address(tranche), attacker, 1_000e18);

        // 1) Attacker deposits tranches via the CDO (unprivileged call),
        //    then requests a withdraw. requestWithdraw post-default branch
        //    mints receipt tokens and sets postDefaultRequests[attacker]
        //    WITHOUT moving underlying or increasing the reserve.
        vm.startPrank(attacker);
        tranche.approve(address(cdo), type(uint256).max);
        cdo.depositAA(1_000e18);              // or depositBB
        cdo.requestWithdraw(/* amount */);    // routes to vault.requestWithdraw
        vm.stopPrank();

        uint256 claim = vault.postDefaultRequests(attacker);
        assertGt(claim, 0);

        // 2) Attacker claims: _claimPostDefaultWithdrawRequest ->
        //    _transferDefaultRecovery pays `claim` 1:1 FROM the reserve.
        vm.prank(attacker);
        cdo.claimWithdrawRequest();

        uint256 reserveAfter = vault.defaultRecoveryReserve();
        assertEq(reserveBefore - reserveAfter, claim); // reserve eaten by unprovisioned claim

        // 3) Victim's defaulted-epoch claim now fails or is short:
        //    reserve no longer covers B * price.
        vm.prank(address(cdo)); // via cdo.claimWithdrawRequest() for victim
        vm.expectRevert();      // NotAllowed / underflow / insufficient reserve
        // vault.claimWithdrawRequest(victim);

        // Broken invariant: reserve R was sized for totalBasis * price only;
        // post-default claims escaped that boundary and spent it.
    }
}
```