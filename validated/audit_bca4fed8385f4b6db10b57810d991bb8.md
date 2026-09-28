### Title
`fullfillWriteOffRequest` executes at a stale agreed price with no epoch/default check, so a fulfiller can pay full underlyings for impaired tranche tokens - (contracts/IdleCreditVaultWriteOffEscrow.sol)

### Summary
`createWriteOffRequest` requires the epoch to be running (`IdleCDOEpochVariant(idleCDOEpoch).isEpochRunning()`), but `fullfillWriteOffRequest` performs no check on the vault state at all. A write-off request priced while the epoch was healthy remains fulfillable after the epoch has stopped with a loss (`stopEpoch`/`_updateAccounting` BB-first loss haircut) or after the borrower has defaulted (`_handleBorrowerDefault`). The fulfiller still pays the full `underlyings` amount agreed pre-loss but receives tranche tokens whose claim on the vault is worth materially less — the same bug class as `add_margin` crediting collateral to an already-liquidatable position without rechecking it.

### Finding Description
- `createWriteOffRequest` gates on `isEpochRunning` (line 88), acknowledging the request is only meaningful while the tranche retains its running-epoch value.
- `fullfillWriteOffRequest` (lines 123–155) only validates `currentRequest.tranches == _tranches` and `_underlyings >= currentRequest.underlyings`. It never checks `isEpochRunning`, `defaulted`, `priceAA/priceBB`, or any current valuation of the escrowed tranche tokens.
- After `stopEpoch` applies a loss via `_updateAccounting`, or `_handleBorrowerDefault` sets `defaulted = true` and pauses the CDO (IdleCDOEpochVariant.sol lines 577–599), the escrowed tranche tokens drop in value per the loss waterfall (BB first). On default, the tokens can no longer be redeemed through the normal flow nor burned through `writeOffDeposit` (which itself requires `isEpochRunning`, line 938) — they are only claimable through the default recovery path at a fraction of face value.
- Despite this, `fullfillWriteOffRequest` atomically transfers the full stale `underlyings` (minus the `exitFee`) to the lender and hands the now-impaired tranche tokens to the fulfiller. The swap is permissionless ("this function can be called by any wallet"), so any third-party write-off fulfiller can trigger it, and the protocol provides no warning or repricing.

This mirrors the external report exactly: the protocol lets a user add value against a position (here: pay underlyings for tranche debt) without checking whether the position is still worth that price — the user loses the added funds.

### Impact Explanation
A fulfiller loses up to the entire `_underlyings` amount paid. Example: an LP requests `10000e18` tranches for `10000e6` USDC while the epoch runs; a mid-epoch loss or borrower default cuts the AA tranche price by X%; fulfilling still transfers ~`10000e6` USDC for tokens redeemable at only `(1 - X) * 10000e6`, a direct, immediate loss of `X * 10000e6` to the fulfiller, with the excess going to the lender (and `feeReceiver` skimming its fee on the stale amount). On a hard default the tokens can be worth ~0, so the loss approaches 100%.

### Likelihood Explanation
Requires a pending write-off request at the moment of a loss event or default, plus a fulfiller (borrower or third party) transacting on stale off-chain information — the exact "write-off fulfiller" actor contemplated by the design. Since fulfillment is permissionless and the escrow exposes no freshness check, this is a realistic footgun rather than an exotic path, though it depends on an epoch loss/default occurring while requests are open.

### Recommendation
Mirror the epoch-state gate used in `createWriteOffRequest` and re-validate token value at fulfillment time:

```solidity
function fullfillWriteOffRequest(address _user, uint256 _tranches, uint256 _underlyings) external nonReentrant {
    if (!IdleCDOEpochVariant(idleCDOEpoch).isEpochRunning()) revert EpochNotRunning();
    // existing checks ...
}
```

Optionally also revert when the CDO is `paused()`/`defaulted`, or recompute the tranche tokens' current underlying value and require `_underlyings` to be consistent with it, so fulfillment can never execute at a price decoupled from the post-loss reality.

### Proof of Concept
Foundry fork sketch (base on `test/foundry/IdleCreditVaultWriteOffEscrow.t.sol`):

```solidity
function testFulfillAfterDefaultPaysStalePrice() external {
    // epoch running; LP escrows tranches for 10000 USDC
    vm.prank(LP);
    escrow.createWriteOffRequest(10000e18, 10000e6);

    // borrower fails to fund instant withdrawals / misses repayment
    // manager triggers default:
    vm.prank(manager);
    cdoEpoch.getInstantWithdrawFunds(); // falls into _handleBorrowerDefault
    assertTrue(cdoEpoch.defaulted());
    assertFalse(cdoEpoch.isEpochRunning());

    // tranche tokens are now impaired (only default-recovery claims)
    // yet fulfill still executes at the pre-default price
    deal(address(underlying), buyer, 10000e6);
    vm.startPrank(buyer);
    underlying.approve(address(escrow), 10000e6);
    escrow.fullfillWriteOffRequest(LP, 10000e18, 10000e6); // succeeds
    vm.stopPrank();

    // buyer paid ~10000e6 for tranche tokens worth ~0 in recovery
    assertEq(underlying.balanceOf(buyer), 0);
    assertEq(tranche.balanceOf(buyer), 10000e18); // worthless
}
```

Variant: instead of default, stop the epoch via `stopEpochWithDuration` with a `_lossAmount` that partially haircuts the tranche, then fulfill — the buyer still pays the full stale `underlyings` for tokens priced at the post-loss `priceAA`/`priceBB`.