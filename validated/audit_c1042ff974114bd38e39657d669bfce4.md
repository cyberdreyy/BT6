### Title
Already-claimed instant-withdraw receipts are never cleared from `instantWithdrawsRequestsByEpoch`/`instantWithdrawClaimsByEpoch`, inflating default recovery basis and stranding recovery funds - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
CVE-2021-34555 crashes on a repeated (multi-value) header where the parser trusts a count that includes values already consumed. The analog in `IdleCreditVault` is identical in spirit: the per-epoch instant-withdraw ledgers `instantWithdrawsRequestsByEpoch[user][epoch]` and `instantWithdrawClaimsByEpoch[epoch]` are written in `requestInstantWithdraw` but are *never decremented on a successful funded claim* in `claimInstantWithdrawRequest`. If an epoch partially funds instant withdrawals and the borrower then defaults in that same epoch, `defaultPendingClaimBasis` and `_defaultPrefundedInstantReserve` count already-paid receipts again — a "multi-value" double count — corrupting `defaultRecoveryPrice` and `defaultRecoveryReserve` for all genuine claimants.

### Finding Description
`requestInstantWithdraw` records the receipt per epoch (`contracts/strategies/idle/IdleCreditVault.sol:371-372`):
```solidity
instantWithdrawsRequestsByEpoch[_user][currentEpoch] += _amount;
instantWithdrawClaimsByEpoch[currentEpoch] += _amount;
```
`claimInstantWithdrawRequest` (`:380-393`) burns the user receipt, zeroes `instantWithdrawsRequests[_user]`, and pays out via `_transferFundedClaim`, but leaves both per-epoch mappings untouched.

At default finalization, `defaultPendingClaimBasis` (`:644-649`) adds the *entire* `instantWithdrawClaimsByEpoch[epochNumber]` whenever `pendingInstantWithdraws != 0`, and `_defaultPrefundedInstantReserve` (`:716-723`) credits `instantBasis - pendingInstant` as underlying "already held" by the strategy — even though part of that basis belongs to receipts that were funded and *already paid out* (those tokens left the strategy when the user claimed). `finalizeDefaultRecovery` (`:661-710`) then:
- includes the ghost basis in `totalBasis`, and
- includes the ghost "prefunded" amount in `reserveAmount`,

so `defaultRecoveryPrice = reserveAmount / totalBasis` is computed on underlying that no longer exists in the contract.

The normal claim path `claimWithdrawRequest → _claimDefaultedWithdrawRequest` and instant path `_claimDefaultedInstantWithdrawRequest` (`:842-856`) then pay `claimBasis * defaultRecoveryPrice` from `defaultRecoveryReserve`/strategy balance. Because the numerator counted money already withdrawn, the reserve is overstated; the last claimant(s) hit `underlyingToken.safeTransfer`/`_transferDefaultRecovery` with insufficient balance and their transactions revert.

Notably, the attacker's *own* stale entry cannot be re-claimed: `_claimDefaultedInstantWithdrawRequest` would underflow `instantWithdrawsRequests[_user] -= claimBasis` since it is already 0 — but that revert does not protect the inflated `instantWithdrawClaimsByEpoch`/`defaultPendingClaimBasis` values that were baked into `recoveryPrice` at finalization. No existing guard (skim, `Default` reverts, `_onlyIdleCDO`, `defaultInstantWithdrawsFinalized`) prevents this; the fix in `requestWithdraw` that forces users to claim old loss-adjusted receipts before re-requesting (`:263-271`) covers only normal receipts, not the instant ledger.

### Impact Explanation
When an epoch partially funds instant withdrawals and then defaults:
1. `defaultRecoveryPrice` is set using a basis that includes ghost (already-paid) instant receipts and a reserve that counts funds already transferred out.
2. Honest pending withdraw requesters, unfunded instant requesters, and active AA/BB LPs receive claims priced against a reserve that does not exist; the recovery reserve is drained early and the final claimants' `claimWithdrawRequest`/`claimInstantWithdrawRequest`/defaulted claims revert permanently on ERC20 insufficient-balance.
3. Quantified loss: up to `alreadyPaidInstant / totalBasis` of the recovery reserve is stranded — i.e., recovery funds equal to the amount of same-epoch instant receipts that were claimed before the default are permanently frozen in the strategy, and the equivalent value is stolen early by whoever claims first (last-come claimants bear the full shortfall). This satisfies "permanent freezing of funds" / "insolvency" with a quantified loss.

### Likelihood Explanation
- Attacker requirement is minimal: any KYC-passed tranche holder can call `requestWithdraw`/`claimInstantWithdrawRequest` in a running epoch where instant withdrawals are enabled and funded.
- Trigger condition is realistic, not exotic: `startEpoch`/`getInstantWithdrawFunds` explicitly support *partial* instant funding (`collectInstantWithdrawFunds` takes `min(pendingInstant, totUnderlyings)`, `IdleCDOEpochVariant.sol:282`), and the code comments at `IdleCreditVault.sol:637-640` acknowledge partial prefunding. A borrower default at the next `stopEpoch` in the same epoch number finalizes with the polluted ledger.
- The user does not even need malicious intent — a normal instant-withdraw claim creates the ghost basis. A deliberate attacker can additionally time a large instant request + claim before an anticipated default to maximize the stranded share.
- The stale ledger entries are never cleaned in any other code path, so the corruption is deterministic whenever the precondition (same-epoch partial instant funding → default) occurs.

### Recommendation
In `claimInstantWithdrawRequest` (and symmetrically in `IdleCDOEpochQueue.processWithdrawalClaims` users' path), clear the per-epoch receipt records when the claim is paid:

```solidity
function claimInstantWithdrawRequest(address _user) external {
    _onlyIdleCDO();
    if (defaultRecoveryFinalized && defaultInstantWithdrawsFinalized) {
        _claimDefaultedInstantWithdrawRequest(_user);
    }
    uint256 amount = instantWithdrawsRequests[_user];
    _burn(_user, amount);
    instantWithdrawsRequests[_user] = 0;
    // clear stale per-epoch basis for every epoch the user was paid for
    // (iterate stored request epochs or track the current one explicitly)
    ...
    _transferFundedClaim(_user, amount);
}
```
Concretely: on a successful funded claim, decrement `instantWithdrawClaimsByEpoch[reqEpoch]` and zero `instantWithdrawsRequestsByEpoch[_user][reqEpoch]` for each funded epoch (this requires tracking funded request epochs, or segregating funded vs. unfunded instant basis at `collectInstantWithdrawFunds` time). Alternatively, maintain a separate `fundedInstantClaimsByEpoch` counter so `defaultPendingClaimBasis` and `_defaultPrefundedInstantReserve` only count receipts that are still backed by strategy-held underlying.

### Proof of Concept
Foundry fork test (against the existing harness in `test/foundry/IdleCreditVault.t.sol`):

```solidity
function testPartialInstantThenDefault_InflatedRecoveryBasis() external {
    // fee config, AYS off, instant withdraws enabled (delay + aprDelta)
    _setFeeParams(TL_MULTISIG, 0, FULL_ALLOC, cdoEpoch.managementFee());
    vm.prank(manager);
    cdoEpoch.setInstantWithdrawParams(cdoEpoch.instantWithdrawDelay(), 1000, false);

    address userA = makeAddr("instantA");
    address userB = makeAddr("instantB");
    uint256 dep = 10_000 * ONE_SCALE;
    idleCDO.depositAA(dep);
    _depositWithUser(userA, dep, true);
    _depositWithUser(userB, dep, true);

    _startEpochAndCheckPrices(0);           // epoch N running
    vm.warp(block.timestamp + cdoEpoch.instantWithdrawDelay() + 1);

    // A and B both request instant withdrawals
    uint256 reqA;
    vm.prank(userA);
    reqA = cdoEpoch.requestWithdraw(0, address(AAtranche));
    uint256 reqB;
    vm.prank(userB);
    reqB = cdoEpoch.requestWithdraw(0, address(AAtranche));

    // fund only enough for A: CDO cash covers reqA only
    vm.prank(manager);
    cdoEpoch.getInstantWithdrawFunds();     // pendingInstantWithdraws = reqB

    // A claims successfully -> paid out, but instantWithdrawsRequestsByEpoch[A][N]
    // and instantWithdrawClaimsByEpoch[N] still contain reqA
    vm.prank(userA);
    cdoEpoch.claimInstantWithdrawRequest();
    assertEq(
        IdleCreditVault(address(strategy)).instantWithdrawsRequestsByEpoch(userA, 0),
        reqA, "stale per-epoch instant basis remains"
    );

    // epoch N ends; borrower cannot repay -> stopEpoch defaults, same epochNumber
    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.prank(manager);
    cdoEpoch.stopEpoch(0, 0);               // _handleBorrowerDefault path

    // finalize recovery with a partial recovery amount R
    uint256 recovered = 5_000 * ONE_SCALE;
    deal(defaultUnderlying, borrower, recovered);
    vm.prank(borrower);
    underlying.approve(address(strategy), recovered);
    vm.prank(manager);
    cdoEpoch.finalizeDefaultRecovery(recovered); // or the CDO-side finalizer

    // defaultPendingClaimBasis counted pendingWithdraws + instantWithdrawClaimsByEpoch[N]
    // where the instant term still includes reqA (ghost). defaultRecoveryReserve
    // credited a "prefundedReserve" = reqA + reqB - pendingInstant that includes
    // reqA's already-paid funds.

    // B's defaulted instant claim and other users' normal claims succeed until
    // the reserve is drained; the LAST claimant reverts on ERC20 insufficient
    // balance even though sum(claims) <= defaultRecoveryReserve:
    vm.prank(userB);
    cdoEpoch.claimInstantWithdrawRequest(); // may succeed (drains reserve)
    vm.prank(address(this));
    vm.expectRevert();                       // ghost basis strands recovery
    cdoEpoch.claimWithdrawRequest();
}
```

Expected on a fork: `instantWithdrawsRequestsByEpoch(userA, epoch)` remains non-zero after A's successful claim; `defaultPendingClaimBasis()` exceeds the real outstanding basis by `reqA`; after finalization the aggregate claims priced by `defaultRecoveryPrice` exceed the underlying actually held, so the final claimant's transfer reverts and `recoveryPrice * ghostBasis / 1e18` underlying is permanently stranded in `IdleCreditVault`.