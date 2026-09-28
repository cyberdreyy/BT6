### Title
`epochNumber` is only incremented inside `deposit()`, so a `stopEpoch` that mints interest via `mintStrategyTokens` (or otherwise skips `deposit`) leaves the epoch counter stale, permanently freezing pending withdraw claims - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
The audit-report bug class is "a monotonic counter that is supposed to advance but doesn't, breaking nonce/id uniqueness." The analog in idle-tranches is `IdleCreditVault.epochNumber`. It is the de-facto nonce that separates withdraw-request epochs (`lastWithdrawRequest`, `withdrawsRequestsByEpoch`, `apr0RateByEpoch`, `instantWithdrawClaimsByEpoch`, `lossRecoveryPriceByEpoch` are all keyed by it), but it is advanced only inside `deposit()` when `isEpochRunning()` is true (`IdleCreditVault.sol:607-614`), not in the epoch state machine itself. Any `stopEpoch` path that does not route through `deposit()` — in particular minted-interest mode, which uses `mintStrategyTokens` — leaves `epochNumber` unchanged, so the "wait one epoch" gate never opens.

### Finding Description
`requestWithdraw` stamps each request with the current counter: `lastWithdrawRequest[_user] = currentEpoch` (`IdleCreditVault.sol:282`) and `withdrawsRequestsByEpoch[_user][currentEpoch]` (`:293`). The claim gate requires the counter to have moved past the request epoch:

```solidity
if (IIdleCDOEpochVariant(idleCDO).epochEndDate() != 0 && (epochNumber <= lastWithdrawRequest[_user])) {
  revert NotAllowed();
}
```
`IdleCreditVault.sol:326-328`

The code comment asserts "Epoch number is increased at stopEpoch" (`:322`), but the only increment is:

```solidity
if (IIdleCDOEpochVariant(idleCDO).isEpochRunning()) {
  totEpochDeposits = 0;
  epochNumber += 1;
}
```
inside `deposit()` (`IdleCreditVault.sol:607-610`). In minted-interest mode (`isInterestMinted()`), the interest path calls `mintStrategyTokens` (`:621-624`), which does not touch `epochNumber`. If a full epoch cycle completes with the only strategy-credit call being `mintStrategyTokens` (or a `stopEpochWithDuration`/loss path where the borrower repays the CDO directly and no `deposit` executes while `isEpochRunning()`), `epochNumber` stays at the request epoch.

Consequences of the stale counter:

- `_claimFundedWithdrawRequest` reverts forever for every user whose `lastWithdrawRequest == epochNumber` while `epochEndDate != 0` — their receipt tokens were already minted/burned-accounted at request time (`requestWithdraw` burns the CDO's strategy tokens at `:273`), so the underlying principal is locked in the vault/strategy with no claim path: permanent freezing of user funds.
- `_settleApr0` requires `_reqEpoch < epochNumber` (`:553`); a stalled counter means APR0 principal never settles and `apr0RateByEpoch` entries from a subsequent successful stop overwrite the same epoch key (`:537`), so earlier APR0 interest is lost or misattributed.
- `lossRecoveryPriceByEpoch[epochNumber]` (`:421`) and `instantWithdrawClaimsByEpoch[epochNumber]` (`:372`) collapse two distinct epochs into one key if the counter is later incremented only once, mixing haircut and default-accounting bases.

### Impact Explanation
An unprivileged lender who calls `requestWithdraw` during an epoch that ends without a `deposit()`-triggered increment has their burned-strategy-token receipt permanently unclaimable: `claimWithdrawRequest` always reverts `NotAllowed` because `epochNumber` can never exceed `lastWithdrawRequest`. Loss equals the user's full requested amount (`withdrawsRequests[_user]`), i.e. permanent freezing/direct loss of funds, not merely DoS — the tokens were burned at request time.

### Likelihood Explanation
The trigger requires no attacker action beyond ordinary usage: it depends only on the honest manager/borrower stopping an epoch through a path that does not call `strategy.deposit` while `isEpochRunning()` — minted-interest epochs (`isInterestMinted`) being the clearest case, since interest accrual there uses `mintStrategyTokens` and a mid-epoch `deposit` is optional. Any user requesting withdrawal in such an epoch is affected deterministically. I could not fully trace `IdleCDOEpochVariant.stopEpoch` to confirm whether `deposit` is unconditionally invoked in every mode; if it is, this degrades to a latent fragility rather than an exploitable path, and the finding should be treated as invalid.

### Recommendation
Increment `epochNumber` in the epoch transition itself rather than inferring it from `deposit()`. E.g., expose a `_onlyIdleCDO` `bumpEpoch()` called from `stopEpoch`/`stopEpochWithDuration`/`startEpoch`, or move the `epochNumber += 1` and `totEpochDeposits = 0` reset into a dedicated function invoked unconditionally by the CDO at epoch end. The claim gate and all `*ByEpoch` mappings should not depend on an incidental side effect of the interest/deposit path.

### Proof of Concept
Foundry fork PoC sketch (mode: minted interest, running epoch):

```solidity
// Setup: cdoEpoch.setIsInterestMinted(true); deposits, startEpoch.
uint256 e0 = IdleCreditVault(address(strategy)).epochNumber();

// User requests withdraw during epoch e0
vm.prank(user);
idleCDO.requestWithdraw(amount); // -> strategy.requestWithdraw: lastWithdrawRequest[user] = e0

// Epoch ends; stopEpoch mints interest via mintStrategyTokens only,
// no strategy.deposit while isEpochRunning() == true.
vm.warp(cdoEpoch.epochEndDate() + 1);
vm.prank(manager);
cdoEpoch.stopEpoch(0, interestOverride);

assertEq(IdleCreditVault(address(strategy)).epochNumber(), e0, "epochNumber not incremented");

// Next epoch starts; user tries to claim -> still e0, epochEndDate != 0
vm.prank(manager);
cdoEpoch.startEpoch(...);
vm.expectRevert(); // NotAllowed
idleCDO.claimWithdrawRequest(user);
```