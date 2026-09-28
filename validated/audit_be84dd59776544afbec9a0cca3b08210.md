The bug class is "state cleared but not fully re-initialized, leaving stale fields that cause incorrect reuse." The closest surface is `IdleCreditVault`'s withdraw-receipt clearing. Let me examine the instant-withdraw claim and `_settleApr0` paths for stale-entry reuse.### Title
Stale `instantWithdrawsRequestsByEpoch` entry is re-claimed after default, letting a user double-draw the recovery reserve - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`claimInstantWithdrawRequest` clears only the aggregate `instantWithdrawsRequests[_user]` but never clears the per-epoch ledger `instantWithdrawsRequestsByEpoch[_user][epoch]`. If the borrower defaults in the same epoch, `_claimDefaultedInstantWithdrawRequest` reads that stale per-epoch entry and pays the user a recovery payout on a receipt that was already paid in full, draining the default-recovery reserve at the expense of other defaulted-epoch claimants.

### Finding Description
When a user requests an instant withdrawal, `requestInstantWithdraw` records the amount in two places: the aggregate `instantWithdrawsRequests[_user]` and the per-epoch `instantWithdrawsRequestsByEpoch[_user][currentEpoch]`, and mints receipt strategy tokens 1:1 [1](#0-0) .

When the user claims during the running epoch, `claimInstantWithdrawRequest` burns the receipts, zeroes `instantWithdrawsRequests[_user]` and pays funded underlyings — but the per-epoch entry `instantWithdrawsRequestsByEpoch[_user][epoch]` is left behind [2](#0-1) .

If the borrower then defaults within that same epoch (honest manager triggers `stopEpoch`/`_handleBorrowerDefault`, finalizing with `defaultRecoveryEpoch == epochNumber`), the defaulted-claim path reads `instantWithdrawsRequestsByEpoch[_user][defaultEpoch]` — which still contains the already-paid amount [3](#0-2) . It burns `claimBasis` receipt tokens and transfers `(claimBasis * defaultRecoveryPrice) / RECOVERY_FULL` from the recovery reserve via `_transferDefaultRecovery` [4](#0-3) .

To satisfy the `_burn`, the attacker simply makes a second instant request in the same epoch before default finalization (each request mints `_amount` receipts). Because `claimInstantWithdrawRequest` calls the defaulted claim first and then the funded path, the attacker can even be re-paid in a single call. The same stale-entry issue inflates `instantWithdrawClaimsByEpoch[defaultEpoch]`, which is used to size the defaulted-epoch instant-claim bucket.

This mirrors the kernel bug exactly: clearing a structure (`instantWithdrawsRequests[_user] = 0`) without re-initializing its dependent fields (`instantWithdrawsRequestsByEpoch`, and symmetrically `withdrawsRequestsByEpoch` in `_claimFundedWithdrawRequest`, which zeroes the aggregate but not per-epoch entries [5](#0-4) ), so a later reset/finalization path dereferences stale state.

### Impact Explanation
Direct theft of the default-recovery reserve. The attacker is paid twice on the same economic withdrawal: once at par pre-default, once at `defaultRecoveryPrice` post-default. The recovery reserve is fixed at `finalizeDefaultRecovery`, so every extra token paid to the attacker reduces what legitimate defaulted-epoch claimants (lenders, tranche holders) can recover — a haircut the pool did not intend, i.e., partial insolvency of the recovery bucket. Loss equals `(staleAmount * defaultRecoveryPrice) / RECOVERY_FULL` per replayed receipt.

### Likelihood Explanation
Requires: an epoch with instant withdrawals enabled (`setInstantWithdrawParams`), an unprivileged user requesting and claiming an instant withdrawal during the running epoch, then an honest borrower default finalizing that same epoch. Instant claims are a normal user action, and defaults are an expected protocol path — no privileged misbehavior needed. The attacker must hold ≥ `claimBasis` receipt tokens at burn time, trivially achieved by one additional instant request in the same epoch.

### Recommendation
In `claimInstantWithdrawRequest`, also delete the per-epoch entries for all epochs contributing to the claimed aggregate (or track and clear the request epoch analogously to `lastWithdrawRequest`), and decrement `instantWithdrawClaimsByEpoch[epoch]` so the defaulted-epoch bucket only counts unfunded receipts. Apply the same fix in `_claimFundedWithdrawRequest`, which clears `withdrawsRequests[_user]` without clearing `withdrawsRequestsByEpoch[_user][*]` — a funded claim followed by a default tagged on a stale epoch key would replay there too.

### Proof of Concept
```solidity
// test/foundry/IdleCreditVaultStaleInstantClaim.t.sol
function testStaleInstantRequestDoubleClaimOnDefault() external {
    // running epoch E with instant withdrawals enabled
    vm.prank(manager);
    cdoEpoch.setInstantWithdrawParams(100, 1e18, false);
    vm.prank(manager);
    cdoEpoch.startEpoch();

    uint256 amount = 100e6;
    address alice = makeAddr('alice');
    _depositWithUser(alice, amount);          // alice holds tranche tokens

    // 1) alice requests + claims an instant withdraw in epoch E (paid at par)
    _requestInstantWithdrawWithUser(alice, amount);
    // ... idleCDO collects/funds and alice claims via claimInstantWithdrawRequest
    //     -> instantWithdrawsRequests[alice] == 0
    //     -> instantWithdrawsRequestsByEpoch[alice][E] == amount   // STALE

    // 2) alice makes a second instant request so she holds receipts for the burn
    _requestInstantWithdrawWithUser(alice, amount);

    // 3) borrower defaults in epoch E; owner/manager finalize default recovery
    _triggerBorrowerDefault();                // defaultRecoveryEpoch == E
    _finalizeDefaultRecovery();               // defaultRecoveryFinalized = true

    // 4) alice claims: defaulted path reads stale by-epoch[E] entry (2*amount incl.
    //    the new one), burns receipts she holds, and pays recovery on the
    //    already-paid first withdrawal as well.
    uint256 balPre = underlying.balanceOf(alice);
    cdoEpoch.claimInstantWithdrawRequest(alice);
    uint256 paid = underlying.balanceOf(alice) - balPre;

    // paid includes haircut value of the already-funded first receipt
    assertGt(paid, amount * strategy.defaultRecoveryPrice() / RECOVERY_FULL);
    // recovery reserve is drained beyond the legitimately pending basis:
    // other defaulted claimants' _transferDefaultRecovery now underflows/reverts.
}
```

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L338-348)
```text
    uint256 normalAmount = withdrawsRequests[_user];
    uint256 apr0PrincipalAmount = _apr0User.settledPrincipal + _apr0User.principal;
    uint256 apr0InterestAmount = _apr0User.settledInterest;
    amount = normalAmount + apr0PrincipalAmount + apr0InterestAmount;
    // burn strategy tokens 1:1 with the principal only (normal amount already includes interest)
    _burn(_user, normalAmount + apr0PrincipalAmount);
    withdrawsRequests[_user] = 0;
    lastWithdrawRequest[_user] = 0;
    if (apr0PrincipalAmount != 0 || apr0InterestAmount != 0) {
      delete apr0Users[_user];
    }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L363-372)
```text
    _mint(_user, _amount);

    // increase the instant withdraw requests for the user
    instantWithdrawsRequests[_user] += _amount;
    uint256 currentEpoch = epochNumber;
    // we record both per-user (old, kept for compatibility) and per-epoch so on
    // finalization we can distinguish "default-epoch pending instant receipts"
    // from old funded instant receipts.
    instantWithdrawsRequestsByEpoch[_user][currentEpoch] += _amount;
    instantWithdrawClaimsByEpoch[currentEpoch] += _amount;
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L387-392)
```text
    uint256 amount = instantWithdrawsRequests[_user];
    // burn strategy tokens from user
    _burn(_user, amount);

    instantWithdrawsRequests[_user] = 0;
    _transferFundedClaim(_user, amount);
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L843-848)
```text
    uint256 defaultEpoch = defaultRecoveryEpoch;
    claimBasis = instantWithdrawsRequestsByEpoch[_user][defaultEpoch];
    if (claimBasis == 0) return claimBasis;

    instantWithdrawsRequestsByEpoch[_user][defaultEpoch] = 0;
    instantWithdrawsRequests[_user] -= claimBasis;
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L852-855)
```text
    pendingInstantWithdraws = claimBasis >= pending ? 0 : pending - claimBasis;
    instantWithdrawClaimsByEpoch[defaultEpoch] -= claimBasis;
    _burn(_user, claimBasis);
    _transferDefaultRecovery(_user, (claimBasis * defaultRecoveryPrice) / RECOVERY_FULL);
```
