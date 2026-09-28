### Title
Instant-withdraw claims pay the unfunded aggregate receipt, draining other claimants' funded reserves - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`claimInstantWithdrawRequest` burns and pays out a user's full `instantWithdrawsRequests[_user]` balance even though only part of the instant-withdraw queue may have been funded by the CDO via `collectInstantWithdrawFunds`. A lender whose instant request was only partially funded can claim 100% of it, consuming underlying that belongs to other instant-withdraw claimants (or that was never collected at all), a direct analog of CVE-2016-6288's "read past the funded boundary" bug class: the claim path reads the aggregate request counter instead of the funded bound.

### Finding Description
`requestInstantWithdraw` (contracts/strategies/idle/IdleCreditVault.sol:356-375) credits the user the full `_amount`: it increments `instantWithdrawsRequests[_user]`, `instantWithdrawsRequestsByEpoch`, `instantWithdrawClaimsByEpoch` and `pendingInstantWithdraws`. Funding is decoupled: `collectInstantWithdrawFunds` (lines 398-403) only decrements the global `pendingInstantWithdraws` and pulls whatever amount the CDO actually holds — the code comments in `defaultPendingClaimBasis` (lines 637-640) explicitly acknowledge "that cash covered only part of the instant queue", i.e. partial funding is an expected state.

`claimInstantWithdrawRequest` (lines 380-393) then does:

```
uint256 amount = instantWithdrawsRequests[_user];   // full aggregate, incl. unfunded remainder
_burn(_user, amount);
instantWithdrawsRequests[_user] = 0;
_transferFundedClaim(_user, amount);                // pays from strategy balance
```

There is no check that the claimed amount was actually funded (no per-user/per-epoch funded bound, no comparison against `pendingInstantWithdraws` or the user's pro-rata share of the collected amount), unlike the normal withdraw path which gates claims by epoch maturity (`epochNumber <= lastWithdrawRequest`, line 326) and by per-epoch loss haircuts (`lossRecoveryPriceByEpoch`). `_transferFundedClaim` (lines 897-907) only protects `defaultRecoveryReserve`, not other claimants' funded cash.

Attack path, unprivileged KYC'd lender as attacker:

1. Buffer/epoch phase: attacker calls `requestInstantWithdraw` for amount A; another honest user requests B. The CDO's `collectInstantWithdrawFunds` only collects A (partial funding), leaving `pendingInstantWithdraws = B` unfunded — a state the code itself anticipates.
2. The attacker immediately calls `claimInstantWithdrawRequest`. `instantWithdrawsRequests[attacker] = A` is paid in full from the strategy's collected cash — correct so far, but the receipt for a *partially* funded epoch pays first-come-first-served at 100%.
3. Worse, if the attacker's own request was only partially funded in epoch N and they make a second instant request in epoch N+1 that is fully funded, `instantWithdrawsRequests[attacker]` aggregates both; the claim burns the whole aggregate and `_transferFundedClaim` pays out the sum, including the never-funded remainder from epoch N, paid out of cash collected for other users' claims.

Broken invariant: "one receipt, one funded payout" — the claim reads aggregate request state rather than the funded bound, i.e. it pays beyond the data (funding) that was actually recorded, the same defect shape as the PHP over-read.

### Impact Explanation
Direct theft / insolvency: an unprivileged lender receives underlying in excess of what was funded for their receipts. The excess comes from cash collected for other instant-withdraw claimants, who are then unable to claim (strategy balance drained → their `safeTransfer` reverts → permanent freezing of their claims). Loss is bounded by the unfunded remainder the attacker can sandwich into their aggregate, i.e. up to the full size of a partially funded instant queue.

### Likelihood Explanation
Requires the instant-withdraw queue to be partially funded, which occurs whenever the CDO's available cash (or borrower-send failure at `startEpoch`) covers only part of `pendingInstantWithdraws` — the code explicitly models this case. The attacker needs only to be a KYC-passing tranche holder; no privileged role is involved and the default-recovery guards (`defaultRecoveryFinalized` path, reserve isolation in `_transferFundedClaim`) do not apply to the pre-default funded-claim path.

### Recommendation
Track the funded portion of the instant queue and bound claims by it, e.g. record `instantWithdrawsFunded` alongside `pendingInstantWithdraws` and either (a) reject claims while a user's epoch-N receipts are unfunded, or (b) store a per-epoch instant recovery price (`instantFundedPriceByEpoch[epoch] = collected / instantWithdrawClaimsByEpoch[epoch]`) and pay `receipt * price` in `claimInstantWithdrawRequest`, mirroring the existing `lossRecoveryPriceByEpoch` mechanism already used for normal withdraws.

### Proof of Concept
```solidity
// Foundry fork test sketch against IdleCreditVault / IdleCDOEpochVariant
function testInstantClaimOverpaysUnfundedAggregate() external {
    // attacker and honest user deposit and hold AA tranches (KYC'd)
    uint256 amount = 10_000 * ONE_SCALE;
    _depositWithUser(attacker, amount, true);
    _depositWithUser(honest, amount, true);

    // epoch N running; CDO has only `amount` cash for instant withdrawals
    _startEpochAndCheckPrices(0);

    // attacker requests instant withdraw of `amount`
    vm.prank(attacker);
    cdoEpoch.requestInstantWithdraw(trancheAmountAttacker, address(AAtranche));
    // honest user requests instant withdraw of `amount` too
    vm.prank(honest);
    cdoEpoch.requestInstantWithdraw(trancheAmountHonest, address(AAtranche));

    // CDO only collects `amount` -> pendingInstantWithdraws = amount (unfunded remainder)
    // honest user's receipt is unfunded at this point

    // attacker claims -> burns full aggregate and receives `amount` at par
    uint256 balPre = underlying.balanceOf(attacker);
    vm.prank(attacker);
    cdoEpoch.claimInstantWithdrawRequest();
    assertEq(underlying.balanceOf(attacker) - balPre, amount, 'paid at par despite partial funding');

    // next epoch: attacker requests again (funded), then claims the aggregate again,
    // sweeping cash meant for honest's still-unclaimed receipt -> honest's claim reverts
    _stopCurrentEpoch();
    _startEpochAndCheckPrices(1);
    vm.prank(attacker);
    cdoEpoch.requestInstantWithdraw(newAmount, address(AAtranche));
    vm.prank(attacker);
    cdoEpoch.claimInstantWithdrawRequest(); // pays newAmount + never-funded remainder
    vm.prank(honest);
    vm.expectRevert(); // ERC20 transfer: balance insufficient -> claim permanently frozen
    cdoEpoch.claimInstantWithdrawRequest();
}
```

Note: the exact CDO-side partial-funding entry point (`getInstantWithdrawFunds` in `IdleCDOEpochVariant`) could not be fully traced in this session; the PoC assumes — consistent with the comments at `IdleCreditVault.sol:637-640` — that `collectInstantWithdrawFunds` can leave `pendingInstantWithdraws > 0` while receipts remain claimable at par. If the CDO reverts instead of partially collecting, the aggregate over-claim across epochs N and N+1 still applies whenever any prior unfunded remainder exists.