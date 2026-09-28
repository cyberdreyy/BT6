### Title
Post-default withdraw requests are recorded under the same (un-bumped) `epochNumber` as defaulted receipts, inflating the recovery claim basis and diluting victim payouts - ([File: contracts/strategies/idle/IdleCreditVault.sol](contracts/strategies/idle/IdleCreditVault.sol))

### Summary
The analog of "different rounds sharing the same version id" is `IdleCreditVault`'s per-epoch receipt indexing. `epochNumber` is only bumped inside `deposit()` when a stopEpoch deposit lands [1](#0-0) , and on borrower default the epoch number is deliberately *not* incremented [2](#0-1) . `requestWithdraw` and `requestInstantWithdraw` key receipts by the current `epochNumber` with no `defaulted` guard — only `defaultRecoveryFinalized` is checked [3](#0-2) [4](#0-3) . A request made after default but before `finalizeDefaultRecovery` therefore lands in `withdrawsRequestsByEpoch[user][N]` / `instantWithdrawsRequestsByEpoch[user][N]` — the *same* epoch id `N` (`defaultRecoveryEpoch`) that stores the defaulted receipts.

### Finding Description
Two distinct "rounds" collapse onto one version id:

1. Epoch N ends in borrower default. `epochNumber` stays `N`; `defaultRecoveryEpoch = N` at finalization.
2. Before finalization, an attacker holding tranche tokens calls the CDO's withdraw path, reaching `requestWithdraw`/`requestInstantWithdraw` on the vault. The `defaultRecoveryFinalized` branch is skipped, `isClosed` is false, and the request is recorded under `currentEpoch = N` while `pendingWithdraws += _amount` and `instantWithdrawClaimsByEpoch[N] += _amount` run [5](#0-4) [6](#0-5) .
3. `finalizeDefaultRecovery` computes the recovery price over `defaultPendingClaimBasis()`, which is `pendingWithdraws + instantWithdrawClaimsByEpoch[epochNumber]` [7](#0-6) . The attacker's fresh, unfunded receipt inflates the basis while contributing zero recovered assets, so `defaultRecoveryPrice` is pushed down for every legitimate defaulted receipt.
4. The attacker then claims through `_claimDefaultedWithdrawRequest`/`_claimDefaultedInstantWithdrawRequest`, which pay `claimBasis * defaultRecoveryPrice / RECOVERY_FULL` from the recovery reserve to any receipt keyed under `defaultEpoch` [8](#0-7) . The reserve is finite; the attacker's share is paid out at the expense of real victims.

Additionally, mixing pre- and post-default receipts under one epoch key corrupts `_clearWithdrawClaimForEpoch`'s per-epoch bookkeeping (`withdrawsRequestsByEpoch`, `apr0TotalPrincipal`, `lastWithdrawRequest`), which assumes one id maps to one round of requests [9](#0-8) .

### Impact Explanation
Direct theft-by-dilution: every unit the attacker adds to the defaulted-epoch basis mints a haircut-priced claim funded by the same recovery pool as honest defaulted receipts, draining the reserve and permanently reducing other users' payouts. The magnitude scales with the attacker's tranche holdings relative to total defaulted claims.

### Likelihood Explanation
Requires a default (infrequent but supported flow) and the attacker to act in the window between `_handleBorrowerDefault` and `finalizeDefaultRecovery`, while holding or buying tranche tokens cheaply post-default. Caveat I could not fully verify within the exploration budget: whether the CDO-side entry points (`IdleCDOEpochVariant.requestWithdraw` / instant-withdraw path) are reachable while `defaulted` and not yet finalized; the vault functions themselves contain no `defaulted` check, so if any CDO path reaches them the exploit holds. If all entry points already revert when defaulted, this reduces to a defense-in-depth gap rather than an exploitable bug.

### Recommendation
Reject new withdraw/instant requests once the vault is in a defaulted-but-unfinalized state, e.g. in `requestWithdraw` and `requestInstantWithdraw`:

```solidity
if (IIdleCDOEpochVariant(idleCDO).defaulted() && !defaultRecoveryFinalized) revert NotAllowed();
```

Alternatively, bump `epochNumber` (or use a dedicated post-default epoch key) on default so post-default requests can never share `defaultRecoveryEpoch` with defaulted receipts — mirroring the report's fix of making the version strictly increasing across rounds.

### Proof of Concept
Foundry fork PoC sketch (test base: `test/foundry/IdleCreditVault.t.sol`):

```solidity
// 1. Users deposit, epoch N starts.
idleCDO.depositAA(victimAmount);
vm.prank(attacker); idleCDO.depositAA(attackerAmount);
vm.prank(manager); cdoEpoch.startEpoch();

// 2. Victim requests withdraw during epoch N (recorded under epoch N).
vm.prank(victim); cdoEpoch.requestWithdraw(victimShares, AAtranche);

// 3. Borrower defaults; manager triggers default handling (no epochNumber bump).
vm.prank(manager); cdoEpoch.stopEpochWithDuration(1, 0); // or _handleBorrowerDefault path
assertEq(strategy.epochNumber(), N); // unchanged on default

// 4. BEFORE finalizeDefaultRecovery, attacker requests withdraw of all tranches.
vm.prank(attacker); cdoEpoch.requestInstantWithdraw(attackerShares, AAtranche); // or requestWithdraw
// attacker receipt is now in instantWithdrawsRequestsByEpoch[attacker][N]

// 5. Finalize recovery and show dilution.
uint256 basis = strategy.defaultPendingClaimBasis();
uint256 recovery = strategy.finalizeDefaultRecovery(...);
// attacker claims haircut-priced payout despite post-default receipt
vm.prank(attacker); cdoEpoch.claimInstantWithdrawRequest(attacker);
// assert victim's recovered amount < expected and attacker recovered > 0
```

Expected assertion: `instantWithdrawClaimsByEpoch[N]` (or `pendingWithdraws`) includes the attacker's post-default amount, `defaultRecoveryPrice` is lower than the honest-only value, and the attacker pulls recovery funds for a receipt created after default.

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L243-258)
```text
  function requestWithdraw(uint256 _amount, address _user, uint256 _principal) external {
    _onlyIdleCDO();
    _ensureDefaultRecoveryInitialized();
    if (_amount == 0) return;
    if (defaultRecoveryFinalized) {
      // user should first claim old already-funded withdraw requests before requesting new ones after default
      if (_hasWithdrawRequest(_user) || instantWithdrawsRequests[_user] != 0 || postDefaultRequests[_user] != 0) {
        revert NotAllowed();
      }
      // Preserve request/claim UX after default without increasing borrower-facing pendingWithdraws.
      // The CDO passes an already-haircut amount because finalization lowered virtualPrice first.
      _burn(msg.sender, _amount);
      _mint(_user, _amount);
      postDefaultRequests[_user] = _amount;
      return;
    }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L277-294)
```text
    if (!isClosed) {
      // Global amount that stopEpoch must source from borrower/strategy for all pending receipts.
      pendingWithdraws += _amount;
    }
    // save the epoch of the last withdraw request (buffer + epochDuration is 1 epoch)
    lastWithdrawRequest[_user] = currentEpoch;
    // APR=0 requests keep separate accounting and settle interest at stopEpoch.
    // `_amount` here is the post-management-fee principal bucket for that flow.
    if (unscaledApr == 0 && !isClosed) {
      _requestWithdrawApr0(_amount, _user);
    } else {
      // increase the withdraw requests for the user
      // we record both per-user (old, kept for compatibility) and per-epoch so
      // on finalization we can distinguish "default-epoch pending receipts"
      // from old funded receipts.
      withdrawsRequests[_user] += _amount;
      withdrawsRequestsByEpoch[_user][currentEpoch] += _amount;
    }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L356-375)
```text
  function requestInstantWithdraw(uint256 _amount, address _user) external {
    _onlyIdleCDO();
    _ensureDefaultRecoveryInitialized();
    // burn strategy tokens from cdo
    _burn(msg.sender, _amount);
  
    // mint equal amount of strategy tokens to the user as receipt, useful in case of default
    _mint(_user, _amount);

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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L607-614)
```text
    if (IIdleCDOEpochVariant(idleCDO).isEpochRunning()) {
      // deposit done on stopEpoch (before setting the var to false) so we reset the counter
      totEpochDeposits = 0;
      epochNumber += 1;
    } else {
      // deposit done between epochs so we increase the counter
      totEpochDeposits += _amount;
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L811-837)
```text
  function _clearWithdrawClaimForEpoch(address _user, uint256 _claimEpoch, bool _isClearingApr0) internal returns (uint256 claimBasis, uint256 burnAmount) {
    (claimBasis, burnAmount) = _withdrawClaimAmountsForEpoch(_user, _claimEpoch);
    if (claimBasis == 0) return (claimBasis, burnAmount);

    uint256 normalAmount = withdrawsRequestsByEpoch[_user][_claimEpoch];
    if (normalAmount != 0) {
      withdrawsRequestsByEpoch[_user][_claimEpoch] = 0;
      // The aggregate may also include older funded receipts; clear only this epoch's piece.
      withdrawsRequests[_user] -= normalAmount;
    }
    Apr0UserData storage apr0User = apr0Users[_user];
    if (apr0User.principal != 0 && apr0User.principalEpoch == _claimEpoch) {
      if (_isClearingApr0) {
        uint256 apr0Principal = apr0User.principal;
        uint256 totalApr0Principal = apr0TotalPrincipal;
        // prepareStopEpochWithApr0 may already close the global APR0 bucket before default finalization.
        apr0TotalPrincipal = apr0Principal >= totalApr0Principal ? 0 : totalApr0Principal - apr0Principal;
      }
      apr0User.principal = 0;
      apr0User.principalEpoch = 0;
    }
    if (lastWithdrawRequest[_user] == _claimEpoch) {
      // The cleared epoch was the latest request marker. Any remaining normal/APR0 receipt
      // is older and already funded, so it can continue to the funded-claim path.
      lastWithdrawRequest[_user] = 0;
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

**File:** contracts/IdleCDOEpochQueue.sol (L288-291)
```text
  function _prefundedEpochToProcess(IdleCDOEpochVariant _cdo) internal view returns (uint256 _epoch) {
    // Prefunded deposits still belong to the epoch that just finished and must be settled against that epoch id.
    _epoch = IdleCreditVault(strategy).epochNumber() + (_cdo.defaulted() ? 1 : 0);
  }
```
