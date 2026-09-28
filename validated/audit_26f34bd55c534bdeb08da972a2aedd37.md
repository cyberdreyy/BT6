### Title
Post-default instant-withdraw receipts attach to the finalized default epoch and drain the isolated recovery reserve - ([File: contracts/strategies/idle/IdleCreditVault.sol])

### Summary
Like the kernel bug where a `replace` let one child qdisc gain a second parent (two owners, inflated refcount, UAF), `IdleCreditVault.requestInstantWithdraw` lets a *new* receipt created **after** `finalizeDefaultRecovery` attach itself to `instantWithdrawsRequestsByEpoch[user][defaultRecoveryEpoch]` — the same bucket genuine defaulted receipts are paid from. The post-default receipt therefore becomes a second claimant on `defaultRecoveryReserve`, spending haircut-priced reserve while the freshly collected underlying stays in the contract.

### Finding Description
`requestWithdraw` explicitly branches on `defaultRecoveryFinalized` and routes new requests into `postDefaultRequests`, reverting if old receipts exist (`IdleCreditVault.sol:247-257`). `requestInstantWithdraw` has **no** such branch (`IdleCreditVault.sol:356-375`): post-default it still mints receipt tokens and records them under `instantWithdrawsRequestsByEpoch[_user][currentEpoch]`, where `currentEpoch == epochNumber` is frozen at `defaultRecoveryEpoch` (no further `stopEpoch`/`deposit` increments it after default, so `epochNumber` never advances past the finalized epoch).

When `claimInstantWithdrawRequest` runs and `defaultInstantWithdrawsFinalized` is true (i.e., `pendingInstantWithdraws != 0` at finalization, `IdleCreditVault.sol:696`), `_claimDefaultedInstantWithdrawRequest` reads `instantWithdrawsRequestsByEpoch[_user][defaultRecoveryEpoch]` — now containing the attacker's new post-default request — and pays `claimBasis * defaultRecoveryPrice / RECOVERY_FULL` via `_transferDefaultRecovery`, which decrements `defaultRecoveryReserve` (`IdleCreditVault.sol:842-856, 912-917`).

The reserve math: at finalization, `defaultRecoveryReserve` covers exactly `totalBasis * recoveryPrice` for pre-existing claimants (`IdleCreditVault.sol:686-691`). A post-default instant request is backed by newly collected underlying (via `collectInstantWithdrawFunds`, `IdleCreditVault.sol:398-403`), yet its payout is drawn from the already-committed recovery reserve, not from the new funds. Each such claim removes `amount * recoveryPrice / RECOVERY_FULL` from reserve that belongs to genuine default-epoch claimants; their later `_transferDefaultRecovery` calls then revert on underflow (`defaultRecoveryReserve -= _amount`), permanently freezing their recovery.

### Impact Explanation
- Theft / permanent freezing of unclaimed recovery: legitimate default-epoch instant-withdraw claimants lose up to `postDefaultRequestAmount * defaultRecoveryPrice / RECOVERY_FULL` per attack iteration, capped by available CDO liquidity and the reserve size.
- The attacker loses nothing net: their request is funded at par by newly collected underlying that remains in the contract, while they extract reserve at the recovery price and can re-request repeatedly.
- Existing guards do not stop it: `_transferFundedClaim`'s reserve guard only protects the reserve from *funded* claims; `_transferDefaultRecovery` has no such check. The `requestWithdraw` post-default guard (revert if prior receipts exist) is simply absent from the instant path.

### Likelihood Explanation
- Reachability requires `defaultInstantWithdrawsFinalized == true` (partial prefunding of instant withdrawals at default finalization) and a CDO instant-withdraw entry point that remains callable while `defaulted()` — the CDO's instant path is liquidity-gated rather than default-gated, so this is reachable whenever the strategy still holds/collected liquidity post-default.
- Attacker is an ordinary KYC'd user (allowed role), unprivileged.

### Recommendation
Mirror `requestWithdraw`'s post-default handling in `requestInstantWithdraw`: either revert when `defaultRecoveryFinalized` is set, or route post-default instant requests to a separate `postDefaultInstantRequests` bucket paid at par via `_transferFundedClaim` from their own collected backing — never through `instantWithdrawsRequestsByEpoch[defaultRecoveryEpoch]`/`_transferDefaultRecovery`. Also record post-default instant requests under a sentinel epoch key distinct from `defaultRecoveryEpoch`.

### Proof of Concept
Foundry fork sketch (setup helpers as in `test/foundry/IdleCreditVault.t.sol`):

```solidity
// 1. Lender deposits, epoch runs, borrower defaults mid-epoch while
//    pendingInstantWithdraws != 0 (partially prefunded instant queue).
//    -> finalizeDefaultRecovery() sets defaultRecoveryFinalized,
//       defaultInstantWithdrawsFinalized = true,
//       defaultRecoveryEpoch = epochNumber, reserve = R.

// 2. Attacker (fresh KYC'd user) deposits post-default at haircut price,
//    then calls CDO instant-withdraw path:
cdoEpoch.requestInstantWithdraw(trancheBal, address(AAtranche));
// IdleCreditVault.requestInstantWithdraw mints receipts and writes
// instantWithdrawsRequestsByEpoch[attacker][defaultRecoveryEpoch] += amt
// because epochNumber is still defaultRecoveryEpoch.

// 3. CDO collects funds; attacker claims:
cdoEpoch.claimInstantWithdrawRequest();
// _claimDefaultedInstantWithdrawRequest pays
// amt * defaultRecoveryPrice / RECOVERY_FULL from defaultRecoveryReserve,
// while the freshly collected underlying backing 'amt' stays in the vault.

// 4. assert: defaultRecoveryReserve decreased, yet total legit default
//    claim basis unchanged -> later honest claimant's
//    _transferDefaultRecovery reverts on reserve underflow
//    (permanent freeze) or is underpaid.
```

Caveat: reachability of step 2 assumes the epoch CDO's instant-withdraw entry point does not revert when `defaulted()`; that gating lives in `IdleCDOEpochVariant` and was not fully verified. If it does revert, no vulnerability.