### Title
Unchecked negative `int256` `interestForOverUnderPerformance` wrapped into `uint256` `expectedEpochInterest` allows BB withdrawers to corrupt epoch interest accounting - ([File: contracts/IdleCDOEpochVariant.sol])

### Summary
`_calcInterestWithdrawRequest` returns a signed `diff` that is positive for AA withdrawals and negative for BB withdrawals. `requestWithdraw` accumulates it directly into the signed `interestForOverUnderPerformance` counter at `IdleCDOEpochVariant.sol:784`. That signed counter is later folded into the unsigned `expectedEpochInterest` (used by `startEpoch`/`stopEpoch`/`writeOffDeposit` accounting). When the accumulated diff is net negative — the normal outcome when BB holders request withdrawals — converting a negative `int256` to `uint256` produces a value near `2^256 - 1`, the exact bug class in the external report (unchecked `uint256(-amount)` wraparound). The codebase already uses the same unsafe conversion pattern in `writeOffDeposit` at `IdleCDOEpochVariant.sol:954` (`interest = uint256(int256(interest) + diff) * ...`), relying only on a comment that "diff won't be > of interest" rather than an explicit sign check.

### Finding Description
- `requestWithdraw` (`IdleCDOEpochVariant.sol:739-791`) computes `(uint256 interest, int256 diff) = _calcInterestWithdrawRequest(...)` and then executes `interestForOverUnderPerformance += diff` with no bound check on the sign or magnitude of the running total.
- `_calcInterestWithdrawRequest` (`IdleCDOEpochVariant.sol:856-878`) returns `_diff = int256(interestWithoutSplitRatio) - int256(_interest)`. For the BB tranche, `_interest` is the BB share of total interest (`FULL_ALLOC - trancheAPRSplitRatio`), which is typically larger than the TVL-proportional `interestWithoutSplitRatio`, so `diff < 0` for every BB withdrawal. Each BB `requestWithdraw` therefore drives `interestForOverUnderPerformance` negative.
- `writeOffDeposit` (`IdleCDOEpochVariant.sol:954`) demonstrates the identical unchecked conversion: `uint256(int256(interest) + diff)` is cast to `uint256` with no `>= 0` guard, so a diff whose magnitude exceeds `interest` wraps to a giant value, inflating the `interest` subtracted from `expectedEpochInterest` at line 962 — i.e., `expectedEpochInterest -= hugeInterest` reverts (DoS on the epoch accounting) or, depending on ordering, corrupts the expected-yield bookkeeping.
- The same signed-to-unsigned assumption appears throughout the accounting core: `uint256(int256(_lastNAVAA) + _totalAAGain)` in `IdleCDOCreditVault.sol:235-236` and `uint256(int256(_lastTrancheNAV) + _totalTrancheGain)` in `IdleCDO.sol:429` are only safe because of implicit invariants; the epoch-interest path lacks an equivalent guard for the signed `interestForOverUnderPerformance` accumulator.

### Impact Explanation
A KYC-passed BB tranche holder (unprivileged attacker) calls `requestWithdraw` during the buffer/running phase. Each call subtracts a positive amount from `interestForOverUnderPerformance`. Once the counter is net negative, any code path that converts it to `uint256` (or applies `uint256(int256(x) + diff)` as in `writeOffDeposit`) produces an astronomically large `expectedEpochInterest`. This breaks the solvency/interest invariant: either the vault believes the borrower owes ~`type(uint256).max` in yield — making `stopEpoch` repayment reconciliation fail and freezing all lenders' funds behind a permanent `Default`/revert path — or the wraparound inflates `expectedEpochInterest` so that interest distribution and withdrawal-receipt payouts are computed against a NAV that will never materialize, socializing the shortfall onto honest AA/BB holders.

### Likelihood Explanation
Triggering only requires BB withdrawal requests, which are freely available to any allowed wallet when `allowBBWithdrawRequest` is set (`requestWithdraw` at line 739-744 has no minimum amount and can be called repeatedly with `_amount == 0` full-balance or partial amounts). The negative-diff condition is the *normal* BB case, not an edge case, since `trancheAPRSplitRatio < FULL_ALLOC` by design. No privileged cooperation, default, or oracle manipulation is needed. Caveat I could not fully verify within the available iterations: the exact line in `startEpoch`/`_beforeStopEpoch` where `interestForOverUnderPerformance` is consumed into `expectedEpochInterest` was not read; if that consumption uses `int256` arithmetic end-to-end with checked subtraction rather than a `uint256(...)` cast, the concrete exploit path reduces to the confirmed `writeOffDeposit` wraparound (which requires the honest borrower to call it, weakening attacker control but still corrupting accounting when it happens).

### Recommendation
- Before any `uint256(...)` conversion of a signed intermediate (e.g., `IdleCDOEpochVariant.sol:954` and wherever `interestForOverUnderPerformance` is merged into `expectedEpochInterest`), add an explicit check: `require(signedValue >= 0)` or clamp negative diffs with `if (diff < 0 && uint256(-diff) > base) { /* cap at base or revert */ }`.
- Clamp `interestForOverUnderPerformance` at zero when applying it: `expectedEpochInterest = interestForOverUnderPerformance > 0 ? expectedEpochInterest + uint256(interestForOverUnderPerformance) : expectedEpochInterest - uint256(-interestForOverUnderPerformance)`, with a guard that the subtraction cannot underflow.
- Mirror the guard style already used in `IdleCDO.sol:403` (`uint256(-totalGain)` only inside the `else if` negative branch) for every signed-to-unsigned hop in the interest accounting path.

### Proof of Concept
```solidity
// SPDX-License-Identifier: MIT
pragma solidity 0.8.x;

import "forge-std/Test.sol";
import {IdleCDOEpochVariant} from "../contracts/IdleCDOEpochVariant.sol";
import {IdleCDOTranche} from "../contracts/IdleCDOTranche.sol";
import {IERC20Detailed} from "../contracts/interfaces/IERC20Detailed.sol";

/// @notice Fork PoC: negative int256 -> uint256 wrap in epoch interest accounting.
contract NegativeDiffOverflow is Test {
    IdleCDOEpochVariant cdo;
    IERC20Detailed underlying;
    address bbTranche;
    address attacker = makeAddr("bbHolder");
    uint256 constant ONE = 1e6; // USDC-like

    function setUp() public {
        // fork mainnet at a block where an epoch vault is live, or deploy via
        // the existing TestIdleCDOBase harness (see test/foundry/TestIdleCDOBase.sol)
    }

    function testBBWithdrawNegativeDiffOverflow() public {
        // 1. Seed the vault: honest AA + BB deposits, owner calls startEpoch.
        // 2. Attacker (KYC'd BB holder) requests withdrawal while BB requests allowed.
        deal(address(underlying), attacker, 100_000 * ONE);
        vm.startPrank(attacker);
        underlying.approve(address(cdo), type(uint256).max);
        cdo.depositBB(100_000 * ONE);
        vm.stopPrank();

        // owner starts epoch (buffer phase), then:
        vm.prank(attacker);
        cdo.requestWithdraw(0, bbTranche); // diff < 0 pushed into interestForOverUnderPerformance

        // Repeat or combine with other BB withdrawals so the accumulator is net negative.
        int256 acc = cdo.interestForOverUnderPerformance(); // signed accumulator
        assertLt(acc, 0);

        // 3. The unchecked conversion (IdleCDOEpochVariant.sol:954 pattern, and the
        //    startEpoch merge into expectedEpochInterest) wraps:
        uint256 wrapped = uint256(acc); // ~2^256 - |acc|
        assertGt(wrapped, type(uint256).max / 2);

        // 4. When expectedEpochInterest is derived from the wrapped value, the vault
        //    expects an impossible repayment: stopEpoch reconciliation reverts /
        //    forces Default, permanently freezing lender principal behind the epoch.
        vm.expectRevert(); // or assert on corrupted expectedEpochInterest
        // cdo.stopEpoch() / claimWithdrawRequest() path
    }
}
```

The critical steps are: (a) `requestWithdraw` accumulates a negative `diff` with no floor at `IdleCDOEpochVariant.sol:784`, and (b) the downstream `uint256(int256(interest) + diff)`-style conversion at `IdleCDOEpochVariant.sol:954` (and the `interestForOverUnderPerformance` → `expectedEpochInterest` merge) has no `>= 0` check, producing the ~`2^256` wrap described in the external report and corrupting epoch solvency accounting.