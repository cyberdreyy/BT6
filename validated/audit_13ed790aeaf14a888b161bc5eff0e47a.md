### Title
Withdrawal exit fee computed on gross user amount instead of the amount actually redeemed from the lending provider, overcharging withdrawers — (`contracts/IdleCDOInstadappLiteVariant.sol`)

### Summary
Analogous to the reported `SwapFunctions::_getFeeByAmountWithFee` bug (fee applied to an amount that already includes the fee, on the wrong base), `IdleCDOInstadappLiteVariant._withdraw` computes the Instadapp Lite exit fee as `_expectedFee = toRedeem * withdrawalFeePercentage / 1e6`, where `toRedeem` is the full gross amount the user wants to receive — regardless of how much is actually redeemed from the ETHV2 vault. The user is always charged a fee on the gross amount even when the redemption never touches the lending provider.

### Finding Description
In `_withdraw` (`contracts/IdleCDOInstadappLiteVariant.sol:83-107`):

- `toRedeem` is the gross underlying value of the burned tranche tokens.
- `_expectedFee = _toRedeem * withdrawalFeePercentage() / 1e6` is computed on the full `toRedeem`.
- If `toRedeem <= balanceUnderlying` (contract holds enough unlent funds), no call to `redeemUnderlying` happens at all, yet the user still pays `toRedeem -= _expectedFee`. The Instadapp vault itself would charge zero fee here because nothing is redeemed.
- If `toRedeem > balanceUnderlying`, `_liquidateWithFee(toRedeem - balanceUnderlying)` returns the real `_paidFee` charged by Instadapp on the redeemed portion only. But then `toRedeem -= (_expectedFee - _paidFee)` tops up the charge so the user always ends up paying the full `_expectedFee` computed on the gross amount.

So the effective fee is `want * pct` instead of `redeemedAmount * pct` (and instead of `net * pct/(1-pct)`-style math needed if the intent were "fee such that user receives `want` net"). Numerically: with a 10% withdrawal fee and a user redeeming 1,000,000 fully covered by the unlent balance, the user receives 900,000 although the actual provider fee on zero redeemed tokens is 0. The report's exact pattern applies: the fee base is wrong (gross inclusive-of-fee amount / amount never redeemed), producing a fee larger than intended.

### Impact Explanation
Every withdrawal is overcharged by `_expectedFee - _paidFee`, up to `withdrawalFeePercentage` of the entire withdrawal when the pool has idle liquidity. The overcharged amount stays in the contract, inflating `lastNAV`/tranche prices and effectively transferring value from withdrawing users to remaining tranche holders and the feeReceiver. This is direct, quantified loss of user funds on a routine, permissionless path (`withdrawAA`/`withdrawBB` → `_withdraw`), with no privileged action required.

### Likelihood Explanation
Any tranche holder can trigger it by calling withdraw while the CDO holds any unlent token balance (deposits that were not yet lent, or liquidity returned by the strategy). The larger the unlent buffer relative to redemptions, the larger the overcharge. There is no guard correcting the fee base: `_liquidateWithFee` correctly returns the real `_paidFee`, but the surrounding code deliberately "be sure to remove the missing fee" — the invariant written in the comment encodes the incorrect gross-base fee itself.

### Recommendation
Charge the fee only on the amount actually redeemed from the Instadapp vault:

- When `toRedeem <= balanceUnderlying`, do not subtract `_expectedFee` at all (or only apply it if protocol intends an internal exit fee independent of the provider — in which case document it and compute it on the net-out amount, not the gross `toRedeem`).
- When partially redeeming, set the total fee to `_paidFee` (the real provider fee) rather than forcing it up to `_expectedFee`; i.e., remove the `toRedeem -= (_expectedFee - _paidFee)` top-up.
- If the intended semantic is "user receives exactly `want` after fees," derive the amount to redeem via `redeem = want * 1e6 / (1e6 - withdrawalFeePercentage)` so the fee is computed on the amount that actually bears it, mirroring the report's recommended `amountWithoutFee = amountWithFee * denom / (num + denom)` fix.

### Proof of Concept
Foundry fork-style PoC (pseudocode against the deployed Instadapp Lite variant and ETHV2 vault):

```solidity
// Setup: deposit so the CDO holds unlent `token` balance >= withdraw amount
deal(token, address(cdo), 1_000_000e6);
uint256 trancheAmt = cdo.depositAA(1_000_000e6);
// do NOT call a harvest/lend step so all funds stay unlent in the CDO

// withdrawalFeePercentage = 100_000 (10%)
uint256 balPre = token.balanceOf(user);
cdo.withdrawAA(0); // burns full balance

uint256 received = token.balanceOf(user) - balPre;
// BUG: received == 900_000e6 although zero tokens were redeemed from
// ETHV2Vault (redeemUnderlying was never called), so the real provider fee is 0.
// Expected: received == ~1_000_000e6.
assertLt(received, 1_000_000e6); // demonstrates gross-base fee overcharge
```

The invariant broken is fair burn: tranche tokens are burned at `tranchePrice` NAV but the payout is haircut by a fee whose base (the gross claim) does not correspond to any fee actually owed to the lending provider.

Note: I verified the fee logic in `IdleCDOInstadappLiteVariant.sol` lines 34–107 directly; if this variant is considered a legacy/strategy contract outside the intended scope, the closest in-scope analogs (`_totalWithdrawFees`, `_netGainAfterFees`, `prepareStopEpochWithApr0` in `contracts/IdleCDOEpochVariant.sol` / `contracts/strategies/idle/IdleCreditVault.sol`) all compute fees on correctly-scoped bases (principal or interest only) and I did not find a fee-on-gross error in them.