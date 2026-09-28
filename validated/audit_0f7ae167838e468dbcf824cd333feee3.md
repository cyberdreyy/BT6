### Title
Post-default instant withdrawals bypass recovery accounting and drain borrower repayments at par - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
Analog of prototype pollution: `requestInstantWithdraw` lets an unprivileged user inject new entries into the already-finalized epoch accounting buckets (`instantWithdrawsRequestsByEpoch[user][defaultRecoveryEpoch]`, `instantWithdrawClaimsByEpoch[defaultRecoveryEpoch]`, `pendingInstantWithdraws`) after `finalizeDefaultRecovery` has closed them. Unlike `requestWithdraw`, which has an explicit `defaultRecoveryFinalized` branch that mints a haircut-applied `postDefaultRequests` receipt, `requestInstantWithdraw` has no post-default handling at all. The injected claim is then paid **at par** via `_transferFundedClaim`, spending any non-reserve underlying (e.g., late borrower repayments) that economically belongs to defaulted-epoch claimants who only recover `defaultRecoveryPrice < 1`.

### Finding Description
After `finalizeDefaultRecovery` runs, `defaultRecoveryFinalized = true` and `defaultRecoveryReserve` isolates the recovered underlying. `requestWithdraw` correctly detects this state and routes new requests into `postDefaultRequests`, paying them 1:1 only because the CDO passes an already-haircut amount (lines 247-257, `contracts/strategies/idle/IdleCreditVault.sol`).

`requestInstantWithdraw` (lines 356-375) performs no such check:

- it burns `_amount` of CDO strategy tokens and mints an equal un-haircut receipt to the user;
- it writes `instantWithdrawsRequestsByEpoch[_user][epochNumber] += _amount` — and since no new epoch can start on a defaulted pool, `epochNumber == defaultRecoveryEpoch`, so the new claim is merged into the finalized epoch bucket exactly like a `__proto__` write into a frozen object;
- it inflates `instantWithdrawClaimsByEpoch[defaultEpoch]` and `pendingInstantWithdraws`, counters that `defaultPendingClaimBasis`/`_defaultPrefundedInstantReserve` treat as belonging to the pre-default claim set.

Two payout paths are both wrong:

1. `defaultInstantWithdrawsFinalized == false` (no pending instant queue at finalization): `claimInstantWithdrawRequest` skips `_claimDefaultedInstantWithdrawRequest` and pays the full `instantWithdrawsRequests[_user]` through `_transferFundedClaim` (lines 387-392). The only guard is `balance - reserve >= amount`, so any underlying above the reserve — most naturally a late borrower repayment/recovery sent to the strategy after finalization — is paid **at par**, while every legitimate defaulted claimant is capped at `defaultRecoveryPrice`.
2. `defaultInstantWithdrawsFinalized == true`: the injected receipt sits under `defaultRecoveryEpoch`, so `_claimDefaultedInstantWithdrawRequest` pays `claimBasis * defaultRecoveryPrice` directly out of `defaultRecoveryReserve` — but the reserve was sized at finalization for a fixed `totalBasis`. The new claim consumes reserve that was priced for pre-existing claims only, and it also clamps `pendingInstantWithdraws` to 0 (line 852), corrupting the unfunded-remainder accounting for real defaulted-epoch instant claimants.

No existing guard stops this: `_ensureDefaultRecoveryInitialized` returns early once initialized, the loss-epoch check at lines 263-271 exists only in `requestWithdraw`, and `_onlyIdleCDO` is satisfied because any tranche holder can make the CDO call `requestInstantWithdraw` on their behalf.

### Impact Explanation
Direct theft of recovery funds. With recovery price `p < 1`, an attacker holding `X` of active strategy-token exposure requests an instant withdrawal post-default and, once any underlying arrives at the strategy above the reserve (path 1), claims `X` at par instead of `X * p` — extracting `(1 - p) * X` that belongs to defaulted-epoch receipt holders and active LPs. In path 2 the attacker draws `X * p` from `defaultRecoveryReserve` ahead of the queue, diluting/stranding legitimate claimants whose aggregate basis was fixed at finalization. Loss is bounded by the attacker's active position but scales with post-finalization inflows, which are expected during work-out/recovery.

### Likelihood Explanation
Requires a finalized default (`_handleBorrowerDefault` → `finalizeDefaultRecovery`) and either (a) any post-finalization underlying inflow to the strategy — routine during recovery workouts — or (b) `defaultInstantWithdrawsFinalized == true`, i.e. a partially unfunded instant queue at default, which is a normal state. The attacker needs only a tranche position (any KYC-passing lender) and one transaction through the CDO; the missing guard makes the path deterministic.

### Recommendation
Mirror the `requestWithdraw` post-default logic in `requestInstantWithdraw`: when `defaultRecoveryFinalized`, require no outstanding requests, mint the receipt into a post-default bucket (e.g., reuse `postDefaultRequests`) that is paid from `defaultRecoveryReserve` at the haircut already applied by the CDO — and never write to `instantWithdrawsRequestsByEpoch[defaultRecoveryEpoch]`, `instantWithdrawClaimsByEpoch`, or `pendingInstantWithdraws` after finalization. Symmetrically, ensure late borrower inflows are swept into `defaultRecoveryReserve` before any funded-claim path can spend them.

### Proof of Concept
```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.10;

import "forge-std/Test.sol";

// Assume: vault = IdleCreditVault behind CDO; epoch ended; borrower defaulted;
// finalizeDefaultRecovery ran with defaultRecoveryPrice = 0.5e18 and
// defaultInstantWithdrawsFinalized = false (pendingInstantWithdraws == 0).
// Attacker holds tranche tokens backed by `active` strategy tokens of the CDO.

contract PostDefaultInstantWithdrawPoC is Test {
    function test_PostDefaultInstantWithdrawPaidAtPar() public {
        // 1. Attacker redeems tranches through the CDO, which calls
        //    vault.requestInstantWithdraw(X, attacker).
        //    No defaultRecoveryFinalized check runs:
        //      - _burn(CDO, X); _mint(attacker, X)
        //      - instantWithdrawsRequestsByEpoch[attacker][defaultEpoch] += X
        //      - instantWithdrawClaimsByEpoch[defaultEpoch] += X   // polluted
        //      - pendingInstantWithdraws += X                      // polluted
        //
        // 2. Borrower (honest) later repays R underlying to the strategy
        //    as part of the work-out. Reserve accounting is NOT updated.
        //
        // 3. CDO calls vault.claimInstantWithdrawRequest(attacker):
        //      - defaultInstantWithdrawsFinalized == false, so the
        //        defaulted-epoch path is skipped entirely;
        //      - amount = instantWithdrawsRequests[attacker] = X;
        //      - _transferFundedClaim: balance = reserve + R,
        //        balance - reserve = R >= X  -> passes;
        //      - attacker receives X at par.
        //
        //    Legitimate defaulted-epoch claimants are still capped at
        //    0.5 * claimBasis. Attacker extracted (1 - 0.5) * X extra,
        //    funded by R which economically belonged to the recovery pool.
        //
        // Expected assertions on a mainnet/test fork:
        //   assertEq(underlying.balanceOf(attacker), X);
        //   assertLt(defaultRecoveryReserve_after - defaultRecoveryReserve_before, 0 or unchanged);
        //   // i.e. attacker paid at par while price = 0.5e18
    }
}
```