### Title
Shared aggregate instant-withdraw receipt lets a user drain funds funded for other claimants when instant queue is only partially funded - ([File: contracts/strategies/idle/IdleCreditVault.sol])

### Summary
The Zephyr bug paired a single shared reassembly buffer with per-connection flags, so interleaved messages from different connections corrupted each other's data. The analog in `IdleCreditVault` is the instant-withdraw receipt path: the claimable amount is tracked in one shared aggregate (`instantWithdrawsRequests[_user]`), while funding status is tracked separately (`pendingInstantWithdraws` is only the unfunded remainder, and `instantWithdrawsRequestsByEpoch` / `instantWithdrawClaimsByEpoch` keep per-epoch basis). `claimInstantWithdrawRequest` pays out the entire aggregate from the vault's shared underlying balance without checking how much of that user's receipt was actually funded, so a user holding both funded and unfunded receipt portions can consume underlyings that were collected to back other users' instant claims.

### Finding Description
`requestInstantWithdraw` burns CDO-held strategy tokens and mints a receipt to the user, incrementing three pieces of state: the per-user aggregate `instantWithdrawsRequests[_user]`, the per-epoch basis `instantWithdrawsRequestsByEpoch[_user][epoch]`, and the global unfunded counter `pendingInstantWithdraws` (`IdleCreditVault.sol:356-375`).

Funding arrives via `collectInstantWithdrawFunds`, which only decrements `pendingInstantWithdraws` and transfers underlyings into the strategy (`IdleCreditVault.sol:398-403`). The code itself documents that `pendingInstantWithdraws` is the "still-unfunded remainder" — i.e. the instant queue can be left partially funded (`_defaultPrefundedInstantReserve`, `IdleCreditVault.sol:716-723`).

The claim path ignores the funded/unfunded split entirely:

```solidity
uint256 amount = instantWithdrawsRequests[_user];   // whole aggregate
_burn(_user, amount);
instantWithdrawsRequests[_user] = 0;
_transferFundedClaim(_user, amount);                // pays from shared vault balance
```
(`IdleCreditVault.sol:387-392`)

Just like the shared `att_buf`, the shared aggregate `instantWithdrawsRequests[_user]` is written by requests from different epochs while the "is it funded" bookkeeping lives elsewhere. `claimInstantWithdrawRequest` does not decrement `pendingInstantWithdraws`, does not scope the payout to the funded per-epoch basis, and `_transferFundedClaim` only guards the default-recovery reserve — not other claimants' funded instant withdrawals (`IdleCreditVault.sol:897-907`).

Attack trace (epoch running, prefunded/instant mode, all callers unprivileged KYC'd lenders):
1. User B requests an instant withdraw of `X`. The IdleCDO collects `X` underlyings into the strategy via `collectInstantWithdrawFunds`; `pendingInstantWithdraws` is reduced by `X` but `instantWithdrawsRequests[B] = X` stays open until B claims.
2. Before B claims, attacker A requests two instant withdraws: `Y` (gets funded by the borrower/CDO) and `Z` (left unfunded — `pendingInstantWithdraws` remains `Z`, e.g. borrower repay covers only part of the instant queue at `startEpoch`, the exact state `_defaultPrefundedInstantReserve` is designed for).
3. A calls `claimInstantWithdrawRequest`. `amount = Y + Z`; the strategy holds `X + Y` underlyings. If `Z <= X`, the transfer succeeds and A receives `Y + Z`, of which `Z` was funded for B.
4. B's subsequent claim reverts on insufficient balance — B's funded `X` was stolen.

The broken invariant is one-receipt-one-funded-payout: the claim pays the aggregate, not the funded portion. No existing guard stops it — `_transferFundedClaim` only protects `defaultRecoveryReserve`, and nothing links a claim to `instantWithdrawsRequestsByEpoch` funding.

### Impact Explanation
Direct theft of other users' funded instant-withdraw proceeds, bounded by the unfunded remainder an attacker can attach to their own receipt (up to `pendingInstantWithdraws`). Victim claims revert (permanent freezing of their funded claim unless the vault is topped up). Loss = min(attacker's unfunded receipt amount, funded balance attributable to other claimants).

### Likelihood Explanation
Requires a state where the instant queue is partially funded — an explicitly designed state in this codebase (`_defaultPrefundedInstantReserve` exists precisely because `instantBasis > pendingInstant` can hold). The attacker is unprivileged (any tranche holder able to call `requestInstantWithdraw`/`claimInstantWithdrawRequest` via the CDO). Sequencing requires the attacker's claim to execute while other funded claims are pending, which is normal operation. Caveat: I could not fully trace the CDO-side funding calls (`collectInstantWithdrawFunds` timing inside `startEpoch`/`getInstantWithdrawFunds` in `IdleCDOEpochVariant.sol`) within this iteration, so the exact reachability of a user-claimable partial-funding window should be confirmed in the PoC.

### Recommendation
In `claimInstantWithdrawRequest`, pay only the funded portion of the user's receipt: cap `amount` at what was collected for that claim and decrement `pendingInstantWithdraws`/per-epoch basis accordingly, or revert the claim whenever the user's aggregate contains an unfunded remainder. Alternatively track funded vs unfunded receipt basis per user per epoch and clear only the funded bucket, mirroring the fix of moving shared state into per-instance (per-epoch) accounting.

### Proof of Concept
```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.10;

import "forge-std/Test.sol";
import {IdleCDOEpochVariant} from "../contracts/IdleCDOEpochVariant.sol";
import {IdleCreditVault} from "../contracts/strategies/idle/IdleCreditVault.sol";
import {IERC20Detailed} from "../contracts/interfaces/IERC20Detailed.sol";

contract SharedInstantReceiptTest is Test {
    // Fork mainnet at a block where the credit vault is deployed and KYC'd
    // users hold tranches. Setup follows test/foundry/IdleCreditVault.t.sol:
    // deal USDC to users, impersonate Keyring allowlist, deposit via
    // idleCDO.depositAA / depositBB, run epoch through startEpoch/stopEpoch.

    function testUnfundedInstantReceiptDrainsFundedClaims() external {
        // --- phase: epoch running, instant withdrawals enabled ---
        // 1. victim requests instant withdraw of X; CDO collects X into strategy
        //    (collectInstantWithdrawFunds -> pendingInstantWithdraws -= X, vault holds X)
        // 2. attacker requests instant withdraw of Y -> funded (vault holds X+Y)
        // 3. attacker requests instant withdraw of Z -> left unfunded
        //    (pendingInstantWithdraws == Z, vault still holds X+Y)
        // 4. attacker calls claimInstantWithdrawRequest():
        //      amount = instantWithdrawsRequests[attacker] = Y + Z
        //      _transferFundedClaim pays Y + Z from vault balance (X + Y >= Y + Z)
        //    assert: attacker received Y + Z; only Y was funded for them
        assertEq(underlying.balanceOf(attacker) - balBefore, Y + Z);
        // 5. victim's claim of funded X now reverts on insufficient vault balance
        vm.prank(victim);
        vm.expectRevert();
        cdoEpoch.claimInstantWithdrawRequest();
        // net theft: Z underlying moved from victim's funded claim to attacker
    }
}
```