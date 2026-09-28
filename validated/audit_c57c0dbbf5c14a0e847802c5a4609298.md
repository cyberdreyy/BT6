### Title
First-depositor NAV inflation via direct `token` donation lets an attacker mint near-zero tranche shares for the next depositor and steal their deposit - ([File: contracts/IdleCDOCreditVault.sol])

### Summary
`IdleCDOCreditVault._deposit` mints tranche tokens at a NAV-derived price (`_mintSharesAtCurrPrice` → `_tranchePrice`/`_updateAccounting` → `_virtualPriceAux`). `getContractValue()` — the NAV used by `_updateAccounting` — counts the raw `token` balance held by the contract (`contracts/IdleCDOCreditVault.sol:125-128`), and no minimum-supply, virtual-share offset, or minimum-deposit guard exists. A KYC-passing attacker who is the first depositor can therefore be the sole holder of a tranche, donate a large amount of `token` directly to the vault (inflating the tranche price via `_virtualPriceAux` at `contracts/IdleCDOCreditVault.sol:287-336`), and cause a subsequent victim deposit to mint zero or near-zero shares while the full deposited amount is still credited to `lastNAVAA`/`lastNAVBB` (`_mintShares` at `contracts/IdleCDOCreditVault.sol:355-363`). The attacker then withdraws, redeeming essentially the entire NAV including the victim's funds. This is the same bug class as the DODO report: reserve/NAV imbalance manipulated to mint the victim too few shares, then unwind for profit.

### Finding Description
- `_updateAccounting` computes `nav = getContractValue()` (`contracts/IdleCDOCreditVault.sol:227`), and `getContractValue` is `strategyToken balance + raw token balance - unclaimedFees` (`contracts/IdleCDOCreditVault.sol:125-128`). A direct `token.transfer` to the vault therefore inflates `nav` and hence tranche `priceAA`/`priceBB` once `_virtualPriceAux` spreads the gain across the existing supply (`contracts/IdleCDOCreditVault.sol:306-336`).
- Shares minted are `amount * ONE_TRANCHE_TOKEN / _tranchePrice(_tranche)` (`contracts/IdleCDOCreditVault.sol:344-348`). When supply is 0 the price is `oneToken` (`contracts/IdleCDOCreditVault.sol:297`), but after a 1-wei first deposit the price is fully donation-controlled.
- The victim's full deposit amount is still added to `lastNAVAA`/`lastNAVBB` regardless of shares minted (`_mintShares`, `contracts/IdleCDOCreditVault.sol:357-362`), so the unminted remainder accrues to existing holders.
- Withdrawal burns shares at `tranchePrice` (`_withdrawOps`, `contracts/IdleCDOCreditVault.sol:369-382`), letting the near-sole holder redeem the inflated NAV. The donation path is reachable because `_deposit` only measures the caller's own balance delta (`contracts/IdleCDOCreditVault.sol:203-206`) and pushes `_amount` to the strategy (`contracts/IdleCDOCreditVault.sol:211`); `_skimDonatedAssets`/skimming merely routes stray tokens into the strategy, which still counts them as NAV gain — it does not prevent price inflation.

### Impact Explanation
The victim loses essentially their entire deposit: they receive 0 (or dust) tranche shares while `lastNAV` is credited with their full amount. The attacker redeems nearly 100% of supply and extracts `X + V` for a cost of `X` donated + 1 wei deposit (minus performance fee on the gain), i.e. profit ≈ `V` whenever the donation `X` is large enough to drive the victim's minted shares to 0/1. Direct theft of user deposits.

### Likelihood Explanation
Requires the attacker to hold (or capture) ~100% of one tranche class — trivially achieved by being the first depositor on a fresh vault, or after all other holders of a class have exited. The attacker only needs KYC clearance (`isWalletAllowed`) to deposit and capital ≥ the victim's deposit, which can be recycled across victims. Mempool/front-running of `depositAA`/`depositBB` executes the sandwich. `whenNotPaused`, `_guarded` limits, and the `Default()` check do not block it (donation is a gain, not a loss); `_checkSameBlock` only prevents same-block deposit+withdraw, not cross-block unwind.

### Recommendation
Enforce a minimum initial deposit and/or mint a dead-share reserve (e.g. send the first `MIN_LIQUIDITY` tranche tokens to address(0) or `feeReceiver`) on the first deposit of each tranche class; alternatively add virtual shares/assets in `_virtualPriceAux` and `_mintSharesAtCurrPrice` so donated NAV cannot move the mint price to infinity, and consider accounting raw `token` balance out of `nav` in `_updateAccounting` (mirroring `_managedContractValue`).

### Proof of Concept
Foundry fork sketch (AA tranche, `token` = USDC-style ERC20):

```solidity
// setup: vault deployed, attacker + victim KYC-allowed
uint256 D = 2000e6;   // donation > victim deposit
uint256 V = 1000e6;

// 1) attacker first deposit -> supply 1 wei, price = oneToken
token.approve(cdo, 1);
vm.prank(attacker); IdleCDOCreditVault(cdo).depositAA(1);

// 2) donation inflates NAV: getContractValue counts raw token balance
token.transfer(cdo, D);   // nav += D

// 3) victim deposits V; _updateAccounting prices AA at ~D, mints V*1e18/priceAA ~= 0
token.approve(cdo, V);
vm.prank(victim); IdleCDOCreditVault(cdo).depositAA(V);
uint256 victimShares = IERC20(AATranche).balanceOf(victim);
assertLe(victimShares, 1); // ~0 shares while lastNAVAA += V

// 4) attacker redeems all shares at inflated price -> receives ~D + V
uint256 balBefore = token.balanceOf(attacker);
vm.prank(attacker); IdleCDOCreditVault(cdo).withdrawAA(type(uint256).max);
assertGt(token.balanceOf(attacker) - balBefore, D + V - fee(D));
```

Caveats I could not fully verify within the iteration budget: the exact invocation site of `_skimDonatedAssets` (if it converts donations before `nav` is read the same result holds, since skimmed tokens still land in `strategyToken` balance counted by `getContractValue`), whether withdrawal in the current epoch variant routes through a queue (delays but does not prevent the theft), and whether this first-depositor class was previously acknowledged by the protocol — if it was, this falls under the rejection criteria.