### Title
`depositDuringEpoch` + `requestWithdraw` round-trip mints unearned epoch interest every epoch at no cost - (contracts/IdleCDOEpochVariant.sol)

### Summary
A KYC'd lender can deposit mid-epoch via `depositDuringEpoch` and immediately request a withdrawal in the following buffer period, cycling capital each epoch to harvest interest for time the funds were never actually lent. The minting formula credits the depositor for the full `bufferPeriod` plus remaining epoch time, and the withdraw receipt then pays an additional full-epoch interest share minus only upfront fees. When `fee`/`managementFee` are low (or in minted-interest mode where interest is created via `mintStrategyTokens`), the net per-cycle gain is positive, repeatable, and paid out of `pendingWithdraws` cash / minted NAV — i.e. diluted from honest LPs — mirroring the FlatMoney "free points" loop.

### Finding Description
`depositDuringEpoch` mints tranche shares using a discounted price that embeds `trancheInterest` computed as `interest * (remaining + buffer) / (epochDuration + buffer)` — note the **full** `bufferPeriod` is credited regardless of how late the deposit occurs (contracts/IdleCDOEpochVariant.sol:691-697). A deposit one block before `epochEndDate` therefore receives shares priced to return `amount + trancheInterest` at epoch end even though the capital was only exposed for ~1 block (line 724).

After `stopEpoch` reopens requests (`allowAAWithdrawRequest = true`, line 481-482), the attacker calls `requestWithdraw`. `_trancheToUnderlyings` converts the shares at the now-realized price, so `principal ≈ amount + trancheInterest` (line 756). Then `_calcInterestWithdrawRequest` adds a **full next-epoch** interest share on that inflated principal (lines 865-870), and `_totalWithdrawFees` subtracts only the upfront management fee plus performance fee (lines 900-906). With `fee`/`managementFee` configured low — or in `isInterestMinted` mode where epoch interest is minted via `mintStrategyTokens` (lines 428-433, 466) rather than paid in cash by the borrower — `principal + interest - totalFees > amount`.

The receipt is claimable at the next `stopEpoch` from `pendingWithdraws`, which in minted mode is funded from the borrower's returned principal and the minted-NAV backing shared by all LPs (lines 376, 393, 408-410). The attacker's capital is then free to re-enter via `depositDuringEpoch` in the next running epoch and repeat.

### Impact Explanation
Each cycle extracts `trancheInterest + (receipt interest - upfront fees)` in excess of the deposited amount, with capital at risk only for `remaining` seconds of the epoch instead of `epochDuration + bufferPeriod`. Repeated every epoch, this siphons yield/minted NAV that honest LPs (who keep funds deployed the entire epoch) would otherwise receive — a direct, quantifiable dilution/theft of yield, unbounded by any rate limit. The privileged roles (owner/manager stopping epochs, borrower repaying) behave honestly; the attack only requires a whitelisted wallet.

### Likelihood Explanation
Requires only `isWalletAllowed(msg.sender)` and that `isDepositDuringEpochDisabled`, `isAYSActive`, and `isProgrammableBorrower` are false — the default fixed-APR, non-AYS configuration. Profitability is positive whenever `_totalWithdrawFees` is less than the receipt interest plus the embedded deposit interest, which holds for low fee configurations and is especially acute in minted-interest mode where no real cash interest constrains the payout. No existing guard stops it: `_skimDonatedAssets`, `_guarded`, and the epoch flags do not prevent a mid-epoch deposit followed by a buffer-period withdraw request.

### Recommendation
- Proportionalize the buffer credit: mint shares crediting only `remaining` time plus the buffer fraction actually overlapping the deposit (or require deposits to have been held a minimum period before the embed-interest discount applies).
- Subtract already-embedded deposit interest from the withdraw receipt, or base `_calcInterestWithdrawRequest` on the original deposited principal rather than the price-appreciated `_trancheToUnderlyings` value.
- Alternatively, disallow `requestWithdraw` for shares minted via `depositDuringEpoch` until one full epoch has elapsed since deposit.

### Proof of Concept
Foundry fork PoC outline (setup mirrors `test/foundry/IdleCreditVault.t.sol`):

```solidity
// Setup: fixed-APR IdleCDOEpochVariant, isInterestMinted = true (or low fee/managementFee),
// epoch running after _startEpochAndCheckPrices().
function testDepositWithdrawCycleMintsUnearnedInterest() external {
    uint256 amount = 100_000 * ONE_SCALE;
    address attacker = makeAddr('attacker'); // Keyring-whitelisted
    _depositWithUser(attacker, amount, true); // seed tranche supply, start epoch

    // 1) Warp to just before epochEndDate
    vm.warp(cdoEpoch.epochEndDate() - 1);
    vm.startPrank(attacker);
    uint256 balBefore = token.balanceOf(attacker);
    uint256 minted = cdoEpoch.depositDuringEpoch(amount, idleCDO.AATranche());
    vm.stopPrank();

    // 2) Honest manager stops the epoch; withdraw requests reopen
    _stopEpochAndCheckPrices(0, newApr, expectedFunds);

    // 3) Attacker requests withdraw of all shares in the buffer period
    vm.prank(attacker);
    uint256 requested = cdoEpoch.requestWithdraw(minted, idleCDO.AATranche());

    // requested = principal + full next-epoch interest - upfront fees
    // principal already embeds buffer+remaining interest from the discounted mint
    assertGt(requested, amount, 'unearned interest minted');
}
```
Run one epoch cycle, claim via `claimWithdrawRequest` at the next `stopEpoch`, and compare `token.balanceOf(attacker)` to `balBefore` to show positive net extraction per cycle.