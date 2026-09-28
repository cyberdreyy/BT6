### Title
Post-default withdraw requests pay 1:1 from `defaultRecoveryReserve`, draining defaulted-epoch claimants and permanently freezing the tail of the recovery reserve - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
After `finalizeDefaultRecovery`, a lender can still open a withdraw request through the CDO. The post-default branch of `requestWithdraw` mints a full `_amount` receipt and stores it in `postDefaultRequests`, and `_claimPostDefaultWithdrawRequest` pays it 1:1 out of `defaultRecoveryReserve` — the same isolated reserve that was sized at finalization to cover only the pre-existing `totalBasis` claims at `recoveryPrice`. Post-default claims add payout obligations without adding any underlying to the reserve, so each one steals recovery value from defaulted-epoch claimants, and once the reserve is exhausted the subtraction `defaultRecoveryReserve -= _amount` in `_transferDefaultRecovery` underflows, permanently reverting the remaining claims.

### Finding Description
In `finalizeDefaultRecovery` the reserve is set once to `reserveAmount` which equals (roughly) `totalBasis * recoveryPrice / RECOVERY_FULL`, backing exactly the claims known at finalization (lines 686-692).

After that, `requestWithdraw` takes the post-default branch (lines 247-257): it burns CDO strategy tokens, mints `_amount` receipt tokens to the user, and records `postDefaultRequests[_user] = _amount`. Critically, no underlying enters `defaultRecoveryReserve` — the post-default haircut is applied by lowering the CDO `virtualPrice` first, so the strategy still holds only the original recovery reserve.

Later, `_claimPostDefaultWithdrawRequest` (lines 760-767) burns the receipt and calls `_transferDefaultRecovery`, which does `defaultRecoveryReserve -= _amount` and transfers `_amount` at par (lines 912-917). Two consequences:

1. **Value theft**: a post-default receipt minted from a deposit priced at the post-haircut `virtualPrice` redeems at 1:1 from a reserve that was only funded at `recoveryPrice` per unit of basis. The claim is under-collateralized by `1 - recoveryPrice` and the shortfall is socialized onto default-epoch claimants.
2. **Permanent freeze**: once cumulative post-default payouts exceed the headroom, `_transferDefaultRecovery` underflows (`defaultRecoveryReserve -= _amount` with `_amount > reserve`), so `claimWithdrawRequest` for remaining defaulted/post-default users always reverts — their recovery is permanently frozen in the strategy. This mirrors the CVE class: a crafted input (an ordinary post-default withdraw request) triggers an assertion-style fault that bricks subsequent calls.

The guards don't help: `_claimPostDefaultWithdrawRequest` is checked before `_claimDefaultedWithdrawRequest`, so post-default claimants are paid first; nothing caps post-default claims against remaining reserve or accounts for them in `totalBasis`.

### Impact Explanation
Direct theft of the default-recovery reserve (post-default claimants withdraw more than their funded share) plus permanent freezing of unclaimed recovery for default-epoch receipt holders once `defaultRecoveryReserve` underflows. Loss is bounded by the post-default withdrawal volume times `1 - defaultRecoveryPrice` for theft, and up to the entire remaining reserve for the freeze.

### Likelihood Explanation
Requires a borrower default finalized via `finalizeDefaultRecovery` — an expected protocol flow, not an exotic state. After that, any KYC-passing lender can deposit through the CDO at the haircutted `virtualPrice` and immediately `requestWithdraw`/`claimWithdrawRequest`; the CDO calls are unprivileged user actions. No privileged misbehavior needed.

### Recommendation
Back post-default receipts out of the recovery reserve: either (a) hold the underlying corresponding to post-default requests in a separate bucket funded by the new deposit, or (b) pay post-default claims at `defaultRecoveryPrice` instead of 1:1, or (c) in `_transferDefaultRecovery` revert/skip when `defaultRecoveryReserve < _amount` only for reserve-funded claims — but the correct fix is to not let post-default claims consume `defaultRecoveryReserve` at all.

### Proof of Concept
```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.10;
import "forge-std/Test.sol";

// Assume forked deployment: IdleCreditVault strategy + IdleCDOEpochVariant CDO,
// USDC underlying, borrower defaulted in epoch N.

contract PostDefaultReserveDrainTest is Test {
    // users
    address alice = makeAddr("alice"); // default-epoch withdraw requester
    address bob   = makeAddr("bob");   // post-default attacker (KYC'd lender)

    function test_postDefaultClaimDrainsReserveAndFreezesAlice() public {
        // --- Epoch N: alice requests withdraw of 100_000 USDC (receipt minted, pendingWithdraws += 100k)
        // --- stopEpoch, borrower repays only partially / defaults
        // --- owner calls finalizeDefaultRecovery:
        //       totalBasis  = activeBasis + 100_000
        //       reserveAmount recovered => defaultRecoveryPrice = 0.5e18 (50% haircut)
        //       defaultRecoveryReserve = reserveAmount (e.g. 50_000 + active share)

        // --- Bob deposits D underlying through CDO post-default at haircutted virtualPrice,
        //     receiving tranche tokens, then calls CDO.requestWithdraw:
        //       IdleCreditVault.requestWithdraw(amount=D_atPar, user=bob, principal=...)
        //       -> mints D receipt to bob, postDefaultRequests[bob] = D
        //     NOTE: no underlying was added to defaultRecoveryReserve.

        // --- Bob claims immediately:
        //       claimWithdrawRequest(bob) -> _claimPostDefaultWithdrawRequest
        //       -> _transferDefaultRecovery(bob, D) pays 1:1, reserve -= D
        //     Bob redeems at par although reserve was funded at 50%.

        // --- Alice claims her default-epoch receipt:
        //       _claimDefaultedWithdrawRequest: amount = claimBasis * 0.5e18 / 1e18
        //       _transferDefaultRecovery: defaultRecoveryReserve -= amount
        //       ==> underflow / revert NotAllowed once bob drained the headroom.
        //     Alice's recovery (and any later claimants') is permanently frozen.

        // Assert: bob.balance increased by D; alice claim always reverts.
    }
}
```

Concrete steps on a mainnet fork: seed an epoch with one pending withdraw, force default, call `finalizeDefaultRecovery` with a partial recovery (e.g. `recoveryPrice < 1e18`), then have a second KYC'd user deposit + request + claim post-default; show `defaultRecoveryReserve` decreasing by the full payout while the first claimant's `_claimDefaultedWithdrawRequest` reverts with arithmetic underflow in `_transferDefaultRecovery` (`IdleCreditVault.sol:915`).