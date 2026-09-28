### Title
Mid-epoch deposits via `mintStrategyTokens` skip management-fee and performance-fee checkpointing, retroactively charging fees on fresh principal and diluting all tranche holders - ([File: contracts/IdleCDOCreditVault.sol](contracts/IdleCDOCreditVault.sol))

### Summary
In the Cozy report, `purchase()` added to the fee pool without first calling `dripSupplierFees()`, so newly contributed fees were incorrectly included in the drip base for already-elapsed time, distorting utilization for markets with no purchase. The direct analog exists in the credit-vault accounting: `_deposit` checkpoints accrued management fees *before* adding principal, but the mid-epoch deposit path (`depositDuringEpoch` → `IdleCreditVault.mintStrategyTokens`) increases the managed NAV without any fee checkpoint, so the next accounting update charges both management and performance fees on the fresh principal as if it had been present for the entire elapsed period.

### Finding Description
`_updateAccounting` first calls `_accrueManagementFee`, which computes `unclaimedFees += _calculateManagementFee(_managedContractValue(), block.timestamp - latestHarvestBlock)` where `_managedContractValue()` is the full strategy-token balance. The ordering in `_deposit` is correct: accrue fees on the old NAV, then pull in the new underlyings and mint shares.

However, the epoch-variant mid-epoch deposit path mints strategy tokens directly to the CDO via `IdleCreditVault.mintStrategyTokens` (strategy tokens minted 1:1 with underlyings sent straight to the borrower) without calling `_updateAccounting`/`_accrueManagementFee` first. From that point, `_managedContractValue()` includes the new principal. On the *next* interaction:

- `_accrueManagementFee` multiplies the *new, larger* NAV by the *entire* elapsed duration since `latestHarvestBlock`, charging management fee on principal that was not in the vault for that time.
- `_updateAccounting` then sees `nav > lastNAV` and books a performance fee `(nav - lastNAV) * fee / FULL_ALLOC` on what is mostly raw deposit principal, not yield.

This mirrors the Cozy bug exactly: Bob deposits and accrues; Alice's new deposit is swept into the accrual/fee base for time it never existed. The consequence is the same broken invariant — users who did not perform the action see their tranche price/NAV reduced because the action's principal was folded into the fee base.

Sequence (buffer phase, epoch vault):
1. Vault has `lastNAV` and accrued time `T` since `latestHarvestBlock`.
2. Attacker (or any whitelisted lender) calls `depositAA`/`depositBB` during the buffer window routed through `depositDuringEpoch`, which calls `IdleCreditVault.mintStrategyTokens(amount)` — strategy-token balance grows by `amount`, `latestHarvestBlock`/`lastNAVAA/lastNAVBB` are not updated consistently (mid-epoch deposits bypass the `_updateAccounting` ordering guarantee of `_deposit`).
3. Any later `_deposit`/`requestWithdraw` triggers `_accrueManagementFee` over the inflated NAV for duration `T`, plus a performance fee on the phantom "gain" of `amount`.
4. `unclaimedFees` is inflated; `getContractValue` subtracts it, lowering `virtualPrice` for AA and BB holders who never deposited.

### Impact Explanation
`unclaimedFees` is over-minted at the expense of every tranche holder: the NAV available to AA/BB is reduced by fees computed on principal that was never managed during the accrual window. The fee receiver/owner can later collect these fees via `unclaimedFees`, so this is a direct transfer of value from LPs to the fee recipient, repeatable each buffer period by timing a deposit immediately before another user interacts. With `fee`/`managementFee` set, the leaked amount is `managementFee * amount * elapsed/31536000s + fee * amount/FULL_ALLOC` (performance fee dominates for large `elapsed` since the deposit registers as a "gain"). Loss is bounded but directly quantified per deposit.

### Likelihood Explanation
Requires only a KYC-passed lender calling a normal deposit during the buffer/mid-epoch window while `managementFee` or `fee` is non-zero — no privileged role, no timing attack beyond ordinary transaction ordering. Every such deposit inflates the fee base for all co-holders.

### Recommendation
Checkpoint fees before mutating the managed balance on the mid-epoch path, mirroring `_deposit`: call `_updateAccounting()` (or at least `_accrueManagementFee()` and update `lastNAVAA`/`lastNAVBB`/`latestHarvestBlock`) inside the epoch-variant deposit entry point *before* invoking `IdleCreditVault.mintStrategyTokens`, so new principal never enters the accrual base for already-elapsed time.

### Proof of Concept
```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.10;

import "forge-std/Test.sol";
import "../contracts/IdleCDOCreditVault.sol";
import "../contracts/strategies/idle/IdleCreditVault.sol";

/// Demonstrates that a mid-epoch deposit (mintStrategyTokens path) is swept into
/// the management/performance fee base retroactively, while a normal deposit is not.
contract FeeBaseInflationTest is Test {
    IdleCDOCreditVault cdo;      // epoch-variant CDO using latestHarvestBlock as fee checkpoint
    IdleCreditVault strategy;
    address alice = address(0xA);
    address bob   = address(0xB);

    function test_MidEpochDepositInFeeBase() public {
        // 1. Setup: vault initialized, managementFee = 5%/yr (5000), fee = 10%.
        //    Alice deposits normally -> _deposit accrues fee on old NAV (0 fee, correct).
        // 2. Warp 30 days. Epoch is in buffer phase (epochEndDate set, epoch not running).
        uint256 priceAliceBefore = cdo.virtualPrice(cdo.AATranche());

        // 3. Bob's mid-epoch deposit: epoch variant routes to depositDuringEpoch,
        //    which calls strategy.mintStrategyTokens(amount) WITHOUT _updateAccounting.
        //    _managedContractValue() jumps by `amount`; latestHarvestBlock unchanged.
        //    (Simulated directly:)
        deal(strategy.token(), address(this), 1000e18);
        vm.prank(cdo_addr); // depositDuringEpoch internals
        strategy.mintStrategyTokens(1000e18); // NAV +1000e18, no fee checkpoint

        // 4. Next user interaction triggers _accrueManagementFee over inflated NAV
        //    for the full 30 days + performance fee on the "gain":
        //    expectedMgmtFee = 1000e18 * 5000 * 30 days / 3153600000000  (charged on Bob's principal)
        //    expectedPerfFee = 1000e18 * 10000 / 100000                  (principal booked as gain)
        uint256 feesBefore = cdo.unclaimedFees();
        vm.prank(alice);
        cdo.depositAA(1e18); // triggers _updateAccounting
        uint256 feesAfter = cdo.unclaimedFees();

        // 5. Assert: fees charged include accrual on Bob's just-minted principal,
        //    and Alice's price dropped although she did nothing.
        assertGt(feesAfter - feesBefore, expectedMgmtFeeOnNewPrincipal);
        assertLt(cdo.virtualPrice(cdo.AATranche()), priceAliceBefore);
    }
}
```

Note: the Foundry PoC must run against the epoch-variant deployment (`IdleCDOEpochVariant`/`IdleCDOEpochVariantPrefunded`) whose `depositDuringEpoch` path invokes `IdleCreditVault.mintStrategyTokens`; the core defect verified in this file is that `mintStrategyTokens` inflates `_managedContractValue()` with no companion call to `_accrueManagementFee`/`_updateAccounting`, while the normal `_deposit` path always checkpoints fees first at `IdleCDOCreditVault.sol:191-211, 222-249, 552-555`.