### Title
Dust instant-withdraw receipt permanently blocks default-recovery initialization and finalization - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
The external bug class is "attacker-crafted input triggers an unchecked panic/revert that is never cleared, producing denial of service." The strongest analog in this codebase is `_ensureDefaultRecoveryInitialized()` in `IdleCreditVault.sol`, which hard-reverts whenever `pendingInstantWithdraws != 0`. Any unprivileged lender can call `requestInstantWithdraw` (via `IdleCDOEpochVariant.requestInstantWithdraw`) with a dust amount, receive a receipt, and simply never claim it. On a vault where `defaultRecoveryInitialized` is still `false` (i.e., a freshly upgraded strategy whose lazy initialization has not yet run), this dust receipt makes every subsequent call to `_ensureDefaultRecoveryInitialized()` revert permanently — and that function is on the critical path of `finalizeDefaultRecovery()`. One wei of attacker receipt therefore bricks the default-recovery finalization path indefinitely.

### Finding Description
- `requestInstantWithdraw(_amount, _user)` burns the CDO's strategy tokens, mints the user a receipt, and increments `pendingInstantWithdraws` and `instantWithdrawClaimsByEpoch[epochNumber]` with no minimum amount check.
- `_ensureDefaultRecoveryInitialized()` (lines 924-935) returns early only if `defaultRecoveryInitialized` is already true; otherwise it executes `if (pendingInstantWithdraws != 0) revert NotAllowed();` with the explicit comment "Pending instant withdrawals are never cleared automatically."
- `finalizeDefaultRecovery()` (line 663) calls `_ensureDefaultRecoveryInitialized()` before doing anything else, so while the attacker's dust receipt is pending, default finalization always reverts.
- `requestInstantWithdraw` itself also calls `_ensureDefaultRecoveryInitialized()`, so once the vault is in the uninitialized state any user can grief every new instant-withdraw request too, but the critical impact is on the recovery path.
- Clearing requires either the attacker claiming (they won't), or the honest borrower/CDO funding the dust via `collectInstantWithdrawFunds` plus the attacker then claiming — the receipt balance stays locked to the attacker's address because `_transfer` is restricted to `msg.sender == idleCDO` and `canTransfer` is forced false, so no third party can clear the attacker's receipt on their behalf. There is no privileged sweep function for pending instant receipts.
- Existing guards do not stop this: there is no dust threshold on `requestInstantWithdraw`, no expiry/forfeit of unclaimed instant receipts, and `stopEpoch`/`stopEpochWithDuration` do not clear `pendingInstantWithdraws`.

### Impact Explanation
Permanent freezing of funds. If a borrower default occurs while `defaultRecoveryInitialized == false` and a dust instant receipt exists, `finalizeDefaultRecovery` reverts on every call, so `defaultRecoveryReserve`, `defaultRecoveryPrice`, and `defaultRecoveryFinalized` are never set. All lender capital behind the vault — active tranche NAV plus pending withdraw receipts — cannot be distributed through `_claimDefaultedWithdrawRequest`, `_claimPostDefaultWithdrawRequest`, `_claimDefaultedInstantWithdrawRequest`, or `DefaultDistributor.claim`. With a 1-wei receipt, the entire recovery (potentially the full vault NAV) is frozen for as long as the attacker refuses to claim, and the attacker can re-request dust receipts repeatedly to keep the condition alive across epochs.

### Likelihood Explanation
Preconditions narrow the window: `defaultRecoveryInitialized` must be `false`, which occurs for a strategy that was upgraded to this implementation and has not yet had a clean initialization call (fresh deployments initialize the flag). Within that window the attack is cheap and permissionless: any KYC-passing lender deposits, requests an instant withdraw of 1 unit, and does nothing. No privileged actor can remove the receipt. Probability is moderate (requires the uninitialized post-upgrade state and an actual default for the worst impact), but cost is negligible and griefing is persistent.

### Recommendation
- Enforce a minimum `requestInstantWithdraw` amount, or
- Allow the manager/keeper to force-clear or fund-and-forfeit dust instant receipts (e.g., a `sweepInstantWithdraw(address _user)` that pulls the funded amount to a forfeiture reserve after a timeout), or
- In `_ensureDefaultRecoveryInitialized`, treat sub-precision `pendingInstantWithdraws` as clearable: roll them into `defaultRecoveryReserve` at zero price rather than reverting, so a dust receipt cannot veto initialization.

### Proof of Concept
Foundry fork PoC sketch (against the existing harness in `test/foundry/IdleCreditVault.t.sol`, which already deploys `cdoEpoch`, `idleCDO`, `AAtranche`, `borrower`, `manager`):

```solidity
// Setup: strategy upgraded -> defaultRecoveryInitialized == false
// (simulate via fresh proxy or storage slot cleared)
address alice = makeAddr("alice");
deal(defaultUnderlying, alice, 1000e6);

// 1. Alice deposits and requests a dust instant withdraw
vm.startPrank(alice);
IERC20Detailed(defaultUnderlying).approve(address(idleCDO), type(uint256).max);
idleCDO.depositAA(1000e6);
cdoEpoch.requestInstantWithdraw(1);          // 1 wei receipt
vm.stopPrank();

assertEq(IdleCreditVault(address(strategy)).pendingInstantWithdraws(), 1);

// 2. Borrower default occurs -> CDO calls finalizeDefaultRecovery
//    which hits _ensureDefaultRecoveryInitialized() and reverts
vm.prank(address(cdoEpoch));
vm.expectRevert(NotAllowed.selector);
IdleCreditVault(address(strategy)).finalizeDefaultRecovery(
    recoveredAmount, recoverySource);

// 3. Alice never claims; receipt is non-transferable (_transfer is CDO-only),
//    so no one can clear pendingInstantWithdraws. Recovery is bricked.
```

Note: I did not fully verify whether a fresh (non-upgraded) deployment initializes `defaultRecoveryInitialized = true` in an initializer; if it does, the exploitable window is limited to post-upgrade uninitialized vaults, which should be stated in the final report.