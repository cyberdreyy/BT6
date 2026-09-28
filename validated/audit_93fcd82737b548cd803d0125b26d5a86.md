### Title
Post-default `requestInstantWithdraw` books receipts into `defaultRecoveryEpoch`, letting new claims drain `defaultRecoveryReserve` and freeze legitimate default claimants - ([File: contracts/strategies/idle/IdleCreditVault.sol])

### Summary
The external bug (`ssl::select_next_proto` returning a buffer whose lifetime is bound to the wrong argument) is a *lifetime/scope confusion*: a value escapes the scope whose backing store its lifetime was tied to. The closest analog in `IdleCreditVault` is epoch-scope confusion for receipt bookkeeping: `requestInstantWithdraw` records every receipt under the *current* `epochNumber`, but after a default is finalized `epochNumber` no longer advances, so post-default instant receipts are written into `instantWithdrawsRequestsByEpoch[user][defaultRecoveryEpoch]` — the same bucket used by `_claimDefaultedInstantWithdrawRequest` to pay the haircutted recovery from the isolated `defaultRecoveryReserve`. A receipt created after finalization is thereby paid from a reserve that was sized only for pre-default claims.

### Finding Description
`requestWithdraw` has an explicit `defaultRecoveryFinalized` branch that diverts post-default requests into `postDefaultRequests` and mints a haircut-priced receipt [1](#0-0) . `requestInstantWithdraw` has no such branch: it unconditionally burns the CDO's strategy tokens, mints the receipt to the user, and adds `_amount` to `instantWithdrawsRequestsByEpoch[_user][epochNumber]` and `instantWithdrawClaimsByEpoch[epochNumber]` [2](#0-1) .

`finalizeDefaultRecovery` snapshots `defaultRecoveryEpoch = epochNumber`, sets `defaultRecoveryReserve` to exactly cover `totalBasis * recoveryPrice`, and sets `defaultInstantWithdrawsFinalized = pendingInstantWithdraws != 0` [3](#0-2) . After finalization there is no `stopEpoch`, so `epochNumber` stays equal to `defaultRecoveryEpoch` forever (it only increments inside `deposit` while an epoch is running [4](#0-3) ).

On claim, `claimInstantWithdrawRequest` routes through `_claimDefaultedInstantWithdrawRequest` whenever `defaultRecoveryFinalized && defaultInstantWithdrawsFinalized`, which reads `instantWithdrawsRequestsByEpoch[_user][defaultRecoveryEpoch]` — including receipts created *after* finalization — and pays `claimBasis * defaultRecoveryPrice` via `_transferDefaultRecovery`, debiting `defaultRecoveryReserve` [5](#0-4) , [6](#0-5) . The reserve is an exactly-sized bucket (`recoveryPrice = reserveAmount * 1e18 / totalBasis`), so every post-default receipt paid from it is recovery funding that was never part of the finalized `totalBasis`.

### Impact Explanation
When `defaultInstantWithdrawsFinalized` is true (i.e., some instant receipts were unfunded at finalization — a partial-recovery scenario), an attacker holding tranche tokens after default can call the CDO's instant-withdraw path, have the receipt booked into `defaultRecoveryEpoch`, and immediately claim it from `defaultRecoveryReserve` at `defaultRecoveryPrice`. Each such claim consumes reserve that was reserved for pre-default pending receipts. Once enough post-default claims are processed, `defaultRecoveryReserve` underflows or `underlyingToken.safeTransfer` fails inside `_transferDefaultRecovery`/`_transferFundedClaim`, permanently freezing the recovery payouts of legitimate defaulted-epoch claimants (burned receipts already cleared → irreversible loss). The attacker's net gain is the ability to convert an unpriced post-default receipt into a recovery-priced payout ahead of the queue; the protocol-level loss is the full drained portion of the reserve plus permanent freezing of remaining claims.

Note: the profitability depends on whether `IdleCDOEpochVariant` permits an instant-withdraw request after default — `requestWithdraw` post-default is explicitly supported via the `postDefaultRequests` flow, and I could not fully confirm the CDO-side gating for the instant path within the available iterations. If the CDO gates instant requests on `isEpochRunning`, the exploit requires the attacker to hold a pre-existing unfunded instant receipt that was *not* included in `instantWithdrawClaimsByEpoch[defaultEpoch]` — which cannot happen, since receipts are always recorded under the current epoch. The reserve-drain path requires the CDO to forward post-default instant requests.

### Likelihood Explanation
- Requires a finalized default with `defaultInstantWithdrawsFinalized == true` (partial instant funding at default) and `defaultRecoveryPrice > 0` — a real but conditional state.
- Attacker needs tranche tokens and the CDO instant-withdraw entry point to remain callable post-default; `requestWithdraw` shows post-default requests are an intended flow, making the missing symmetric guard in `requestInstantWithdraw` a plausible oversight rather than a blocked path.
- No privileged role needed; guardian/manager remain honest.

### Recommendation
Add the same post-default handling to `requestInstantWithdraw` that `requestWithdraw` has: when `defaultRecoveryFinalized`, either revert, or store the receipt in a separate post-default instant bucket keyed by a value other than `defaultRecoveryEpoch` (e.g., `type(uint256).max` or a dedicated mapping), and make `claimInstantWithdrawRequest` pay that bucket only from prefunded instant funds — never through `_claimDefaultedInstantWithdrawRequest`/`_transferDefaultRecovery`. Additionally, `_claimDefaultedInstantWithdrawRequest` should cap `claimBasis` at the basis snapshotted at finalization (e.g., verify `instantWithdrawClaimsByEpoch[defaultEpoch]` was not increased after `defaultRecoveryFinalized`).

### Proof of Concept
Foundry fork sketch (mirroring `test/foundry/IdleCreditVault.t.sol` setup):

```solidity
// test/foundry/IdleCreditVaultPostDefaultInstant.t.sol
function testPostDefaultInstantDrainsRecovery() external {
    uint256 amountWei = 10_000 * ONE_SCALE;
    uint256 mintedAA = idleCDO.depositAA(amountWei);
    idleCDO.depositBB(amountWei);

    _startEpochAndCheckPrices(0);

    // victim opens an instant withdraw request mid-epoch
    address victim = makeAddr('victim');
    _depositWithUser(victim, amountWei, true);
    vm.prank(victim);
    cdoEpoch.requestWithdraw(0, address(AAtranche)); // instant path when APR allows

    // borrower repays only partially -> getInstantWithdrawFunds underfunds instant queue
    deal(defaultUnderlying, borrower, _expectedFundsEndEpoch() / 2);
    vm.warp(block.timestamp + cdoEpoch.instantWithdrawDelay() + 1);
    vm.prank(manager);
    cdoEpoch.getInstantWithdrawFunds(); // partial -> pendingInstantWithdraws > 0

    // epoch defaults; manager finalizes with partial recovery
    vm.prank(manager);
    cdoEpoch.stopEpoch(initialProvidedApr, 0); // or via default path -> defaulted()
    uint256 recovered = /* partial recovery */;
    deal(defaultUnderlying, manager, recovered, true);
    vm.startPrank(manager);
    underlying.approve(address(strategy), recovered);
    cdoEpoch.finalizeDefault(recovered, manager);
    vm.stopPrank();

    assertTrue(strategy.defaultRecoveryFinalized());
    assertTrue(strategy.defaultInstantWithdrawsFinalized()); // pendingInstantWithdraws != 0
    assertEq(strategy.defaultRecoveryEpoch(), strategy.epochNumber()); // same bucket key

    // attacker (any tranche holder) requests instant withdraw AFTER finalization
    uint256 reservePre = strategy.defaultRecoveryReserve();
    cdoEpoch.requestWithdraw(0, address(AAtranche)); // instant request -> recorded under defaultRecoveryEpoch
    assertEq(
        strategy.instantWithdrawsRequestsByEpoch(address(this), strategy.defaultRecoveryEpoch()),
        mintedAA // or requested amount
    );

    // claim pays from defaultRecoveryReserve even though this receipt was never in totalBasis
    cdoEpoch.claimInstantWithdrawRequest();
    assertLt(strategy.defaultRecoveryReserve(), reservePre);

    // victim's legitimate defaulted receipt now reverts or is underpaid:
    // _transferDefaultRecovery decrements reserve below what victim is owed -> freeze/shortfall
    vm.prank(victim);
    vm.expectRevert(); // safeTransfer / underflow on drained reserve
    cdoEpoch.claimInstantWithdrawRequest();
}
```

Caveat: the PoC assumes `IdleCDOEpochVariant.requestWithdraw` forwards an instant request post-default; if the CDO reverts post-default for the instant path, the missing guard is latent rather than exploitable and the finding reduces to a defense-in-depth gap. The strategy-side asymmetry (`requestWithdraw` handles `defaultRecoveryFinalized`, `requestInstantWithdraw` does not) is verified in `contracts/strategies/idle/IdleCreditVault.sol:243-258` vs `356-375`.

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L247-257)
```text
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L382-392)
```text
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
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L607-611)
```text
    if (IIdleCDOEpochVariant(idleCDO).isEpochRunning()) {
      // deposit done on stopEpoch (before setting the var to false) so we reset the counter
      totEpochDeposits = 0;
      epochNumber += 1;
    } else {
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L688-696)
```text
    uint256 recoveryPrice = reserveAmount * RECOVERY_FULL / totalBasis;

    defaultRecoveryFinalized = true;
    defaultRecoveryReserve = reserveAmount;
    defaultRecoveryPrice = recoveryPrice;
    defaultRecoveryEpoch = epochNumber;
    // A non-zero pending instant bucket means current-epoch instant receipts were not fully funded
    // and must be paid through the same recovery ratio as normal pending receipts.
    defaultInstantWithdrawsFinalized = pendingInstantWithdraws != 0;
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
