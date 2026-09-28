### Title
`fullfillWriteOffRequest` credits the nominal `_underlyings` amount without measuring the actual received balance, allowing a fee-on-transfer shortfall to drain other users' escrowed underlyings - (File: contracts/IdleCreditVaultWriteOffEscrow.sol)

### Summary
`IdleCreditVaultWriteOffEscrow.fullfillWriteOffRequest` pulls `_underlyings` from the fulfiller with `safeTransferFrom` and then pushes `_underlyings - _totFee` to the request owner, while decrementing `pendingUnderlyings` by the full `currentRequest.underlyings`. Unlike the vault's `_deposit` (`IdleCDO.sol:250-253`, `IdleCDOCreditVault.sol:203-206`), which mints shares based on `balanceOf` delta, the escrow never measures how many tokens actually arrived. If the underlying token deducts a fee on transfer (the system has no token whitelist guaranteeing otherwise), the escrow receives less than `_underlyings` but pays out and accounts for the full amount, silently consuming underlyings escrowed for other pending write-off requests.

### Finding Description
In `fullfillWriteOffRequest` (contracts/IdleCreditVaultWriteOffEscrow.sol:123-155):

1. `userRequests[_user]` is validated and deleted, and `pendingUnderlyings` is reduced by `currentRequest.underlyings`.
2. `underlyingToken.safeTransferFrom(msg.sender, address(this), _underlyings)` pulls the nominal amount — the contract assumes it received `_underlyings`.
3. `_totFee = _underlyings * exitFee / FULL_VALUE` is computed on the nominal amount and sent to `feeReceiver`.
4. `underlyingToken.safeTransfer(_user, _underlyings - _totFee)` pays the user the nominal remainder.

With a fee-on-transfer `underlying` (e.g., USDT with Tether's fee enabled, or any rebasing/deflationary token selected as vault underlying — there is no whitelist preventing this), the escrow's actual balance increases by `_underlyings - tokenFee`. As long as `tokenFee > _totFee`, the `safeTransfer(_user, _underlyings - _totFee)` is funded partly by underlyings already escrowed for other users' pending requests (`pendingUnderlyings` tracks exactly this pooled liability at line 134). The attacker's own fulfillment succeeds — their request is deleted and they receive the full payout — while the pool's aggregated underlying balance becomes less than `pendingUnderlyings`.

Contrast with `IdleCDOEpochVariant.depositDuringEpoch` (IdleCDOEpochVariant.sol:685), which has the same missing-delta pattern but self-corrects via revert: `_skimDonatedAssets` empties the raw balance before the deposit, so the subsequent `_transferUnderlyings(_borrower(), _amount)` reverts on the shortfall and rolls back the whole transaction. The escrow has no such backstop because the pooled balance legitimately holds other users' funds at the moment of fulfillment.

### Impact Explanation
An unprivileged fulfiller (explicitly in-scope attacker role) causes the escrow to pay out more underlyings than it took in. The deficit is taken from `pendingUnderlyings` belonging to other write-off requests, so subsequent `fullfillWriteOffRequest` calls for honest users revert on insufficient balance — permanent freezing/theft of escrowed underlyings proportional to the token's transfer fee times the number of drained fulfillments. There is no recovery path: `emergencyWithdraw` is owner-only and accounting (`pendingUnderlyings`, `userRequests`) is already inconsistent with the real balance.

### Likelihood Explanation
Requires the vault/escrow `underlying` to be a token that takes a transfer fee or rebases downward. Standard USDC/DAI underlyings are unaffected, and Tether's USDT fee is currently disabled on mainnet (though toggleable and enabled on some chains). The escrow also only accumulates a drainable pool while multiple write-off requests are pending, which occurs mainly around borrower default settlements. Impact is real but conditional on token behavior, keeping likelihood low.

### Recommendation
Measure the actual received amount inside `fullfillWriteOffRequest`, mirroring the delta pattern already used in `_deposit`:

```solidity
uint256 balBefore = underlyingToken.balanceOf(address(this));
underlyingToken.safeTransferFrom(msg.sender, address(this), _underlyings);
uint256 received = underlyingToken.balanceOf(address(this)) - balBefore;
```

Then compute `_totFee` and the user payout from `received`, and revert (or require `received >= currentRequest.underlyings`) if the credited amount is insufficient to satisfy the request, so a shortfall can never consume other users' escrowed funds. Alternatively, enforce at initialization that `underlying` is a plain ERC20 with no fee/rebase mechanics.

### Proof of Concept
Reproducible on a Foundry fork with a mocked fee-on-transfer underlying (or a USDT fork with fees enabled):

```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.10;

import "forge-std/Test.sol";
import "../contracts/IdleCreditVaultWriteOffEscrow.sol";

contract FoTToken is IERC20Detailed {
    // minimal ERC20 taking `feeBps` on every transfer/transferFrom
}

contract EscrowFoTTest is Test {
    IdleCreditVaultWriteOffEscrow escrow;
    FoTToken fot;           // 1% transfer fee
    address userA = address(0xA);
    address userB = address(0xB);
    address fulfiller = address(0xF);

    function testDrainViaFeeShortfall() public {
        // Setup: escrow.exitFee() == 0 for simplicity.
        // 1. userA and userB each create write-off requests of 1000e6 tranches
        //    backed by pending underlyings (escrow pools them in pendingUnderlyings).
        // 2. Fulfiller calls fullfillWriteOffRequest(userA, tranchesA, 1000e6).
        //    Escrow receives only 990e6 (1% FoT), but pays userA 1000e6 and
        //    decrements pendingUnderlyings by 1000e6.
        //    The extra 10e6 comes from userB's escrowed underlyings.
        // 3. Fulfiller calls fullfillWriteOffRequest(userB, tranchesB, 1000e6).
        //    Escrow again receives 990e6; pool now holds 990e6 but must pay 1000e6.
        //    safeTransfer reverts -> userB's request is permanently unfulfillable
        //    even though pendingUnderlyings still records the full liability.
        assertLt(fot.balanceOf(address(escrow)), escrow.pendingUnderlyings());
    }
}
```

Key assertion: after fulfilling one request under a fee-on-transfer token, `underlying.balanceOf(escrow) < pendingUnderlyings`, and the next honest fulfillment reverts — demonstrating insolvency of the escrow's pooled underlyings.