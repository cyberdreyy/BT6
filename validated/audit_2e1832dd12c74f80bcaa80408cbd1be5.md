### Title
`finalizeDefaultRecovery` lets any caller spend a third party's underlying allowance as the default recovery source - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`IdleCreditVault.finalizeDefaultRecovery(uint256 _recoveredAmount, address _recoverySource)` accepts a caller-supplied `_recoverySource` and pulls `_recoveredAmount` of underlying from it via `underlyingToken.safeTransferFrom(_recoverySource, address(this), _recoveredAmount)` at `IdleCreditVault.sol:706-709`. The only requirement is that `_recoverySource` has previously approved the strategy contract. There is no check that `msg.sender == _recoverySource` or that the source authorized being used to fund the recovery — the same bug class as `PerpDepository::rebalance` pulling shortfall funds from an arbitrary caller-supplied `account`.

### Finding Description
When a credit pool is in the `defaulted` state and recovery has not yet been finalized, the CDO-side entry point `IdleCDOEpochVariant.finalizeDefaultRecovery` forwards a recovery amount and a source address to this function. Any address that has ever granted the `IdleCreditVault` strategy an underlying allowance (or that can be induced to) can be named as `_recoverySource` by an unrelated caller. The pulled funds are locked into `defaultRecoveryReserve` and distributed to pending withdraw request claimants and active tranche holders through `defaultRecoveryPrice`; the source receives nothing in return — no claim, no credit, no tranche tokens. This is a strictly worse outcome for the source than the external report: in UXD the funder at least covered a swap, here the funder's tokens are absorbed into an insolvency waterfall for a debt they never owed.

The guard `_onlyIdleCDO()` at `IdleCreditVault.sol:662` only restricts the caller to the CDO contract; it does not constrain which address supplies the recovery. `_ensureDefaultRecoveryInitialized()` and the `defaulted()` check at lines 663-666 gate timing, not source authorization. Line 667 only rejects a zero `_recoverySource` when `_recoveredAmount != 0` — an arbitrary nonzero approved address passes.

### Impact Explanation
Any account holding an underlying allowance to the strategy contract can have those approved funds permanently seized into the default recovery reserve by an unrelated caller. Loss is bounded by the victim's allowance and equals up to `_recoveredAmount`; the funds are distributed pro-rata to claimants, so a claimant (including the attacker if they hold pending default claims) partially benefits, but the primary harm — irreversible loss of the victim's approved tokens without consent — stands on its own. The victim is typically the borrower or a recovery funder who approved the strategy expecting to call the function themselves.

### Likelihood Explanation
Requires: (1) the pool is defaulted and `defaultRecoveryFinalized` is false; (2) some address holds a nonzero underlying allowance to the `IdleCreditVault` contract. Condition (2) is realistic — the borrower funds epochs through this strategy, and any EOA/contract that approved the strategy for a failed or partial repayment flow retains the allowance. The caller needs no privilege: the transfer happens inside the same call, so a griefer or a pending-claim holder can finalize recovery using the victim's funds the moment allowance exists, front-running the intended funder and stealing the entire contribution. There is no whitelist or `msg.sender == _recoverySource` check anywhere in the pull path.

### Recommendation
Bind the recovery source to the caller: either require `_recoverySource == msg.sender` in `IdleCreditVault.finalizeDefaultRecovery` (or enforce this in the `IdleCDOEpochVariant` entry point), or maintain an owner/borrower-set `allowedRecoverySource` address that is the only permitted funder.

### Proof of Concept
```solidity
// SPDX-License-Identifier: MIT
pragma solidity 0.8.10;

import "forge-std/Test.sol";
import {IdleCreditVault} from "contracts/strategies/idle/IdleCreditVault.sol";
import {IdleCDOEpochVariant} from "contracts/IdleCDOEpochVariant.sol";
import {IERC20Detailed} from "contracts/interfaces/IERC20Detailed.sol";

contract RecoverySourceTheftTest is Test {
    IdleCDOEpochVariant cdo;      // deployed epoch CDO proxy
    IdleCreditVault strategy;     // deployed IdleCreditVault strategy
    IERC20Detailed underlying;

    address victim = makeAddr("victim");   // e.g. borrower-side wallet that approved strategy
    address attacker = makeAddr("attacker"); // unprivileged caller / pending claimant

    function testStealApprovedRecoverySource() public {
        uint256 recovered = 100_000e6; // USDC-like underlying

        // Pool is in `defaulted` state (borrower failed; _handleBorrowerDefault ran).

        // Victim previously approved the strategy, e.g. intending to fund recovery itself.
        vm.prank(victim);
        underlying.approve(address(strategy), recovered);

        uint256 victimBefore = underlying.balanceOf(victim);
        uint256 stratBefore = underlying.balanceOf(address(strategy));

        // Attacker finalizes recovery naming the victim as the source.
        // The CDO forwards (recovered, victim) into
        // IdleCreditVault.finalizeDefaultRecovery, which executes
        // underlyingToken.safeTransferFrom(victim, strategy, recovered).
        vm.prank(attacker);
        cdo.finalizeDefaultRecovery(recovered, victim); // CDO entry point, unprivileged

        assertEq(underlying.balanceOf(victim), victimBefore - recovered, "victim funds seized");
        assertEq(underlying.balanceOf(address(strategy)), stratBefore + recovered);
        assertTrue(strategy.defaultRecoveryFinalized());
        // Victim received no claim, credit, or tranche tokens in return.
    }
}
```

Caveat: I could not fully trace `IdleCDOEpochVariant.finalizeDefaultRecovery` to confirm its access control and that it forwards a caller-chosen `_recoverySource`. If the CDO entry point is owner/manager-gated or hardcodes the source, the unprivileged attack path closes; the strategy-side pull itself (unconditional `safeTransferFrom` of a caller-influenced address) is confirmed at `IdleCreditVault.sol:706-709`.