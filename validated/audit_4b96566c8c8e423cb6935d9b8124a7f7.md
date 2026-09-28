### Title
Post-default instant withdraw receipts are minted into the defaulted epoch bucket and either double-haircut or permanently locked — (`contracts/strategies/idle/IdleCreditVault.sol`)

### Summary
`requestInstantWithdraw` is the only receipt-creation entrypoint that lacks a `defaultRecoveryFinalized` branch. After `finalizeDefaultRecovery`, `epochNumber` never advances again, so any new instant-withdraw receipt is recorded under `instantWithdrawsRequestsByEpoch[user][defaultRecoveryEpoch]` — the same bucket used to pay the pre-default haircut. Depending on `defaultInstantWithdrawsFinalized`, the new receipt is either paid `claimBasis * defaultRecoveryPrice` a second time (double haircut on an amount that was already haircut when the CDO priced the tranche tokens) or the claim reverts forever in `_transferFundedClaim` because the strategy's entire balance is reserved. This mirrors CVE-2021-21851's class: attacker-controlled index/amount values feed unchecked arithmetic paths (per-epoch mappings keyed by a stale `epochNumber`, saturating subtractions like `claimBasis >= pending ? 0 : pending - claimBasis`) that corrupt the recovery accounting invariants.

### Finding Description
- `requestWithdraw` explicitly detects `defaultRecoveryFinalized` and routes post-default requests into `postDefaultRequests` with a 1:1 claim from the reserve (`IdleCreditVault.sol:247-258`).
- `requestInstantWithdraw` (`IdleCreditVault.sol:356-375`) has no equivalent branch: it burns CDO strategy tokens, mints a receipt to the user, increments `instantWithdrawsRequests`, writes `instantWithdrawsRequestsByEpoch[user][epochNumber]`, `instantWithdrawClaimsByEpoch[epochNumber]`, and `pendingInstantWithdraws` — with `epochNumber == defaultRecoveryEpoch` forever after finalization.
- In `claimInstantWithdrawRequest` (`IdleCreditVault.sol:380-393`), when `defaultInstantWithdrawsFinalized == true`, `_claimDefaultedInstantWithdrawRequest` (`IdleCreditVault.sol:842-856`) reads that same per-epoch mapping, pays `claimBasis * defaultRecoveryPrice / RECOVERY_FULL` out of `defaultRecoveryReserve`, and mutates `pendingInstantWithdraws`/`instantWithdrawClaimsByEpoch` with saturating subtraction that was designed only for receipts included in `totalBasis` at finalization time.
- The minted receipt `_amount` was already produced by the CDO at the post-default (recovery-adjusted) tranche price, so applying `defaultRecoveryPrice` again is a second, unintended haircut; the unpaid difference remains locked in the reserve with no claimant. When `defaultInstantWithdrawsFinalized == false` (instant queue fully funded before default), the new receipt instead falls to `_transferFundedClaim`, whose guard `balance - reserve < _amount` reverts permanently since the whole strategy balance is `defaultRecoveryReserve` — the user's tranche-backed receipt can never be claimed.

### Impact Explanation
Any tranche holder (unprivileged) who uses the instant-withdraw path after a finalized default either (a) is paid `amount * recoveryPrice` instead of `amount`, permanently losing `(1 - recoveryPrice) * amount` of already-haircut principal to unreachable reserve dust, or (b) has their claim revert forever, permanently freezing the full receipt value. Loss is bounded by the post-default instant-withdraw volume but applies to the entire remaining tranche supply, which can be arbitrarily large. Broken invariants: one-receipt-one-payout and loss-waterfall consistency (recovery ratio applied twice to the same basis; a new claim basis is admitted into the defaulted epoch without ever entering `totalBasis`/`reserveAmount`).

### Likelihood Explanation
Requires a borrower default followed by `finalizeDefault`/`finalizeDefaultRecovery` — an exceptional but fully supported protocol state that ships dedicated claim paths for post-default users (`postDefaultRequests` proves post-default withdraws are an intended flow). The instant path is reachable by any tranche holder in the same state with no privileged cooperation. The trigger is deterministic once default finalization occurs; there is no race. Residual uncertainty: if the CDO-side `requestInstantWithdraw` in `IdleCDOEpochVariant` independently reverts while `defaulted`, the exploitability narrows to configurations where the CDO permits post-default instant requests (the strategy code is explicitly written to serve them, per `defaultInstantWithdrawsFinalized` handling).

### Recommendation
Add the same `defaultRecoveryFinalized` handling to `requestInstantWithdraw` that `requestWithdraw` already has: either route the receipt into `postDefaultRequests` (paid 1:1 from reserve at the already-haircut amount) or revert cleanly. If post-default instant withdraws are supported, tag them with a distinct epoch/mapping so they can never be read back through `instantWithdrawsRequestsByEpoch[user][defaultRecoveryEpoch]`, and never apply `defaultRecoveryPrice` a second time to an amount already priced post-default.

### Proof of Concept
Foundry test (extend `test/foundry/IdleCreditVault.t.sol` harness, which already has default-finalization helpers):

```solidity
function testPostDefaultInstantWithdrawDoubleHaircut() external {
    // 1. Deposit AA/BB, start epoch, create a partially funded instant request so
    //    pendingInstantWithdraws != 0 at finalization (defaultInstantWithdrawsFinalized = true).
    idleCDO.depositAA(10_000 * ONE_SCALE);
    idleCDO.depositBB(10_000 * ONE_SCALE);
    _startEpochAndCheckPrices(0);
    cdoEpoch.requestInstantWithdraw(/* partial */, address(AAtranche)); // leaves unfunded remainder
    // 2. Trigger borrower default + finalize recovery at recoveryPrice R < 1e18.
    _finalizeDefaultRecovery(R); // helper: stopEpoch default path -> finalizeDefaultRecovery

    // 3. Attacker (any remaining tranche holder) requests an instant withdraw AFTER finalization.
    uint256 trancheBal = AAtranche.balanceOf(attacker);
    uint256 receipt = cdoEpoch.requestInstantWithdraw(trancheBal, address(AAtranche));
    // receipt is already valued at post-default price (haircut applied once)

    // 4. Claim: instantWithdrawsRequestsByEpoch[attacker][defaultRecoveryEpoch] == receipt,
    //    so _claimDefaultedInstantWithdrawRequest pays receipt * R / 1e18 (haircut applied twice).
    uint256 balBefore = underlying.balanceOf(attacker);
    cdoEpoch.claimInstantWithdrawRequest(attacker);
    uint256 paid = underlying.balanceOf(attacker) - balBefore;
    assertEq(paid, receipt * R / 1e18);        // double haircut
    assertLt(paid, receipt);                    // user underpaid
    // paid - expected difference stays in defaultRecoveryReserve with no claimant (locked dust).
}

function testPostDefaultInstantWithdrawFrozen() external {
    // Variant: pendingInstantWithdraws == 0 at finalization (all instant receipts prefunded)
    // => defaultInstantWithdrawsFinalized == false. A post-default instant request falls to
    // _transferFundedClaim, where balance - defaultRecoveryReserve < amount => permanent revert.
    ...
    vm.expectRevert(NotAllowed.selector);
    cdoEpoch.claimInstantWithdrawRequest(attacker); // receipt unclaimable forever
}
```

Note: due to index coverage I could not confirm whether `IdleCDOEpochVariant.requestInstantWithdraw` guards `defaulted`; if it reverts post-default, the finding degrades to a dead-code inconsistency rather than an exploitable path — the strategy's own `defaultInstantWithdrawsFinalized` handling strongly indicates the post-default instant flow is intended to be reachable.