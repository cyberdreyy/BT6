### Title
Instant-withdraw receipts are paid immediately with no funding or maturity check, letting a mid-epoch requester spend underlying reserved for funded withdraw claims — double spend of the strategy's underlying balance - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`claimInstantWithdrawRequest` burns the caller's instant receipt and pays out from the strategy's free underlying balance via `_transferFundedClaim`, which only protects `defaultRecoveryReserve`. Unlike `claimWithdrawRequest`/`_claimFundedWithdrawRequest` (which gate on `epochNumber > lastWithdrawRequest`), instant claims have no maturity check and no check that the request was actually funded through `collectInstantWithdrawFunds`. An instant request made during a running epoch is therefore immediately claimable against underlying that was collected to back already-matured normal withdraw receipts (which sit in the strategy until claimed). The same underlying is effectively released twice — the analog of CVE-2021-36088's double free in `flb_free`.

### Finding Description
- `requestInstantWithdraw` (IdleCreditVault.sol:356-375) burns CDO strategy tokens, mints receipt tokens, and increments `instantWithdrawsRequests[_user]` / `pendingInstantWithdraws` — but pulls **no** underlying. Funding only happens later when the CDO calls `collectInstantWithdrawFunds` (lines 398-403), which merely decrements `pendingInstantWithdraws` and transfers `_amount` from the CDO.
- `claimInstantWithdrawRequest` (lines 380-393) pays `instantWithdrawsRequests[_user]` in full the moment it is called. There is no per-epoch funding marker, no `pendingInstantWithdraws`-vs-balance check, and no epoch-wait. `_transferFundedClaim` (lines 897-907) transfers any underlying balance above `defaultRecoveryReserve`.
- Underlying held by the strategy is a fungible pool: borrower-funded amounts collected by `collectWithdrawFunds` (lines 411-430) for matured normal receipts remain in the contract until each user claims. Nothing segregates them from instant payouts.

Attack trace (running epoch, instant-withdraw mode enabled):
1. Epoch N-1 stops; `stopEpoch`/`collectWithdrawFunds` pulls underlying for matured normal receipts into the strategy. `pendingWithdraws` is cleared but the underlying sits unclaimed.
2. Epoch N runs with `allowInstantWithdraw`. Attacker (KYC-passing lender holding tranche tokens) calls `requestWithdraw`/`requestInstantWithdraw` through the CDO during the allowed instant window. Their receipt is recorded but `collectInstantWithdrawFunds` has not yet run for it.
3. Attacker immediately calls `cdoEpoch.claimInstantWithdrawRequest()` → `IdleCreditVault.claimInstantWithdrawRequest` burns the receipt and `_transferFundedClaim` pays out of the pooled balance — spending underlying earmarked for the honest matured receipts.
4. When honest users later call `claimWithdrawRequest`, `_transferFundedClaim` reverts (`balance - reserve < amount`) or `safeTransfer` fails — their funded claims are unpayable (insolvency).

### Impact Explanation
Direct theft / insolvency: the attacker extracts underlying equal to their unfunded instant receipt, and an equal amount of funded normal-withdraw claims becomes permanently unpayable (claims revert). Loss is bounded by the strategy's liquid underlying balance at claim time, i.e., up to the full funded-but-unclaimed withdraw pool plus any prefunded instant reserve belonging to other users. The broken invariant is "one receipt one payout": two claimants are paid from the same underlying.

### Likelihood Explanation
Requires `allowInstantWithdraw` enabled and the ability to submit an instant request while the strategy holds unfunded-earmarked underlying. The strategy itself imposes no maturity or funding gate on instant claims, so exploitability hinges entirely on CDO-side timing rules (`instantWithdrawDelay`, buffer-vs-running request windows) and on whether every accepted instant request is prefunded before claims open. If any window exists where a request is recorded before `collectInstantWithdrawFunds` runs (mid-epoch request, partial borrower prefund at `startEpoch`), the drain is atomic and repeatable. The deliberate `defaultRecoveryReserve` guard in `_transferFundedClaim` shows reserve isolation was considered only for default recovery, not for the funded normal-withdraw pool.

### Recommendation
Gate instant claims on actual funding: track a funded instant balance (incremented in `collectInstantWithdrawFunds`, decremented on claim) and pay claims only against it, or record per-epoch instant funding analogous to `lossRecoveryPriceByEpoch`/`instantWithdrawClaimsByEpoch` and revert claims for unfunded request epochs. Alternatively, isolate the funded normal-withdraw pool (e.g., `fundedWithdrawReserve` mirroring `defaultRecoveryReserve`) inside `_transferFundedClaim` so instant payouts can never consume it.

### Proof of Concept
Foundry fork outline:
1. Deploy the repo's standard epoch stack (`IdleCDOEpochVariant` + `IdleCreditVault`); deposit AA/BB for honest user H and attacker A.
2. `startEpoch`; H calls `requestWithdraw`; warp past `epochEndDate`; manager calls `stopEpoch`, which pulls funded underlying into the strategy via `collectWithdrawFunds`. Do not let H claim yet.
3. `startEpoch` (epoch N running); manager enables instant withdrawals (`setInstantWithdrawParams`). A calls `requestInstantWithdraw` through the CDO.
4. A calls `cdoEpoch.claimInstantWithdrawRequest()` before any `collectInstantWithdrawFunds` for the request. Assert A's underlying balance increased and the strategy balance dropped by the receipt amount.
5. H calls `claimWithdrawRequest` → expect revert in `_transferFundedClaim`/`safeTransfer` due to insufficient balance. Assert: A's payout equals H's shortfall.

Note: if the CDO enforces that instant requests are only accepted in the buffer and are always fully prefunded at `startEpoch` before `claimInstantWithdrawRequest` becomes callable, this vector is mitigated at the orchestration layer; the strategy-level absence of a funding check then remains a latent invariant violation rather than an exploited path. I could not fully verify the CDO-side gating of mid-epoch instant requests within the available search iterations.