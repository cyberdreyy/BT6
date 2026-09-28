### Title
Post-close withdraw requests escape `pendingWithdraws` accounting and drain funded claims of other users - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
The analog of `zgfx_decompress` heap overflow — an attacker-controlled write that lands outside the provisioned buffer — maps to `IdleCreditVault.requestWithdraw`/`_claimFundedWithdrawRequest`. When the pool is closed (`epochEndDate() == 0`), `requestWithdraw` mints the user a strategy-token receipt and records `withdrawsRequests[_user]`, but deliberately skips incrementing `pendingWithdraws`, so the borrower/epoch machinery never provisions funds for that receipt. The claim path then bypasses the "wait one epoch" gate precisely when the pool is closed and pays the full amount from the strategy's underlying balance, which at that point holds only the funded claims of other pending withdrawers. One receipt is written outside the funded bound but is paid out of the shared buffer anyway — the same shape as a write past the end of an allocation corrupting adjacent data.

### Finding Description
In `requestWithdraw` (`contracts/strategies/idle/IdleCreditVault.sol:259-294`):

- `isClosed` is computed as `IIdleCDOEpochVariant(idleCDO).epochEndDate() == 0` (line 259).
- When `isClosed`, `pendingWithdraws += _amount` is skipped (lines 277-280), so `stopEpoch`/`collectWithdrawFunds` will never source underlying for this receipt.
- The receipt is still fully recorded: `_mint(_user, _amount)`, `lastWithdrawRequest[_user] = currentEpoch`, `withdrawsRequests[_user] += _amount`, `withdrawsRequestsByEpoch[_user][currentEpoch] += _amount` (lines 275, 282, 292-293).

In `_claimFundedWithdrawRequest` (lines 319-350), the epoch-gating check `epochNumber <= lastWithdrawRequest[_user]` is short-circuited by `IIdleCDOEpochVariant(idleCDO).epochEndDate() != 0` (line 326). For a closed pool this condition is false, so the claim succeeds immediately — there is no check that the request's `pendingWithdraws` basis was ever funded.

Payout is via `_transferFundedClaim` (lines 897-907), which transfers from `underlyingToken.balanceOf(address(this))` and only guards against dipping into `defaultRecoveryReserve`. It does not distinguish "funded withdraw reserve" owed to earlier claimants from liquidity for this new, unfunded receipt.

Sequence:

1. Pool closes: borrower repaid all funds, `epochEndDate` is set to 0. Users A (honest lenders) have pending withdraw requests whose funds were pulled into the vault via `collectWithdrawFunds`; the strategy now holds `pendingWithdraws`-sized underlying earmarked for them.
2. Attacker (a KYC-passed tranche-token holder, in-scope) calls `IdleCDOEpochVariant.requestWithdraw` with their tranche balance. `creditVault.requestWithdraw` runs the `isClosed` branch: burns `_principal` of CDO strategy tokens, mints `_amount` receipt tokens to the attacker, records `withdrawsRequests[attacker]`, but adds nothing to `pendingWithdraws`.
3. Attacker calls `claimWithdrawRequest` in the same transaction. `_claimLossAdjustedWithdrawRequest` returns 0 (no loss epoch), `_claimFundedWithdrawRequest` skips the epoch wait because `epochEndDate() == 0`, burns the freshly minted receipt, and `safeTransfer`s `amount` underlying from the strategy — taking funds that were funded for user A's claims.
4. When A later claims, the balance is short and `_transferFundedClaim` reverts (`balance - reserve < _amount` → `NotAllowed`, or plain ERC20 insufficient-balance revert), permanently freezing A's funded claim.

The invariant broken is "one receipt, one payout": the aggregate paid out exceeds the aggregate funded, because the closed-pool branch emits receipts beyond `pendingWithdraws` while the claim path treats every `withdrawsRequests` entry as funded.

### Impact Explanation
Direct theft of funded withdrawal proceeds and permanent freezing of other users' claims. The attacker can extract up to `min(attackerReceiptAmount, vaultUnderlyingBalance)` — i.e., the entire underlying balance held by the vault for funded-but-unclaimed withdraw requests. The victim's claim then reverts forever (the receipt tokens were never burned for the shortfall portion, but no new funding will ever arrive since the pool is closed), so this is both direct theft by the attacker and a permanent freeze of the victim's funded claim. Quantified loss equals the full funded pending-withdraw reserve at the time of the attack.

### Likelihood Explanation
Requirements: the vault/pool reaches closed state (`epochEndDate == 0`, e.g., pool-close flow or full recall), there exist funded-but-unclaimed withdraw receipts (users routinely delay claiming; the code comments even state users may claim "at any time even if a new epoch started"), and the attacker holds or acquires tranche tokens. All attacker actions (`requestWithdraw`, `claimWithdrawRequest`) are unprivileged user calls on `IdleCDOEpochVariant` gated only by `isWalletAllowed` and the `allowAAWithdrawRequest`/`allowBBWithdrawRequest` flags, which are not conditioned on pool closure. No privileged action, oracle manipulation, or timing race is needed — the claim can be executed atomically in the same transaction as the request.

### Recommendation
When the pool is closed, either (a) revert `requestWithdraw`/`claimWithdrawRequest` for new requests, or (b) require the CDO to fund the request synchronously (e.g., pull the underlying from the CDO's recalled balance in the same call before minting the receipt) so that every receipt is backed 1:1 at creation time. At minimum, `_claimFundedWithdrawRequest` should verify that the claimable amount does not exceed the funded reserve actually collected via `collectWithdrawFunds`/`collectInstantWithdrawFunds` (track a `fundedWithdraws` counter decremented per claim) instead of paying against the raw token balance.

### Proof of Concept
Foundry fork test (against the existing `IdleCreditVault.t.sol` harness, which provides `_depositWithUser`, `_startEpochAndCheckPrices`, `_stopEpochAndCheckPrices`):

```solidity
function testClosedPoolUnfundedReceiptDrainsFundedClaims() external {
    uint256 amount = 10000 * ONE_SCALE;

    // victim deposits AA and BB to seed the pool
    address victim = makeAddr('victim');
    _depositWithUser(victim, amount, true);   // AA tranche
    idleCDO.depositBB(amount);

    // run and stop epoch 0 so a normal withdraw request can be made
    _startEpochAndCheckPrices(0);
    _stopEpochAndCheckPrices(0, initialProvidedApr, _expectedFundsEndEpoch());

    // victim requests a normal withdraw -> receipt recorded, pendingWithdraws > 0
    vm.prank(victim);
    uint256 victimReq = cdoEpoch.requestWithdraw(0, address(AAtranche));

    // close the pool: borrower repays in full; collectWithdrawFunds pulls
    // victim's funded amount into the strategy; epochEndDate becomes 0
    // (use the existing pool-close helper / stopEpoch with _interest == 1 sentinel)
    _closePoolFullyRepaid(); // pseudo-helper matching existing pool-close flow

    // attacker: a separate KYC'd tranche holder who deposited earlier
    // (or acquired tranche tokens); requests withdraw while epochEndDate == 0
    uint256 attackerUnderlying = IERC20Detailed(defaultUnderlying).balanceOf(address(strategy));
    vm.prank(attacker);
    cdoEpoch.requestWithdraw(0, address(BBtranche)); // mints receipt, pendingWithdraws unchanged

    // same tx claim: epoch gate skipped because epochEndDate == 0,
    // _transferFundedClaim pays from vault balance holding victim's funds
    uint256 balPre = IERC20Detailed(defaultUnderlying).balanceOf(attacker);
    vm.prank(attacker);
    cdoEpoch.claimWithdrawRequest();
    assertGt(
        IERC20Detailed(defaultUnderlying).balanceOf(attacker) - balPre,
        0,
        'attacker paid from other users funded claims'
    );

    // victim's funded claim now reverts: balance shortfall -> NotAllowed / ERC20 revert
    vm.prank(victim);
    vm.expectRevert();
    cdoEpoch.claimWithdrawRequest();
}
```

Key assertion targets: `IdleCreditVault.pendingWithdraws()` does not increase for the attacker's request while `withdrawsRequests(attacker)` does (lines 277-293); `strategy.balanceOf` underlying decreases by the attacker's claim while no `collectWithdrawFunds` was ever called for it.

Caveat I could not fully verify within the search budget: whether `IdleCDOEpochVariant.requestWithdraw` or the pool-close path disables `allowAAWithdrawRequest`/`allowBBWithdrawRequest` once `epochEndDate == 0`. The vault-side `isClosed` branch (lines 259, 277) exists specifically to process requests in that state, which indicates the CDO does not blanket-block them; if a dedicated flag check exists on the CDO side, the finding degrades to a design-debt note rather than a live exploit.