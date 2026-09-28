### Title
Claimed instant-withdraw receipts are never cleared from `instantWithdrawsRequestsByEpoch`/`instantWithdrawClaimsByEpoch`, inflating default recovery basis and reserve - ([File: contracts/strategies/idle/IdleCreditVault.sol](contracts/strategies/idle/IdleCreditVault.sol))

### Summary
`claimInstantWithdrawRequest` clears only the aggregate `instantWithdrawsRequests[_user]` but leaves the per-epoch basis `instantWithdrawsRequestsByEpoch[_user][epoch]` and the global `instantWithdrawClaimsByEpoch[epoch]` populated forever. If a borrower default is later finalized in the same epoch while other instant receipts remain unfunded, the already-paid-out basis is counted again in `defaultPendingClaimBasis()` and, worse, in `_defaultPrefundedInstantReserve()` as if the strategy still held those tokens. This inflates `defaultRecoveryReserve`/`defaultRecoveryPrice` with phantom funds and causes recovery insolvency: honest claimants' payouts sum to more than the underlying actually held, so later claimants (or active tranche holders via `defaultBBNav`) are left unpaid.

### Finding Description
The bug mirrors CVE-2020-12414: state that must be deleted when leaving a context is left behind. The cleanup paths for the three instant-withdraw counters are asymmetric:

- `requestInstantWithdraw` increments `instantWithdrawsRequests[_user]`, `instantWithdrawsRequestsByEpoch[_user][currentEpoch]`, `instantWithdrawClaimsByEpoch[currentEpoch]` and `pendingInstantWithdraws` [1](#0-0) 
- `collectInstantWithdrawFunds` decrements only `pendingInstantWithdraws` [2](#0-1) 
- `claimInstantWithdrawRequest` burns the receipt and zeroes only `instantWithdrawsRequests[_user]`; the per-epoch and global claim mappings are untouched [3](#0-2) 
- Only `_claimDefaultedInstantWithdrawRequest` (reachable solely after default finalization) clears the per-epoch mappings [4](#0-3) 

At finalization, `defaultPendingClaimBasis` includes `instantWithdrawClaimsByEpoch[epochNumber]` whenever `pendingInstantWithdraws != 0` [5](#0-4) , and `_defaultPrefundedInstantReserve` credits `instantBasis - pendingInstant` as underlying already held by the strategy [6](#0-5) . But tokens backing already-claimed instant receipts were transferred out by `_transferFundedClaim`, so the "prefunded reserve" is phantom. The stale basis then flows into `reserveAmount` and `recoveryPrice` [7](#0-6) .

### Impact Explanation
Broken invariant: recovery solvency — `defaultRecoveryReserve` must equal underlying actually held. With stale claims the strategy books reserve for tokens it already paid out. Consequences:

- `recoveryPrice` is computed against an inflated `reserveAmount`, so each defaulted-epoch claim (normal withdraws, unfunded instant receipts, and active LP NAV via `activeFinalNAV`) is paid at a ratio the strategy cannot cover; the last claimants' `_transferDefaultRecovery` calls revert for lack of balance — permanent freezing of unclaimed recovery.
- The inflated `totalBasis` also includes already-claimed basis, diluting honest users' recovery share.

Quantified example (6-dec underlying): epoch running, `allowInstantWithdraw` on. Alice requests instant withdraw of 1,000; `getInstantWithdrawFunds` collects 1,000 from borrower (`pendingInstantWithdraws` → 0, `instantWithdrawClaimsByEpoch[0]` stays 1,000). Alice claims 1,000 — funds leave the strategy. Bob requests instant withdraw 500; it stays unfunded (`pendingInstantWithdraws` = 500). Borrower defaults; `finalizeDefaultRecovery` computes `instantBasis = 1,500` (Alice's stale 1,000 + Bob's 500), so `prefundedReserve = 1,500 - 500 = 1,000` phantom tokens are added to `reserveAmount` and `totalBasis` includes Alice's already-paid 1,000. Recovery is distributed over non-existent value; the strategy's real balance is short by 1,000, so final claims revert.

### Likelihood Explanation
Requires only unprivileged actions in a named phase: instant withdraw request during a running epoch, funding via honest `manager`/`queue` `getInstantWithdrawFunds`, a claim, a second unfunded instant request in the same epoch, then a borrower default finalized via `finalizeDefaultRecovery`. No privileged misbehavior needed — the stale-mapping defect triggers on the ordinary claim path. Probability hinges on partial instant-withdraw funding followed by default in the same epoch, which is exactly the prefunded-instant flow the code already supports (test at `test/foundry/IdleCreditVault.t.sol:3799-3818` exercises instant default in an epoch).

### Recommendation
In `claimInstantWithdrawRequest`, clear the per-epoch accounting alongside the aggregate:

```solidity
uint256 amount = instantWithdrawsRequests[_user];
_burn(_user, amount);
instantWithdrawsRequests[_user] = 0;
uint256 currentEpoch = epochNumber; // or track the request epoch per user
instantWithdrawsRequestsByEpoch[_user][currentEpoch] -= amount;
instantWithdrawClaimsByEpoch[currentEpoch] -= amount;
_transferFundedClaim(_user, amount);
```

Because `requestInstantWithdraw` doesn't store the request epoch per user, either track the epoch (e.g., `lastInstantWithdrawRequestEpoch[_user]`) or, since claims only occur in the request epoch, decrement `instantWithdrawClaimsByEpoch[epochNumber]`. Add a regression test: funded+claimed instant receipt, then unfunded instant receipt + default in the same epoch, asserting `defaultPendingClaimBasis` excludes the claimed amount and total payouts never exceed real reserve.

### Proof of Concept
Foundry fork PoC (in the style of `test/foundry/IdleCreditVault.t.sol`):

```solidity
// assume: epoch running, allowInstantWithdraw = true, borrower funded for instant claims
address alice = makeAddr('alice');
address bob = makeAddr('bob');
_depositWithUser(alice, 1_000 * ONE_SCALE, true);
_depositWithUser(bob, 500 * ONE_SCALE, true);
_startEpochAndCheckPrices(0);

// 1) Alice instant-withdraws 1000 and borrower funds it fully
vm.prank(alice);
cdoEpoch.requestWithdraw(/*instant amount*/ ..., address(AAtranche)); // instant path
vm.prank(manager);
cdoEpoch.getInstantWithdrawFunds(); // collects 1000 -> pendingInstantWithdraws = 0
vm.prank(alice);
cdoEpoch.claimInstantWithdrawRequest(); // paid 1000; byEpoch mappings stale

// 2) Bob requests instant withdraw 500, stays unfunded
vm.prank(bob);
cdoEpoch.requestWithdraw(..., address(AAtranche));
assertEq(strategy.pendingInstantWithdraws(), 500 * ONE_SCALE);
assertEq(strategy.instantWithdrawClaimsByEpoch(strategy.epochNumber()), 1_500 * ONE_SCALE); // stale Alice basis

// 3) Borrower defaults; finalize recovery
// ... trigger default and call finalizeDefaultRecovery via CDO ...
uint256 basis = strategy.defaultPendingClaimBasis();
// BUG: basis includes Alice's already-paid 1000; _defaultPrefundedInstantReserve()
// reports 1000 phantom tokens as held. recoveryPrice is inflated and the strategy's
// real balance cannot cover all claims -> last claimant's _transferDefaultRecovery reverts.
```

Assert the failure mode: `underlying.balanceOf(strategy) < total expected recovery payouts`, and Bob's or the last defaulted claim reverts / active `defaultBBNav` exceeds real NAV.

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L366-374)
```text
    instantWithdrawsRequests[_user] += _amount;
    uint256 currentEpoch = epochNumber;
    // we record both per-user (old, kept for compatibility) and per-epoch so on
    // finalization we can distinguish "default-epoch pending instant receipts"
    // from old funded instant receipts.
    instantWithdrawsRequestsByEpoch[_user][currentEpoch] += _amount;
    instantWithdrawClaimsByEpoch[currentEpoch] += _amount;
    // increase the total instant withdraw requests
    pendingInstantWithdraws += _amount;
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L398-403)
```text
  function collectInstantWithdrawFunds(uint256 _amount) external {
    _onlyIdleCDO();
    if (_amount == 0) return;
    pendingInstantWithdraws -= _amount;
    underlyingToken.safeTransferFrom(idleCDO, address(this), _amount);
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L685-692)
```text
    uint256 prefundedReserve = _defaultPrefundedInstantReserve();
    uint256 reserveAmount = _recoveredAmount + prefundedReserve + defaultRecoveryReserve;
    // Recovery can be above par if the recovered funds exceed the computed basis.
    uint256 recoveryPrice = reserveAmount * RECOVERY_FULL / totalBasis;

    defaultRecoveryFinalized = true;
    defaultRecoveryReserve = reserveAmount;
    defaultRecoveryPrice = recoveryPrice;
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L716-723)
```text
  function _defaultPrefundedInstantReserve() internal view returns (uint256 prefundedReserve) {
    uint256 pendingInstant = pendingInstantWithdraws;
    if (pendingInstant == 0) return prefundedReserve;
    uint256 instantBasis = instantWithdrawClaimsByEpoch[epochNumber];
    if (instantBasis > pendingInstant) {
      prefundedReserve = instantBasis - pendingInstant;
    }
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L842-855)
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
```
