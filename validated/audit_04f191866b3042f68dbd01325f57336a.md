### Title
Post-default withdraw requests drain `defaultRecoveryReserve` that was sized only for default-epoch claims, freezing later recovery payouts - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`finalizeDefaultRecovery` sizes `defaultRecoveryReserve` to cover only the claim basis outstanding at finalization (active NAV + `pendingWithdraws` + unfunded instant receipts). After finalization, `requestWithdraw` creates new `postDefaultRequests` receipts without adding any underlying to the reserve, yet `_claimPostDefaultWithdrawRequest` pays them 1:1 out of `defaultRecoveryReserve` via `_transferDefaultRecovery`. Each post-default claim shrinks the reserve below what the remaining default-epoch claimants are owed, so the last claimants' `_transferDefaultRecovery` underflows/reverts and their recovery is permanently frozen.

### Finding Description
At `contracts/strategies/idle/IdleCreditVault.sol:686-693` the reserve is set once:

```solidity
uint256 reserveAmount = _recoveredAmount + prefundedReserve + defaultRecoveryReserve;
uint256 recoveryPrice = reserveAmount * RECOVERY_FULL / totalBasis;
defaultRecoveryReserve = reserveAmount;
```

`totalBasis = activeBasis + pendingBasis` covers only positions existing at finalization. Invariant: sum of all future claims paid from the reserve = `defaultedBasis * recoveryPrice`, and active holders' share is claimed by redeeming tranches through the CDO, not via `_transferDefaultRecovery`.

After finalization, `requestWithdraw` (lines 247-257) mints a new receipt and records `postDefaultRequests[_user] = _amount` with no corresponding increase of `defaultRecoveryReserve` or restriction on the reserve it can consume:

```solidity
_burn(msg.sender, _amount);
_mint(_user, _amount);
postDefaultRequests[_user] = _amount;
```

`_claimPostDefaultWithdrawRequest` (lines 760-767) then pays `amount` 1:1 through `_transferDefaultRecovery`, which does `defaultRecoveryReserve -= _amount` (line 915). The haircut applied to the post-default request is a `virtualPrice` haircut, not a `defaultRecoveryPrice` haircut, and — critically — the reserve was never topped up for these claims. So `Σ(defaulted claims) + Σ(post-default claims) > defaultRecoveryReserve`: the last `defaultRecoveryPrice`-priced claimants hit an underflow revert in `_transferDefaultRecovery` and can never claim.

### Impact Explanation
Direct theft / permanent freezing of recovery funds. Post-default withdrawers who race to claim consume recovery reserve earmarked for defaulted-epoch receipt holders; the claim of the last claimant(s) reverts on `defaultRecoveryReserve -= _amount`, permanently freezing their recovery. Loss magnitude equals the total post-default withdrawal volume processed before honest default-epoch claimants act — potentially the entire residual reserve.

### Likelihood Explanation
- Attacker is any unprivileged tranche holder; post-default `requestWithdraw`/`claimWithdrawRequest` only require going through the honest `idleCDO` (`_onlyIdleCDO`), and no privileged actor is malicious.
- Triggering requires only a finalized default (an honest `finalizeDefaultRecovery` by the CDO) followed by ordinary post-default withdraw requests — normal user behavior.
- `postDefaultRequests` claims are explicitly designed to be paid 1:1 and to consume the reserve (`_transferDefaultRecovery`), so the deficit is deterministic, not rounding-dependent.
- The `_transferFundedClaim` reserve guard (lines 899-905) does not apply: post-default claims bypass it and draw directly on the reserve.

### Recommendation
Either fund post-default receipts separately (transfer the backing underlyings into the strategy outside `defaultRecoveryReserve` and pay them via `_transferFundedClaim`-style accounting), or add each post-default request amount to `defaultRecoveryReserve` at request time with the reserve balance increased accordingly. Alternatively, price post-default receipts against `defaultRecoveryPrice` and track a separate `postDefaultReserve` bucket so default-epoch claimants' aggregate entitlement `pendingBasis * defaultRecoveryPrice` is never reduced by later claims.

### Proof of Concept
Foundry fork PoC outline (repository already ships `test/foundry/IdleCreditVault.t.sol` with helpers `_depositWithUser`, `_startEpochAndCheckPrices`, `deal`):

```solidity
function testPostDefaultClaimDrainsRecoveryReserve() external {
    uint256 amount = 10_000 * ONE_SCALE;
    // 1. Deposit AA for userA and userB, run one epoch.
    idleCDO.depositAA(amount);
    _depositWithUser(userB, amount, true);
    _startEpochAndCheckPrices(0);

    // 2. userA requests a normal withdraw (enters pendingWithdraws basis).
    uint256 reqA = cdoEpoch.requestWithdraw(
        IERC20(AAtranche).balanceOf(address(this)) / 2, address(AAtranche));

    // 3. Borrower defaults; CDO calls _handleBorrowerDefault then
    //    strategy.finalizeDefaultRecovery(partialRecovery, source) with
    //    recoveryPrice < 1e18. defaultRecoveryReserve = recovered funds only.

    // 4. userB requests a withdraw AFTER finalization:
    //    requestWithdraw stores postDefaultRequests[userB] with no reserve top-up.
    vm.prank(userB);
    cdoEpoch.requestWithdraw(IERC20(AAtranche).balanceOf(userB), address(AAtranche));
    vm.prank(userB);
    cdoEpoch.claimWithdrawRequest(); // pays 1:1, decrements defaultRecoveryReserve

    // 5. userA (and remaining active claimants) now claim their default-epoch
    //    receipt at defaultRecoveryPrice. Assert the last claim reverts /
    //    pays less than claimBasis * defaultRecoveryPrice / 1e18 because
    //    defaultRecoveryReserve was consumed by userB's post-default claim.
    vm.expectRevert(); // underflow in _transferDefaultRecovery
    cdoEpoch.claimWithdrawRequest();
}
```

Note: the exact default-finalization entry points in `IdleCDOEpochVariant` (`_handleBorrowerDefault`/`finalizeDefault`) were not fully traced within this session, so the PoC wiring at step 3 should be confirmed against those functions; the reserve-accounting mismatch in `IdleCreditVault` (reserve sized at finalization, consumed by un-sized post-default claims) is directly verifiable from the code cited above.