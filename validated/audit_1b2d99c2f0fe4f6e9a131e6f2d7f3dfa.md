### Title
Instant-withdraw path in `requestWithdraw` skips management and performance fees entirely, letting tranche holders exit accrued-yield-inclusive principal fee-free - (File: contracts/IdleCDOEpochVariant.sol)

### Summary
`IdleCDOEpochVariant.requestWithdraw` has two mutually exclusive settlement paths: a queued path that deducts `_totalWithdrawFees` (upfront management fee plus net performance fee) from the receipt, and an instant-withdraw path that burns tranche tokens at the post-`_updateAccounting` price and routes the full underlying value to `IdleCreditVault.requestInstantWithdraw` with no fee deduction at all. When the vault's unscaled APR drops by more than `instantWithdrawAprDelta` relative to `lastEpochApr`, every withdrawer is routed through the fee-free instant path, so the management and performance fees that the protocol charges on economically identical exits are never collected.

### Finding Description
In `requestWithdraw`, after `_updateAccounting()` accrues yield into tranche prices, the instant branch is taken when `lastEpochApr > creditVault.unscaledApr() + instantWithdrawAprDelta`:

- Instant path (`IdleCDOEpochVariant.sol:761-769`): `_underlyings = _trancheToUnderlyings(_amount, _tranche)` — principal plus all accrued interest embedded in the tranche price — then `creditVault.requestInstantWithdraw(_underlyings, msg.sender)` and `_withdrawOps`. No call to `_totalWithdrawFees`, no increase of `pendingWithdrawFees`.
- Queued path (`IdleCDOEpochVariant.sol:772-790`): `totalFees = _totalWithdrawFees(principal, interest)` is deducted from `principal + interest` and accumulated into `pendingWithdrawFees`, which is later paid to `feeReceiver`/owner at `stopEpoch` (`_transferFeeUnderlyings` / `unclaimedFees`).

Because `_updateAccounting()` has just run, the tranche price already contains the epoch's accrued interest, and the protocol's own fee model (`_netGainAfterFees`, `fee`, `managementFee`) says that yield must pay a performance fee and the time spent outside live NAV must pay a management fee. The instant path charges neither. This is the same bug class as the reference report: a fee that should apply uniformly is silently dropped on one code branch.

### Impact Explanation
Any tranche holder who calls `requestWithdraw` while `lastEpochApr > unscaledApr + instantWithdrawAprDelta` receives `tranchePrice * amount` with zero performance fee on the accrued interest embedded in the price and zero management fee for the buffer/waiting time (instant receipts are only funded at the next `startEpoch`/`getInstantWithdrawFunds`, so the funds do sit outside live NAV for the buffer remainder). The feeReceiver and owner lose `fee * accruedInterest` plus `managementFee` on every instantly-withdrawn unit. Since the routing condition applies to all requests during the buffer, the entire exiting TVL escapes fees, not just an attacker's.

### Likelihood Explanation
Requires `unscaledApr` to drop below `lastEpochApr - instantWithdrawAprDelta` — this happens whenever the manager lowers the vault APR at `stopEpoch`/`setAprs`, a normal operational event (e.g., borrower rate renegotiation). Privileged roles remain honest; the attacker is any KYC-passed tranche holder who times `requestWithdraw` during the buffer window, or simply every user exiting during such a window. No existing guard (`_isInstantWithdrawEnabled`, `allowInstantWithdraw`, `_checkDefault`) restores the skipped fee; the only mitigations are `disableInstantWithdraw` and the programmable-borrower exclusion.

### Recommendation
Charge the proportional fee on the instant path too: compute the accrued-yield component of `_trancheToUnderlyings` (or a prorated management fee over the buffer remainder plus the performance fee on embedded gains) and deduct it before calling `requestInstantWithdraw`, crediting `pendingWithdrawFees`/`unclaimedFees` identically to the queued path. Alternatively, if instant withdrawals are intentionally fee-free, document it and exclude the accrued-interest portion by burning at principal-only value.

### Proof of Concept
```solidity
// Foundry fork test sketch (deploy via IdleCreditVaultFactory fork fixture)
// Pre-conditions: epoch running, fee > 0, managementFee > 0, instantWithdraw enabled.
uint256 amount = 100_000 * ONE_SCALE;
idleCDO.depositAA(amount);            // attacker is a KYC'd AA holder
// epoch runs; accrue interest so tranchePrice(AA) > 1e18
vm.warp(cdoEpoch.epochEndDate() - 1);
vm.prank(manager);
cdoEpoch.stopEpoch(newLowerApr, 0);   // unscaledApr drops below lastEpochApr - delta

uint256 balPre = underlying.balanceOf(attacker);
uint256 trancheAmt = AAtranche.balanceOf(attacker);
vm.prank(attacker);
uint256 requested = cdoEpoch.requestWithdraw(trancheAmt, address(AAtranche));

// Bug: requested == trancheAmt * tranchePrice / 1e18 with zero fee deducted.
// Expected: requested reduced by performance fee on accrued interest + mgmt fee
// over the remaining buffer, i.e. requested' = gross - _totalWithdrawFees(...).
assertEq(cdoEpoch.pendingWithdrawFees(), 0);              // no fees booked
assertEq(requested, trancheAmt * cdoEpoch.tranchePrice(address(AAtranche)) / 1e18);
// fund and claim at next startEpoch -> attacker receives full accrued yield fee-free;
// feeReceiver balance delta == 0 where the queued path would have paid out fees.
```