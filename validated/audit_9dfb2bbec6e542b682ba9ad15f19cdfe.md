### Title
Spot (zero-window) exchange rate used as `price()` lets an attacker inflate tranche virtualPrice and drain underlying - ([File: contracts/strategies/harvest/IdleHarvestStrategy.sol])

### Summary
The external finding is a TWAP whose observation window can collapse to a single block, making the oracle price manipulable. The direct analog in idle-tranches is that every strategy `price()` is a **spot exchange rate with no time window at all**. `IdleCDO.virtualPrice()` / `getContractValue()` value the entire strategy-token position at `strategyToken.balanceOf(CDO) * IIdleCDOStrategy.price() / oneToken`, and that instantaneous rate is used to mint and redeem tranche shares. Several strategies implement `price()` via a balance-derived exchange rate that an unprivileged attacker can move within one transaction:

- `IdleHarvestStrategy.price()` → `fToken.getPricePerFullShare()` (underlying balance / share supply — the historically exploited rate).
- `ERC4626Strategy.price()` → `vault.convertToAssets(1e18)` (`contracts/strategies/ERC4626Strategy.sol:121-124`) — manipulable by direct underlying donation to the ERC4626 vault.
- `IdleClearpoolStrategy.price()` → `cpToken.getCurrentExchangeRate()`.
- `IdleMStableStrategy.price()` / Euler strategies → `exchangeRate()` / `balanceOfUnderlying()` — all instantaneous.

### Finding Description
`price()` returns the current underlying-per-strategyToken ratio with zero smoothing. An attacker holding tranche tokens can donate underlying directly to the underlying yield vault (or deposit/withdraw to move `convertToAssets`), pushing `price()` up before calling `withdrawAA`/`withdrawBB`/redeem flows. `virtualPrice` then overstates NAV, so the attacker redeems more underlying than their fair share — the excess comes from other depositors. This is the same manipulation-resistance failure as the reported 1-block TWAP: the "observation window" is literally zero, cheaper to manipulate than a short TWAP.

`getContractValue`/`virtualPrice` feed share minting in `_deposit`/`_mintSharesAtCurrPrice` and redemptions; `_skimDonatedAssets` only protects tokens donated to the CDO contract itself, not donations into the external vault that move `price()`.

### Impact Explanation
Direct theft: redeeming tranches at an artificially inflated exchange rate withdraws more underlying than the attacker's fair claim; the loss is bounded by the donation size but profitable whenever redemption proceeds exceed manipulation cost, and is repeatable. Solvency invariant (fair mint/burn, one receipt one payout) is broken.

### Likelihood Explanation
Unprivileged: any tranche holder or EOA can transfer underlying to Harvest fToken / ERC4626 vault / Clearpool pool. No privileged role required. Cost is the donation amount; profit is the inflated redemption on pre-held tranche supply. Feasibility depends on the specific vault's rate formula (fToken `getPricePerFullShare` is balance-derived and manipulable; `convertToAssets` likewise unless virtual-shares offset is large).

### Recommendation
Read exchange rates through a manipulation-resistant path: use ERC4626 `convertToAssets` only on vaults with meaningful virtual offset, enforce a tolerance band vs. the last stored NAV (`lastNAV`) before applying large instantaneous `price()` moves inside `_updateAccounting`, or rate-limit NAV deltas per epoch. Alternatively snapshot a floor price at epoch boundaries (as `IdleCDOUsualVariant` does with `priceAtStartEpoch`/`getChainlinkPrice`) and treat instantaneous balance-based rates only as upper bounds.

### Proof of Concept
Foundry fork outline (mainnet, Harvest fDAI strategy CDO):

```solidity
// test: attacker already holds BB tranche tokens
// 1. record fair virtualPrice: vp0 = cdo.virtualPrice(BBTranche)
// 2. attacker DAI.transfer(address(fDAI), donationAmount);
//    fDAI.getPricePerFullShare() rises -> strategy.price() rises
// 3. vp1 = cdo.virtualPrice(BBTranche) // > vp0
// 4. cdo.withdrawBB(attackerBal) redeems at inflated NAV
// 5. assert receivedUnderlying > fairShare (attacker profit = donation-independent excess)
```

Note: I was not able to fully verify each strategy's `price()` formula and whether the underlying vaults' rates are donation-sensitive in every deployment (e.g., Clearpool `getCurrentExchangeRate` may resist raw donations); the ERC4626/Harvest paths are the strongest. Some index file contents may be truncated, so a Devin session should confirm the exact `price()` implementations and the `virtualPrice` call sites before finalizing the PoC.