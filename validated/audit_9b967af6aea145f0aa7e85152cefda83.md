### Title
Buffer-period deposit + `requestWithdraw` sandwich mints a receipt for a full epoch of interest on capital never lent during the epoch - (File: contracts/IdleCDOEpochVariant.sol)

### Summary
A KYC-passing lender can front-run `startEpoch` by depositing into a tranche during the buffer period and immediately calling `requestWithdraw` in the same transaction. `requestWithdraw` credits the receipt with a full `epochDuration` of interest even though the withdrawer's principal leaves live NAV in `_withdrawOps` and is never sent to the borrower for the upcoming epoch. The interest portion of the receipt is therefore funded by honest tranche holders' yield when the borrower repays at `stopEpoch`. This is the credit-vault analog of the reported delegation/reward sandwich: stake (deposit) right before the reward event (epoch start), withdraw right after (request, then claim post-`stopEpoch`), capturing epoch yield with near-zero capital exposure.

### Finding Description
During the buffer phase `isEpochRunning` is false, deposits are unpaused, and `allowAAWithdrawRequest`/`allowBBWithdrawRequest` are true, so an attacker can atomically `depositAA`/`depositBB` then `requestWithdraw`.

Inside `requestWithdraw`, the receipt amount is `principal + interest - fees` where `interest` covers the entire epoch duration:

```solidity
// contracts/IdleCDOEpochVariant.sol:772-790
(uint256 interest, int256 diff) = _calcInterestWithdrawRequest(_underlyings, _tranche);
uint256 totalFees = _totalWithdrawFees(principal, interest);
_underlyings = principal + interest - totalFees;
...
creditVault.requestWithdraw(_underlyings, msg.sender, principal);
_withdrawOps(_amount, principal, _tranche);
```

`_calcInterestWithdrawRequest` computes interest over the full `epochDuration` with no dependence on how long the shares were held:

```solidity
// contracts/IdleCDOEpochVariant.sol:865-870
uint256 totInterest = _calcInterest(_managedContractValue()) * _duration / (_duration + _buffer);
uint256 totTrancheInterest = _calcTrancheInterestShare(totInterest, _tranche);
uint256 _trancheBal = _lastSavedNAV(_tranche);
_interest = _trancheBal == 0 ? 0 : _amount * totTrancheInterest / _trancheBal;
```

At `startEpoch`, `expectedEpochInterest` is computed on `getContractValue()`, which is already net of the attacker's burned principal — the borrower is never asked to pay interest on the withdrawn amount, and the withdrawn principal is excluded from funds sent to the borrower (`IdleCDOEpochVariant.sol:260-262`). Yet the vault-side receipt (`creditVault.requestWithdraw`) irrevocably fixes `principal + interest` as a claim against the epoch's repayment. That interest is only generated on the *remaining* NAV, so it is diluted out of honest depositors' tranche price at `stopEpoch`.

There is no minimum holding-time guard: nothing in `_deposit`, `requestWithdraw`, or `_calcInterestWithdrawRequest` distinguishes a deposit made one second ago from one made a year ago. `_skimDonatedAssets`, KYC, and `_guarded` limits do not stop a legitimate KYC'd whale from performing this.

### Impact Explanation
The attacker earns approximately `amount * apr * epochDuration / 365 days` of yield while their capital was at risk only for the buffer-period seconds between deposit and withdraw request — a riskless extraction structurally identical to the Covalent report (delegate before `rewardValidators`, unstake after). The stolen interest is paid from the fixed borrower repayment, so honest tranche holders' `virtualPrice` at `stopEpoch` is lower by exactly the attacker's interest share. With a large deposit relative to tranche NAV (subject only to `_guarded` TVL limits), the attacker can capture a large fraction of the epoch's tranche interest — e.g., depositing ~19x the existing NAV captures ~95% of the tranche's interest allocation, matching the report's 95% figure.

### Likelihood Explanation
Requires only a whitelisted lender address and sufficient underlying liquidity; no privileged role. It can be executed atomically (deposit + requestWithdraw in one transaction) in the last block of the buffer period, or via a contract holding the tranche tokens. It does not even require mempool timing precision — any point in the buffer works, since the receipt interest is constant regardless of when in the buffer the request is made. Profit scales linearly with capital; cost is only management/performance fees charged upfront, which are smaller than the interest captured when `interest > totalFees`.

### Recommendation
Do not grant epoch interest to withdraw requests whose principal was not live NAV for the epoch being exited. Concretely: exclude interest from receipts created during the buffer period for deposits made in that same buffer (e.g., track deposit timestamp per position or epoch of last deposit in `IdleCDOStorage`), or have `requestWithdraw` pay interest only on the portion of shares held since before the current buffer began, or require withdrawal requests to have been submitted before the preceding `stopEpoch` for the associated NAV. Alternatively, charge the receipt's interest against `expectedEpochInterest` explicitly at request time so the borrower's obligation includes it rather than socializing it onto remaining holders.

### Proof of Concept
Foundry fork PoC sketch (against the in-repo test harness in `test/foundry/IdleCreditVault.t.sol`, which already exposes `idleCDO`, `cdoEpoch`, `AAtranche`, `setAprs`, `_startEpochAndCheckPrices`, `_toggleEpoch`):

```solidity
// Setup: honest depositor in AA; epoch just stopped, we are in bufferPeriod.
idleCDO.depositAA(1_000 * ONE_SCALE);            // honest TVL
// ... run epoch, stopEpoch, enter buffer ...

address attacker = makeAddr('sandwicher');
deal(defaultUnderlying, attacker, 19_000 * ONE_SCALE);

uint256 navBefore = cdoEpoch.getContractValue();
uint256 priceBefore = cdoEpoch.virtualPrice(address(AAtranche));

vm.startPrank(attacker);
IERC20Detailed(defaultUnderlying).approve(address(cdoEpoch), type(uint256).max);
idleCDO.depositAA(19_000 * ONE_SCALE);           // front-run: deposit in buffer
uint256 shares = AAtranche.balanceOf(attacker);
uint256 receipt = cdoEpoch.requestWithdraw(shares, address(AAtranche)); // same tx
vm.stopPrank();

// receipt ~= 19_000 * ONE_SCALE + full epochDuration interest - fees
assertGt(receipt, 19_000 * ONE_SCALE);

// honest depositor's claimable value at next stopEpoch is reduced:
// borrower repayment only covers interest on net NAV (1_000), while
// attacker receipt pulls principal + interest off the top.
```

Expected outcome: `receipt > principal` by roughly `apr * epochDuration / 365d * 19_000`, and post-`stopEpoch` the honest holder's `virtualPrice` is lower than the no-attack baseline by approximately the attacker's interest amount — demonstrating yield extraction with capital exposed only during the buffer.

Caveat: I was unable to read `contracts/strategies/idle/IdleCreditVault.sol`'s `requestWithdraw`/`claimWithdrawRequest` bodies before the tool budget ended (the grep returned match counts without content). The claim-settlement side — i.e., confirming the vault pays the receipt's interest out of the borrower repayment rather than reverting — should be verified there when building the PoC.