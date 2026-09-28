### Title
`createWriteOffRequest` does not validate `underlyingsRequested` against the current tranche value, allowing anyone to buy escrowed tranche tokens far below NAV - ([File: contracts/IdleCreditVaultWriteOffEscrow.sol](contracts/IdleCreditVaultWriteOffEscrow.sol))

### Summary
`IdleCreditVaultWriteOffEscrow.createWriteOffRequest(uint256 amount, uint256 underlyingsRequested)` escrows a lender's tranche tokens together with a freely chosen ask price (`underlyingsRequested`), but never checks that price against the tranche's current value (`IdleCDOEpochVariant.virtualPrice(tranche)`). `fullfillWriteOffRequest` can then be called by *any* wallet and only requires `_underlyings >= currentRequest.underlyings`, so a mispriced (too-low) request can be filled immediately by an unprivileged buyer who captures the difference. This is the direct analog of the Flatcoin `priceLowerThreshold`/`priceUpperThreshold` issue: a user-supplied price/threshold is accepted without a sanity check against current market/NAV price, and a counterparty profits from the mistake.

### Finding Description
- `createWriteOffRequest` pulls `amount` tranche tokens into escrow and stores `underlyings` = the user-supplied `underlyingsRequested` with no bounds checking at all — not zero, not a floor, not a ceiling relative to `virtualPrice` (`contracts/IdleCreditVaultWriteOffEscrow.sol:86-102`). Only `amount == 0` is rejected and the epoch must be running.
- `fullfillWriteOffRequest` is permissionless ("this function can be called by any wallet", line 122) and only enforces `currentRequest.tranches == _tranches && _underlyings >= currentRequest.underlyings` (lines 123-131). There is no minimum-price check: if the lender accidentally set `underlyingsRequested` to 1 wei or otherwise far below `amount * virtualPrice / 1e18`, the fulfiller pays that trivial sum (minus the ≤1% exit fee goes to the lender) and receives the full `amount` of tranche tokens.
- The tranche tokens the fulfiller receives are redeemable for real value via the normal withdrawal path (`requestWithdraw`/`claimWithdrawRequest` on `IdleCDOEpochVariant`, or at epoch end), so the fulfiller's profit is directly quantifiable: `(amount * virtualPrice / 1e18) - underlyingsPaid`.
- No existing guard stops this: `EpochNotRunning` gating doesn't help (requests are only creatable while running, which is exactly when tranches still carry full expected value), `nonReentrant` is irrelevant, and the exit fee is capped at 1% (`MAX_EXIT_FEE`), far smaller than the mispricing loss.

### Impact Explanation
A lender who enters a wrong `underlyingsRequested` (decimal error, stale price assumption, UI bug, or simply 0/very low) loses up to the full market value of their escrowed tranche tokens. The fulfiller (any EOA, no KYC or role needed — the function is explicitly callable by any wallet) can atomically fulfill and later redeem the tranches through `requestWithdraw`, extracting `trancheValue - underlyingsRequested` as pure profit taken directly from the lender's funds. The foot-gun is entirely preventable with a cheap price check, matching the original report's "no trader would do this intentionally" scenario.

### Likelihood Explanation
Medium-to-high in the mistake scenarios and trivially exploitable once it happens: the escrow design creates a standing orderbook where any underpriced request is free money for the first bot/searcher to call `fullfillWriteOffRequest`. Monitoring `userRequests` is trivial and fulfillment is a single nonReentrant call requiring only an ERC20 approval. The only mitigation is user care, since the protocol performs no price sanity check — identical root cause to the external report.

### Recommendation
In `createWriteOffRequest`, validate the ask against the current tranche NAV with a configurable tolerance, e.g.:

```solidity
uint256 fairValue = amount * IdleCDOEpochVariant(idleCDOEpoch).virtualPrice(tranche) / ONE_TRANCHE;
if (underlyingsRequested < fairValue * (FULL_VALUE - maxDiscountBps) / FULL_VALUE) revert WrongRequest();
if (underlyingsRequested > fairValue * (FULL_VALUE + maxPremiumBps) / FULL_VALUE) revert WrongRequest();
```

Alternatively, require the user to re-specify `expectedUnderlyings` in `fullfillWriteOffRequest` and revert if the stored ask deviates from current `virtualPrice` beyond a bound, so stale requests cannot be filled at outdated prices.

### Proof of Concept
Foundry fork test sketch (place under `test/foundry/`, using the existing `IdleCreditVault`/`IdleCDOEpochVariant` deployment harness):

```solidity
function testWriteOffUnderpricedRequestExploit() external {
    // setup: deposit into AA tranche, manager starts epoch
    uint256 amount = 10_000 * ONE_SCALE;
    idleCDO.depositAA(amount);           // lender (this contract / lender EOA)
    vm.prank(manager);
    cdoEpoch.startEpoch(0);              // epoch running

    // lender mistakes: requests only 1 wei of underlying for 10k tranches
    IERC20Detailed(tranche).approve(address(escrow), amount * 1e18);
    escrow.createWriteOffRequest(amount * 1e18, 1); // underlyingsRequested = 1 wei

    // attacker: any wallet, no role
    address attacker = makeAddr("attacker");
    deal(address(underlying), attacker, 1);
    vm.startPrank(attacker);
    underlying.approve(address(escrow), 1);
    escrow.fullfillWriteOffRequest(lender, amount * 1e18, 1);
    vm.stopPrank();

    assertEq(IERC20Detailed(tranche).balanceOf(attacker), amount * 1e18);
    // attacker now redeems tranches at ~virtualPrice (≈1e18 per tranche)
    // profit ≈ amount * virtualPrice / 1e18 - 1 wei
}
```

Expected outcome: the fulfill succeeds, the attacker holds ~10,000e18 tranche tokens for 1 wei of underlying, and can recover near-full value via `cdoEpoch.requestWithdraw` at epoch end — demonstrating direct theft of the lender's position enabled by the missing price validation.