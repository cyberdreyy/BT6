### Title
Stale `trancheAPRSplitRatio` used to price mid-epoch deposits and withdrawal-request interest — KYC'd lender can shift real AA/BB TVL and lock an overpaid fixed receipt - ([File: contracts/IdleCDOEpochVariant.sol](contracts/IdleCDOEpochVariant.sol))

### Summary
Analogous to the Maverick finding (price-relevant state — the bin — is only moved *after* the swap, so an attacker inserts a cheap transaction to control the state the real trade executes against), `IdleCDOEpochVariant` prices `depositDuringEpoch` mints and `requestWithdraw` interest from `trancheAPRSplitRatio`, but that ratio is only refreshed inside the ordinary `_deposit` path (`_updateSplitRatio(_getAARatio(true))`). Mid-epoch flows (`depositDuringEpoch`, `requestWithdraw`/`_withdrawOps`) move real tranche TVL/NAV without ever updating the ratio, so the split used to price shares and lock fixed withdrawal receipts is stale. A whitelisted lender can move the true AA:BB ratio with a `depositDuringEpoch` call, then submit a `requestWithdraw` (or second deposit) that is priced off the old ratio, over-crediting interest that is funded as a fixed claim at `stopEpoch` and socializing the shortfall onto the remaining LPs.

### Finding Description
- `requestWithdraw` calls `_updateAccounting()` (which splits accrued gain using the stored `trancheAPRSplitRatio`) and then `_calcInterestWithdrawRequest`, which computes `totTrancheInterest = _calcTrancheInterestShare(totInterest, _tranche)` using `trancheAPRSplitRatio` (`contracts/IdleCDOEpochVariant.sol:883-886`). The resulting `_underlyings` is written as a fixed receipt via `creditVault.requestWithdraw(_underlyings, msg.sender, principal)` (`:788`) and must be funded by the borrower at `stopEpoch`.
- `depositDuringEpoch` calls `_updateAccounting()` (`:682`) but never `_updateSplitRatio`, then prices the mint as `(amount + trancheInterest) * supply / expectedFinal` where both `trancheExpected` and `trancheInterest` derive from the stale `trancheAPRSplitRatio` (`:705-724`).
- The ordinary deposit path updates the ratio only *after* minting (`contracts/IdleCDOCreditVault.sol:206-208`), so once an epoch starts, nothing re-synchronizes `trancheAPRSplitRatio` with the actual AA/BB NAV even though `depositDuringEpoch` and withdraw requests keep changing that NAV. This is the same structure as the Maverick bug: the shared pricing parameter lags one interaction behind the state that determines it.

Attack sequence (epoch running, fixed-APR mode, attacker is a KYC-passed lender holding both tranche positions):
1. `depositDuringEpoch(largeAmount, AATranche)` — fairly minted shares, but real AA TVL/NAV jumps while `trancheAPRSplitRatio` stays at its stale low value.
2. `requestWithdraw(0, BBTranche)` — `_calcInterestWithdrawRequest` credits the attacker `(FULL_ALLOC - staleLowRatio)/FULL_ALLOC` of total epoch interest per unit of `lastNAVBB`, i.e., a BB share of interest computed as if AA TVL were still small. The receipt `creditVault.requestWithdraw` fixes this inflated `principal + interest - fees`.
3. At `stopEpoch`, the borrower only repays principal plus `expectedEpochInterest`; the gap between the funded aggregate receipts and actual repaid interest becomes a loss absorbed by the remaining active LPs through the ordinary waterfall, while the attacker exits with excess tokens.

The mirror-image variant works on `depositDuringEpoch`: with a stale ratio favoring the deposit tranche, repeated mid-epoch deposits are minted at a discounted effective price (over-minted shares claim more than `expectedEpochInterest` covers), diluting existing holders.

### Impact Explanation
Direct overpayment of fixed withdrawal receipts / over-minted tranche shares. The excess is a real claim on vault funds: funded receipts are paid at `claimWithdrawRequest` and any shortfall in `expectedEpochInterest` versus funded receipts is socialized to other LPs via `_updateAccounting`/the BB-first loss waterfall at `stopEpoch`. Loss scales with the size of the ratio the attacker can move (bounded only by their capital and `limit`) and the epoch interest pool.

### Likelihood Explanation
Requires: an epoch vault with mid-epoch deposits enabled (`isBBDepositEnabled`/AA path, `isAYSActive == false`, `isProgrammableBorrower == false`), a KYC-passed attacker, and enough capital to shift the AA/BB ratio — the same capital that is deposited stays the attacker's, so the marginal cost is only gas plus one epoch of lockup on the deposit leg. No privileged action is needed; `requestWithdraw` is permissionless for allowed wallets during the buffer/running window. The precondition (stale vs. true ratio divergence) is created by the attacker's own first transaction, exactly like the Maverick zero-swap.

### Recommendation
Update the split ratio before pricing state-dependent mid-epoch operations — i.e., call `_updateSplitRatio(_getAARatio(true))` at the start of `depositDuringEpoch` (after `_updateAccounting`) and inside `requestWithdraw` after `_updateAccounting`/before `_calcInterestWithdrawRequest` — mirroring the audit recommendation of moving the bin at the *beginning* of a swap rather than after it. Alternatively compute `_calcTrancheInterestShare` from live tranche NAVs instead of the stored `trancheAPRSplitRatio`.

### Proof of Concept
Reproducible Foundry fork test sketch (extend `test/foundry/IdleCreditVault.t.sol` style harness):

```solidity
// setup: deploy IdleCDOEpochVariant vault, seed AA and BB liquidity so that
// trancheAPRSplitRatio == R0 (e.g. AA is 10% of TVL => R0 = 1000 bps of FULL_ALLOC share).
// attacker is a KYC-passed wallet (isWalletAllowed == true) holding BB tranche tokens.

// 1) start epoch via manager: cdoEpoch.startEpoch(...) -> isEpochRunning == true

// 2) attacker moves real AA TVL mid-epoch; trancheAPRSplitRatio stays R0
deal(underlying, attacker, aaDeposit);
cdoEpoch.depositDuringEpoch(aaDeposit, address(AAtranche));
assertEq(cdoEpoch.trancheAPRSplitRatio(), R0); // ratio NOT updated by depositDuringEpoch

// 3) attacker requests full BB withdrawal in the same/later tx.
//    _calcInterestWithdrawRequest credits BB (FULL_ALLOC - R0) share of totInterest
//    even though true AA TVL now dominates the vault.
uint256 receipt = cdoEpoch.requestWithdraw(0, address(BBtranche));

// 4) warp past epochEndDate, borrower repays principal + expectedEpochInterest, manager stops.
//    Assert: sum of funded receipts (incl. attacker's inflated one) exceeds
//    repaid principal + expectedEpochInterest, and remaining LPs' virtualPrice
//    is lower than in the run where step 2 is omitted (loss socialization).
```

Caveat: I verified that `depositDuringEpoch` and `requestWithdraw` do not refresh `trancheAPRSplitRatio` and that both code paths consume it for pricing, but I could not fully trace whether `stopEpoch`/`startEpoch` or `_withdrawOps` resynchronize the ratio or cap funded receipts against `expectedEpochInterest`; if `stopEpoch` reverts or clips receipts when funded claims exceed repaid interest, the impact degrades to a freeze rather than a loss. A fork PoC is needed to confirm the net overpayment direction and magnitude.