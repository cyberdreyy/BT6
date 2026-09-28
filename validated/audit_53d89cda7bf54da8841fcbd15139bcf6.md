### Title
Instant-withdraw receipts are keyed to the request epoch but claimed at `defaultRecoveryEpoch`, so defaulted instant claims fall through to the par-value funded path - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`requestInstantWithdraw` records a user's instant-receipt under the *current* `epochNumber` at request time, but `_claimDefaultedInstantWithdrawRequest` looks the receipt up under `defaultRecoveryEpoch`, which is only set later at `finalizeDefaultRecovery`. Because `deposit()` bumps `epochNumber` inside the `stopEpoch` path, any instant request made during a running epoch is stored under `N` while the default is finalized under `N+1`. The per-epoch lookup therefore always returns `0` for those users, the haircut branch is skipped, and execution falls through to `claimInstantWithdrawRequest`'s funded path which pays `instantWithdrawsRequests[_user]` at par — the exact analog of using index `0` where index `1` was intended.

### Finding Description [1](#0-0) 

`requestInstantWithdraw` stores the per-epoch receipt keyed by `epochNumber` **at request time** (epoch `N`). [2](#0-1) 

`deposit()` — invoked by the CDO during `stopEpoch` — increments `epochNumber` to `N+1` when the epoch was running. [3](#0-2) 

`finalizeDefaultRecovery` then sets `defaultRecoveryEpoch = epochNumber` (= `N+1`). [4](#0-3) 

`_claimDefaultedInstantWithdrawRequest` reads `instantWithdrawsRequestsByEpoch[_user][defaultRecoveryEpoch]` — i.e. slot `N+1` — which is `0` for every user who requested during epoch `N`. It returns early without decrementing `instantWithdrawsRequests[_user]`, `pendingInstantWithdraws`, or `instantWithdrawClaimsByEpoch[N]`, and without burning the receipt.

`claimInstantWithdrawRequest` then continues and pays the full un-haircutted amount through `_transferFundedClaim`: [5](#0-4) 

The same wrong-key mismatch exists in `defaultPendingClaimBasis` and `_defaultPrefundedInstantReserve`: both read `instantWithdrawClaimsByEpoch[epochNumber]` at *finalization* time (`N+1`), so current-epoch instant receipts recorded under `N` are silently excluded from the default claim basis and their already-held prefunded cash is never added to `defaultRecoveryReserve`. [6](#0-5) [7](#0-6) 

### Impact Explanation
Two failure modes, both stemming from the epoch-index mismatch:

1. **Theft at par / reserve bypass.** If the strategy holds instant-withdraw underlyings prefunded during epoch `N` (e.g., `collectInstantWithdrawFunds` partially ran), `_defaultPrefundedInstantReserve` computes `0` because `instantWithdrawClaimsByEpoch[N+1] == 0`, so that cash is *not* isolated into `defaultRecoveryReserve`. The `_transferFundedClaim` guard (`balance - reserve < amount`) then does not protect it, and the instant requester burns his receipt and withdraws **100% of his request** instead of `claimBasis * defaultRecoveryPrice / RECOVERY_FULL`. Against a defaulted pool this directly steals funds that belong to haircutted claimants, and permanently inflates `pendingInstantWithdraws`/`instantWithdrawClaimsByEpoch[N]` beyond backing.

2. **Permanent freeze.** If no unreserved balance exists, `_transferFundedClaim` reverts (`NotAllowed`), and since the haircut path never clears `instantWithdrawsRequests[_user]`, the user can never claim anything — his recovery share is permanently frozen.

Either way the invariant "defaulted receipts are paid at `defaultRecoveryPrice` from the isolated reserve" is broken.

### Likelihood Explanation
Triggering requires only ordinary, unprivileged timing: a user calls `requestInstantWithdraw` while an epoch is running (epoch `N`), the honest borrower/manager then `stopEpoch`s with insufficient repayment and the vault defaults, and `finalizeDefault` runs in epoch `N+1`. No privileged misbehavior, oracle manipulation, or donation is needed — any instant request that survives unfunded across the `stopEpoch` boundary hits the wrong key. The only constraint is that `pendingInstantWithdraws != 0` at finalization so that `defaultInstantWithdrawsFinalized` is set, i.e. the instant queue was not fully funded before default — precisely the scenario the epoch-indexed accounting was added to handle.

### Recommendation
Key defaulted instant claims by the epoch the receipts were actually recorded in, not the finalization epoch — e.g., iterate/settle against `instantWithdrawsRequestsByEpoch[_user][epoch]` for the epochs that contributed to `pendingInstantWithdraws`, or snapshot the "request epoch" when the request is made (e.g., store `defaultRecoveryEpoch` as the epoch in which the requests were pending, `epochNumber - 1` when the default crosses a stop boundary). Apply the same fix consistently in `defaultPendingClaimBasis` and `_defaultPrefundedInstantReserve` so basis, reserve and claim path all reference the same epoch index — mirroring the upstream fix of changing the second consumer's index from `0` to `1`.

### Proof of Concept
Foundry fork PoC sketch (single vault, instant-withdraw enabled):

```solidity
// test/foundry/IdleCreditVaultInstantIndex.t.sol
function testInstantReceiptWrongEpochKey() external {
  _useStandardEpochVariant();
  _stopCurrentEpochWithApr(10e18);            // epoch #1 buffer

  // enable instant withdrawals
  vm.prank(manager);
  cdoEpoch.setInstantWithdrawParams(100, 1e18, false);

  // user deposits during buffer, epoch running
  uint256 amt = 100e6;
  address alice = makeAddr("alice");
  uint256 tr = _depositWithUser(alice, amt);
  vm.prank(manager);
  cdoEpoch.startEpoch();                      // epoch N running

  // Alice requests instant withdraw -> recorded under instantWithdrawsRequestsByEpoch[alice][N]
  vm.prank(alice);
  cdoEpoch.requestInstantWithdraw(tr, address(tranche));
  uint256 reqEpoch = strategy.epochNumber();

  // Borrower under-repays -> stopEpoch bumps epochNumber to N+1 and vault defaults
  vm.warp(cdoEpoch.epochEndDate() + 1);
  vm.prank(manager);
  cdoEpoch.stopEpoch(0, 0);
  assertTrue(cdoEpoch.defaulted());
  uint256 defaultEpoch = strategy.epochNumber();
  assertGt(defaultEpoch, reqEpoch);           // keys diverge

  // Partial recovery finalized -> defaultRecoveryEpoch = N+1
  uint256 recovered = strategy.balanceOf(address(cdoEpoch)) / 2;
  deal(address(underlying), manager, recovered, true);
  vm.startPrank(manager);
  underlying.approve(address(strategy), recovered);
  cdoEpoch.finalizeDefault(recovered, manager);
  vm.stopPrank();
  assertEq(strategy.instantWithdrawsRequestsByEpoch(alice, defaultEpoch), 0); // wrong key

  // Alice claims: haircut path sees 0, funded path pays full amount at par
  uint256 balPre = underlying.balanceOf(alice);
  vm.prank(alice);
  cdoEpoch.claimInstantWithdrawRequest();
  uint256 paid = underlying.balanceOf(alice) - balPre;
  uint256 fairPay = tr * strategy.defaultRecoveryPrice() / strategy.RECOVERY_FULL();
  assertGt(paid, fairPay);                    // stole unreserved prefunded cash
  assertEq(strategy.instantWithdrawsRequests(alice), 0); // aggregate cleared but epoch ledger stays dirty
}
```

Caveats: I verified the index divergence statically (`requestInstantWithdraw` uses request-time `epochNumber`; `defaultRecoveryEpoch` is set post-`stopEpoch` at a later `epochNumber`), but I could not fully confirm whether the in-scope `IdleCDOEpochVariant` ever leaves `pendingInstantWithdraws != 0` across a defaulting `stopEpoch` boundary — if prefunding always completes atomically in the same epoch, the unfunded-receipt precondition may be unreachable in some configurations, in which case the finding degrades to a frozen-funds edge case rather than a par-value theft.

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L367-374)
```text
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L690-696)
```text
    defaultRecoveryFinalized = true;
    defaultRecoveryReserve = reserveAmount;
    defaultRecoveryPrice = recoveryPrice;
    defaultRecoveryEpoch = epochNumber;
    // A non-zero pending instant bucket means current-epoch instant receipts were not fully funded
    // and must be paid through the same recovery ratio as normal pending receipts.
    defaultInstantWithdrawsFinalized = pendingInstantWithdraws != 0;
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L716-722)
```text
  function _defaultPrefundedInstantReserve() internal view returns (uint256 prefundedReserve) {
    uint256 pendingInstant = pendingInstantWithdraws;
    if (pendingInstant == 0) return prefundedReserve;
    uint256 instantBasis = instantWithdrawClaimsByEpoch[epochNumber];
    if (instantBasis > pendingInstant) {
      prefundedReserve = instantBasis - pendingInstant;
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
