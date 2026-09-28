### Title
Unsanitized `underlyingsRequested` lets anyone seize a lender's escrowed tranche tokens for free via `fullfillWriteOffRequest` - (File: contracts/IdleCreditVaultWriteOffEscrow.sol)

### Summary
`createWriteOffRequest` accepts a user-supplied `underlyingsRequested` with no lower bound, and `fullfillWriteOffRequest` lets any caller "fulfill" any user's request as long as `_underlyings >= currentRequest.underlyings`. A request created with `underlyingsRequested = 0` (not rejected) can therefore be fulfilled by any EOA paying 0 underlying tokens, while receiving the full escrowed tranche balance. This mirrors CVE-2019-16985: an unsanitized, attacker-reachable input enables a "delete/consume this record" primitive that destroys a victim's escrowed position without fair payment.

### Finding Description
In `createWriteOffRequest` (IdleCreditVaultWriteOffEscrow.sol:86-102) the only validation is `amount != 0` and `isEpochRunning()`. `underlyingsRequested` is stored verbatim:

```solidity
if (amount == 0) revert NotAllowed();
// no check on underlyingsRequested
userRequests[msg.sender] = WriteOffRequest({
  tranches: currentRequest.tranches + amount,
  underlyings: currentRequest.underlyings + underlyingsRequested
});
```

In `fullfillWriteOffRequest` (lines 123-155), the guard `if (currentRequest.tranches != _tranches || _underlyings < currentRequest.underlyings) revert WrongRequest();` passes for any request whose `underlyings == 0`, even with `_underlyings = 0`. The fulfiller then receives `IERC20Detailed(tranche).safeTransfer(msg.sender, _tranches)` — the victim's entire escrowed tranche position — while the victim receives `0 - 0` underlying. The `_user` parameter is fully attacker-chosen, exactly like the unsanitized `rec` parameter in the FusionPBX bug: attacker-controlled selection of which record is acted upon, combined with unvalidated stored input, yields destruction of another user's asset record.

Additionally, a lender who sets `underlyingsRequested` below the tranches' fair value (e.g., a UI default of 0, or a rounding error) has no recourse: fulfillment is permissionless and front-runnable, so the first MEV bot that observes a mispriced request takes the tranches. There is no minimum-price, oracle, or owner guard.

### Impact Explanation
Direct theft of escrowed tranche tokens. Loss is the full tranche balance of any request created with `underlyingsRequested = 0` (or any underpriced request). Broken invariant: one receipt one payout / fair exchange — a write-off request is supposed to trade tranches for the requested underlyings, not hand tranches away for nothing. None of the existing guards (`nonReentrant`, `Is0`, `WrongRequest`, `EpochNotRunning`) stop it.

### Likelihood Explanation
Requires a lender to create a request with zero (or severely underpriced) `underlyingsRequested`. The contract explicitly permits this input, and the escrow is a user-facing periphery contract where price entry errors are realistic; Medium likelihood consistent with the CVSS 6.5 source bug. The exploit itself is a single permissionless transaction with no capital requirement beyond gas.

### Recommendation
In `createWriteOffRequest`, revert when `underlyingsRequested == 0` (or enforce a sane minimum relative to deposited tranches). Optionally restrict `fullfillWriteOffRequest` to the `borrower` if third-party fulfillment is not a deliberate feature, since any permissionless fulfiller can snipe underpriced requests.

### Proof of Concept
```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.10;
import "forge-std/Test.sol";
import {IdleCreditVaultWriteOffEscrow} from "contracts/IdleCreditVaultWriteOffEscrow.sol";
import {IERC20Detailed} from "contracts/interfaces/IERC20Detailed.sol";

contract WriteOffEscrowZeroPriceTest is Test {
    // fork mainnet at a block where the vault epoch is running
    IdleCreditVaultWriteOffEscrow escrow =
        IdleCreditVaultWriteOffEscrow(WRITE_OFF_ESCROW); // deployed escrow proxy
    IERC20Detailed tranche = IERC20Detailed(escrow.tranche());
    IERC20Detailed underlying = IERC20Detailed(escrow.underlying());

    function test_stealEscrowedTranchesForZero() public {
        address lender = makeAddr("lender");
        address attacker = makeAddr("attacker");

        // Lender deposits 100e18 tranches, mistakenly requests 0 underlyings
        deal(address(tranche), lender, 100e18);
        vm.startPrank(lender);
        tranche.approve(address(escrow), 100e18);
        escrow.createWriteOffRequest(100e18, 0); // allowed: no check
        vm.stopPrank();

        // Attacker fulfills with _underlyings = 0, no underlying needed
        vm.prank(attacker);
        escrow.fullfillWriteOffRequest(lender, 100e18, 0);

        assertEq(tranche.balanceOf(attacker), 100e18);   // attacker got tranches
        assertEq(underlying.balanceOf(lender), 0);       // lender got nothing
        (uint256 t, ) = escrow.userRequests(lender);
        assertEq(t, 0);                                   // request deleted
    }
}
```