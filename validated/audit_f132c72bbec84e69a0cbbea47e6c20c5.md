### Title
Post-default withdraw receipts are paid 1:1 from `defaultRecoveryReserve`, draining the recovery reserve and leaving defaulted-epoch claimants unpayable - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
After a borrower default is finalized, `IdleCreditVault.requestWithdraw` takes an early path that mints the user a receipt without adding to `pendingWithdraws` and without any new underlying entering the vault. The matching claim path `_claimPostDefaultWithdrawRequest` pays that receipt **1:1 out of `defaultRecoveryReserve`** — the same finite reserve that was sized at finalization to cover only the pre-default claims multiplied by `defaultRecoveryPrice`. Any post-default claimant therefore consumes reserve earmarked for defaulted-epoch receipt holders, and the last honest claimants revert on transfer / get nothing.

### Finding Description
At `requestWithdraw` (contracts/strategies/idle/IdleCreditVault.sol:243-258), once `defaultRecoveryFinalized` is true the strategy burns `_amount` strategy tokens from the CDO, mints `_amount` receipt tokens to the user, records `postDefaultRequests[_user] = _amount`, and returns. No underlying is transferred in, `pendingWithdraws` is not increased, and `defaultRecoveryReserve` is not topped up.

At claim time, `claimWithdrawRequest` (lines 301-314) first routes to `_claimPostDefaultWithdrawRequest` (lines 760-767), which does:

```solidity
_burn(_user, amount);
_transferDefaultRecovery(_user, amount);   // pays amount 1:1, decrements defaultRecoveryReserve
```

The reserve was set in `finalizeDefaultRecovery` (lines 686-693) as `reserveAmount = _recoveredAmount + prefundedReserve + defaultRecoveryReserve`, and it exactly satisfies the equation `Σ(claimBasis_i) * defaultRecoveryPrice / RECOVERY_FULL == reserveAmount` over the defaulted claim basis. Every post-default claim pays `amount` at par — i.e., at rate `1e18` rather than `defaultRecoveryPrice` — so each unit claimed post-default consumes `1/defaultRecoveryPrice` times what a defaulted claim of the same basis would consume. Since no funding source backs post-default receipts, the reserve is exhausted before all defaulted claims are paid, and `_transferDefaultRecovery` (lines 912-917) either underflows `defaultRecoveryReserve -= _amount` or transfers fewer funds than owed to later claimants.

Additionally the economics are wrong even for the paying user: a defaulted receipt is worth `claimBasis * defaultRecoveryPrice`, but the post-default path pays the haircut-valued `_amount` at par, so an attacker who still holds tranche tokens converts them into more underlying than the recovery ratio entitles anyone to, purely at the expense of other claimants.

### Impact Explanation
Direct theft / permanent freezing of unclaimed yield (recovery funds). After finalization, any user still holding tranche tokens (AA or BB) can call `IdleCDOEpochVariant.requestWithdraw` → `IdleCreditVault.requestWithdraw` → `claimWithdrawRequest` and withdraw underlying at par from the recovery reserve. Quantified loss: up to the entire `defaultRecoveryReserve` can be siphoned before defaulted-epoch receipt holders claim; e.g., with `defaultRecoveryPrice = 0.5e18` and a reserve of 500k backing 1M of defaulted basis, an attacker requesting a 100k post-default claim removes 100k of reserve, leaving at least one honest defaulted claimant permanently unable to claim ~100k·price of recovery. Funds are effectively stolen, not merely delayed — the reserve accounting `defaultRecoveryReserve -= _amount` makes the shortfall permanent.

### Likelihood Explanation
High once a default is finalized, which is the exact state this code path exists for. Requirements for the attacker are minimal: hold any tranche tokens after default (no need to have had a pending request), and pass `isWalletAllowed` (KYC'd lender — an explicitly in-scope attacker). `requestWithdraw` only checks `allowAAWithdrawRequest`/`allowBBWithdrawRequest` flags and `isWalletAllowed`; the post-default branch in the strategy does not re-check epoch state. No privileged action is needed — `finalizeDefault` is called by the honest manager per the normal recovery flow. The attacker simply front-runs honest defaulted claimants, which is trivially sequenceable in the same or subsequent blocks.

### Recommendation
Back post-default receipts with real funds or stop paying them from the recovery reserve:
- Either revert `requestWithdraw` when `defaultRecoveryFinalized` (no post-default withdraw queue), or
- Require the CDO to actually source underlying for post-default receipts (e.g., increment `pendingWithdraws` so a subsequent borrower repayment funds them via `collectWithdrawFunds`), and pay them via `_transferFundedClaim` instead of `_transferDefaultRecovery`, and
- Never let `_claimPostDefaultWithdrawRequest` decrement `defaultRecoveryReserve` for amounts that were not part of the finalized claim basis.

### Proof of Concept
Foundry fork PoC sketch (same harness style as `test/foundry/IdleCreditVault.t.sol`):

```solidity
// setup: depositAA for attacker and victim, run epoch, borrower defaults
_depositWithUser(attacker, amount, true);   // attacker holds tranches, NO withdraw request
_depositWithUser(victim,   amount, true);
vm.prank(victim);
cdoEpoch.requestWithdraw(0, address(AAtranche));  // victim has defaulted-epoch receipt

_startEpochAndCheckPrices(0);
_stopEpochAndCheckPrices(0, apr, 0);              // borrower repays 0 -> default
_checkDefault();

uint256 recovered = basis * recoveryRatio / 1e18; // e.g. 50%
deal(underlying, manager, recovered);
vm.startPrank(manager);
IERC20(underlying).approve(strategy, recovered);
cdoEpoch.finalizeDefault(recovered, manager);     // honest manager, reserve = recovered
vm.stopPrank();

// ATTACK: attacker (still holding tranches) requests post-default withdraw
vm.prank(attacker);
cdoEpoch.requestWithdraw(0, address(AAtranche));  // mints postDefaultRequests receipt

vm.prank(attacker);
cdoEpoch.claimWithdrawRequest();                  // paid 1:1 from defaultRecoveryReserve

// ASSERT: attacker received underlying although reserve only covers defaulted claims at recoveryPrice
assertGt(underlying.balanceOf(attacker), 0);

// victim then claims: defaulted receipt should get claimBasis*price but reserve is drained
uint256 balBefore = underlying.balanceOf(victim);
vm.prank(victim);
cdoEpoch.claimWithdrawRequest();
uint256 paid = underlying.balanceOf(victim) - balBefore;
assertLt(paid, victimBasis * recoveryRatio / 1e18); // victim underpaid / reverts
```

Key invariants broken: one receipt one payout (receipts created after finalization are paid from a reserve sized only for pre-default claims), and fair loss distribution (post-default claims pay at par `1e18` instead of `defaultRecoveryPrice`). Guards do not stop it: `_onlyIdleCDO` is satisfied, `isWalletAllowed` passes for any KYC'd lender, and the reserve guard in `_transferFundedClaim` is bypassed because the payout goes through `_transferDefaultRecovery`.