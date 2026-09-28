### Title
`writeOffDeposit` uses stale `priceAA`/`priceBB` and `lastNAV` without calling `_updateAccounting()` - (File: contracts/IdleCDOEpochVariant.sol)

### Summary
`writeOffDeposit` converts tranche tokens to underlyings with the stored tranche price (`_trancheToUnderlyings` → `_tranchePrice`) and computes the interest write-off via `_calcInterestWithdrawRequest`, which divides by the stale `lastNAVAA`/`lastNAVBB` (`_lastSavedNAV`). Unlike `requestWithdraw` (IdleCDOEpochVariant.sol:750) and `depositDuringEpoch` (line 682), it never calls `_updateAccounting()` first — only `_skimDonatedAssets()`. This is the same bug class as the Deriverse finding: checks/accounting performed on a price that was last synced by an unrelated earlier call.

### Finding Description
At `contracts/IdleCDOEpochVariant.sol:936-963`:

```solidity
function writeOffDeposit(uint256 _amount, address _tranche) external {
  _checkNotAllowed(_borrower() != msg.sender || !isEpochRunning);
  _checkTranche(_tranche);
  _skimDonatedAssets();                       // skims donations, but no _updateAccounting()

  uint256 _underlyings = _trancheToUnderlyings(_amount, _tranche);  // stale priceAA/priceBB
  (uint256 interest, int256 diff) = _calcInterestWithdrawRequest(_underlyings, _tranche); // stale lastNAV
  interest = uint256(int256(interest) + diff) * (_epochDuration + bufferPeriod) / _epochDuration;

  _withdrawOps(_amount, _underlyings, _tranche);            // reduces lastNAV by stale-priced amount
  IdleCreditVault(strategy).burnStrategyTokens(_underlyings);
  expectedEpochInterest -= interest;
}
```

`_updateAccounting()` is what syncs `priceAA`, `priceBB`, `lastNAVAA`, `lastNAVBB` and `unclaimedFees` with `getContractValue()` (contracts/IdleCDOCreditVault.sol:222-249). Every other user-facing pricing path refreshes it first; `writeOffDeposit` does not, and mid-epoch nothing else is guaranteed to have run recently (deposits/requests are disabled while `isEpochRunning`), so the staleness window is the whole elapsed epoch plus management-fee accrual (`_accrueManagementFee` is also skipped, while `_updateAccounting` in the credit-vault variant calls it at line 223).

### Impact Explanation
Because strategy-token price is pegged 1:1 (`IdleCreditVault.price()` returns `oneToken`) and tranche prices drift up as interest accrues:

- If `priceAA/priceBB` is stale-low relative to the true virtual price, `_underlyings` is understated. `_withdrawOps` then subtracts too little from `lastNAV` and `burnStrategyTokens` burns too little backing, leaving remaining tranche holders' NAV inflated relative to real strategy-token backing — an over-minted NAV that cannot be fully paid out; the last claimants absorb the shortfall (solvency / one-receipt-one-payout violation).
- If the price is stale-high (e.g., a loss was skimmed/registered), the borrower burns more debt than owed — harm to the honest borrower.
- `expectedEpochInterest -= interest` uses `interest`/`diff` computed against a stale `_managedContractValue()`/`_lastSavedNAV` pair, so the borrower's remaining epoch obligation is decremented by the wrong amount; in the overstated direction this can underflow-revert or strand excess `expectedEpochInterest` that distorts the next `stopEpoch` funding requirement.

Loss magnitude is bounded by the interest + management fees accrued since the last `_updateAccounting` (up to a full epoch's accrual on the written-off principal), and early withdraw-requesters can extract the un-backed NAV before later claimants.

### Likelihood Explanation
Triggering requires only that the borrower performs a negotiated `writeOffDeposit` mid-epoch after accrual time has passed without any `deposit`/`requestWithdraw`/`stopEpoch` refreshing accounting — a normal, intended usage path. No malicious privileged role is needed: the borrower is honest; the miscalculation is embedded in the stale pricing. Damage is realized by ordinary lenders whose later claims exceed remaining backing.

### Recommendation
Call `_accrueManagementFee()` and `_updateAccounting()` inside `writeOffDeposit` (after `_skimDonatedAssets()` and before `_trancheToUnderlyings`), matching `requestWithdraw`:

```solidity
_skimDonatedAssets();
_updateAccounting();
uint256 _underlyings = _trancheToUnderlyings(_amount, _tranche);
```

### Proof of Concept
Foundry fork PoC outline (test file `test/foundry/WriteOffStalePrice.t.sol`):

1. Deploy `IdleCDOEpochVariant` + `IdleCreditVault` with a whitelisted lender; lender deposits into AA; owner/manager calls `startEpoch()`.
2. Warp forward a fraction of the epoch so `virtualPrice(AATranche) > priceAA` while stored `priceAA` stays stale (no deposits/requests possible mid-epoch, so nothing refreshes it).
3. Record `stalePrice = priceAA` and `virtualPrice(AATranche)`; assert `virtual > stale`.
4. As `borrower`, call `writeOffDeposit(amount, AATranche)`.
5. Assert `burnStrategyTokens` burned `amount * stalePrice / 1e18` instead of `amount * virtualPrice / 1e18`, i.e. `lastNAVAA` was reduced by less than the true claim, leaving `getContractValue()` < `lastNAVAA + lastNAVBB` discrepancy in favor of remaining holders.
6. Have a second lender `requestWithdraw`/`claimWithdrawRequest` after `stopEpoch` and show residual NAV claims exceed remaining strategy-token backing (or that `expectedEpochInterest` was decremented by an incorrect `interest` amount derived from stale `lastNAV`), demonstrating measurable under-burning equal to the accrued-but-unsynced interest on the written-off amount.

Caveat: I could not confirm whether `WriteOffEscrow` exists in this repo (the file was not found), so the PoC assumes the borrower calls `writeOffDeposit` directly as an honest actor; the exploitable consequence is borne by unprivileged lenders racing to claim against under-reserved NAV.