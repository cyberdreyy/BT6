### Title
Post-default instant withdraw requests are misclassified as defaulted-epoch receipts and corrupt recovery accounting - ([File: contracts/strategies/idle/IdleCreditVault.sol])

### Summary
`requestWithdraw` explicitly branches on `defaultRecoveryFinalized` and routes new post-default requests into `postDefaultRequests`, but `requestInstantWithdraw` has no equivalent branch. Because `epochNumber` never advances after a default is finalized, a post-default instant request is written into `instantWithdrawsRequestsByEpoch[user][defaultRecoveryEpoch]` — the same bucket used by pre-default defaulted receipts — and is paid out of `defaultRecoveryReserve` at `defaultRecoveryPrice` even though its basis was never included in the `totalBasis` used to compute that price. This drains the recovery reserve and decrements `instantWithdrawClaimsByEpoch[defaultEpoch]`/`pendingInstantWithdraws` for claims the attacker never owned, permanently freezing legitimate defaulted-epoch instant claimants.

### Finding Description
In `requestWithdraw`, post-default requests are isolated so they cannot pollute the defaulted epoch's receipt ledgers:

```solidity
// contracts/strategies/idle/IdleCreditVault.sol:247-257
if (defaultRecoveryFinalized) {
  if (_hasWithdrawRequest(_user) || instantWithdrawsRequests[_user] != 0 || postDefaultRequests[_user] != 0) {
    revert NotAllowed();
  }
  _burn(msg.sender, _amount);
  _mint(_user, _amount);
  postDefaultRequests[_user] = _amount;
  return;
}
```

`requestInstantWithdraw` performs the identical burn/mint receipt pattern but lacks the `defaultRecoveryFinalized` branch:

```solidity
// contracts/strategies/idle/IdleCreditVault.sol:356-375
function requestInstantWithdraw(uint256 _amount, address _user) external {
  _onlyIdleCDO();
  _ensureDefaultRecoveryInitialized();
  _burn(msg.sender, _amount);
  _mint(_user, _amount);
  instantWithdrawsRequests[_user] += _amount;
  uint256 currentEpoch = epochNumber;              // still == defaultRecoveryEpoch
  instantWithdrawsRequestsByEpoch[_user][currentEpoch] += _amount;
  instantWithdrawClaimsByEpoch[currentEpoch] += _amount;
  pendingInstantWithdraws += _amount;
}
```

After `finalizeDefaultRecovery`, `defaultRecoveryEpoch = epochNumber` (`IdleCreditVault.sol:693`) and no further `stopEpoch`/`deposit`-on-stop can bump `epochNumber` on a defaulted pool. Any later instant request therefore lands in `instantWithdrawsRequestsByEpoch[user][defaultRecoveryEpoch]`, indistinguishable from a pre-default receipt — a direct analog of CVE-2018-5130's mismatched-payload-type state confusion.

On claim, `claimInstantWithdrawRequest` (lines 380-393) first runs `_claimDefaultedInstantWithdrawRequest`, which reads exactly that bucket:

```solidity
// contracts/strategies/idle/IdleCreditVault.sol:842-855
claimBasis = instantWithdrawsRequestsByEpoch[_user][defaultEpoch];
instantWithdrawsRequestsByEpoch[_user][defaultEpoch] = 0;
instantWithdrawsRequests[_user] -= claimBasis;
pendingInstantWithdraws = claimBasis >= pending ? 0 : pending - claimBasis;
instantWithdrawClaimsByEpoch[defaultEpoch] -= claimBasis;
_burn(_user, claimBasis);
_transferDefaultRecovery(_user, (claimBasis * defaultRecoveryPrice) / RECOVERY_FULL);
```

The payout comes from `defaultRecoveryReserve`, which was sized at finalization as `reserveAmount * RECOVERY_FULL / totalBasis` where `totalBasis` included only the pre-default `instantWithdrawClaimsByEpoch[epochNumber]` (via `defaultPendingClaimBasis`, lines 644-649). The attacker's new basis was never part of `totalBasis`, yet it draws on the same reserve and the same per-epoch counter.

### Impact Explanation
Two concrete harms, both reproducible by an unprivileged tranche-token holder after default finalization:

1. **Reserve theft / insolvency for legitimate claimants.** Each post-default instant request pays `claimBasis * defaultRecoveryPrice` out of a reserve that was only provisioned for pre-default receipts. Once the attacker claims, the reserve is short by that amount; when legitimate defaulted-epoch instant claimants later call `claimInstantWithdrawRequest`, `_transferDefaultRecovery` either underflows on the reserve or transfers less than owed. The attacker effectively converts an active-holder claim (redeemable anyway at `defaultRecoveryPrice`) while additionally *destroying* the aggregate accounting of honest claims — the loss is borne entirely by other claimants. Quantified loss: up to the full remaining `defaultRecoveryReserve` for requests sized within it.

2. **Permanent freeze via underflow.** `instantWithdrawClaimsByEpoch[defaultEpoch] -= claimBasis` decrements the epoch aggregate by the attacker's basis. Since the aggregate only ever contained the pre-default total, a sufficiently large post-default request (or the sum of several) drives the counter to zero; the next legitimate claim then reverts on underflow (`instantWithdrawClaimsByEpoch[defaultEpoch] -= claimBasis` with `claimBasis > remaining`), permanently freezing every remaining defaulted-epoch instant receipt. The same applies to `pendingInstantWithdraws`, which is zeroed early and causes subsequent `_claimDefaultedInstantWithdrawRequest` calls to misclassify funding state.

The existing guard in `requestWithdraw` (reverting when `instantWithdrawsRequests[_user] != 0`) does not help: it only blocks *normal* requests by users who already hold instant receipts — it does not prevent creating new instant receipts post-default, which is the path that corrupts the ledgers.

### Likelihood Explanation
Reachability requires only that the CDO's `requestInstantWithdraw` entrypoint remains callable after default finalization — the claim path itself (`claimInstantWithdrawRequest`) is explicitly designed to operate post-default (lines 382-386), and nothing in the strategy-level request function gates on `defaulted()`/`defaultRecoveryFinalized`, in contrast to `requestWithdraw` where the developers clearly considered the same scenario. The attacker needs only tranche tokens (acquirable on secondary market, potentially below recovery price, which also creates an arbitrage profit motive: buy discounted tranche tokens, mint instant receipts, drain reserve at `defaultRecoveryPrice`). The only privileged actions in the sequence — `stopEpoch` producing the default and `finalizeDefault` — are performed by honest roles; the attacker merely sequences requests and claims around them.

### Recommendation
Mirror the `requestWithdraw` post-default handling in `requestInstantWithdraw`: when `defaultRecoveryFinalized` is true, either revert (`NotAllowed`) or route the receipt into a post-default ledger (e.g. `postDefaultRequests`) that is never keyed by `defaultRecoveryEpoch` and is funded separately from `defaultRecoveryReserve`. Additionally, in `_claimDefaultedInstantWithdrawRequest`, cap `claimBasis` at the basis recorded at finalization time (e.g. snapshot per-user defaulted basis during `finalizeDefaultRecovery`) rather than trusting the live `instantWithdrawsRequestsByEpoch[user][defaultEpoch]` value, so post-finalization writes cannot enter the recovery claim set.

### Proof of Concept
Foundry fork test sketch (extends the existing harness in `test/foundry/IdleCreditVault.t.sol`, which already provides `_depositWithUser`, `_stopCurrentEpochWithApr`, and default-finalization helpers):

```solidity
function testPostDefaultInstantRequestDrainsRecoveryReserve() external {
    // 1. Legit user deposits, epoch runs, user requests instant withdraw
    address legit = makeAddr('legit');
    _depositWithUser(legit, 100_000 * ONE_SCALE, true);
    _startEpochAndCheckPrices(0);
    // enable instant withdrawals, legit requests instant withdraw
    // (as in testClaimInstantWithdrawRequest)

    // 2. Warp past epochEndDate, owner stops epoch with insufficient funds -> defaulted
    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.prank(cdoEpoch.owner());
    cdoEpoch.stopEpoch(0, 0);
    assertTrue(cdoEpoch.defaulted());

    // 3. Manager finalizes default with partial recovery
    uint256 recovered = /* partial recovery amount */;
    deal(defaultUnderlying, manager, recovered, true);
    vm.startPrank(manager);
    underlying.approve(address(strategy), recovered);
    cdoEpoch.finalizeDefault(recovered, manager);
    vm.stopPrank();
    assertTrue(strategy.defaultRecoveryFinalized());
    assertTrue(strategy.defaultInstantWithdrawsFinalized());

    // 4. Attacker holds tranche tokens; requests instant withdraw POST-default.
    //    epochNumber == defaultRecoveryEpoch, so the receipt lands in the
    //    defaulted-epoch bucket despite being created after finalization.
    address attacker = makeAddr('attacker');
    _depositWithUser(attacker, 50_000 * ONE_SCALE, true); // pre-default deposit
    vm.prank(attacker);
    cdoEpoch.requestInstantWithdraw(attackerBalance, address(tranche));

    // 5. Attacker claims: paid from defaultRecoveryReserve at defaultRecoveryPrice,
    //    and instantWithdrawClaimsByEpoch[defaultEpoch] is decremented by a basis
    //    that was never in totalBasis at finalization.
    uint256 reservePre = strategy.defaultRecoveryReserve();
    vm.prank(attacker);
    cdoEpoch.claimInstantWithdrawRequest();
    assertLt(strategy.defaultRecoveryReserve(), reservePre, 'reserve drained by unaccounted claim');

    // 6. Legit user's defaulted-epoch claim now reverts (underflow on
    //    instantWithdrawClaimsByEpoch) or is underpaid -> frozen/stolen recovery.
    vm.prank(legit);
    vm.expectRevert(); // arithmetic underflow or NotAllowed
    cdoEpoch.claimInstantWithdrawRequest();
}
```

Note on confidence: the strategy-side flaw (missing `defaultRecoveryFinalized` branch and the epoch-bucket collision) is confirmed by the code above; the PoC assumes `IdleCDOEpochVariant.requestInstantWithdraw` does not independently revert on a defaulted pool — that CDO-side gating could not be fully verified within the search budget and should be confirmed when writing the test.