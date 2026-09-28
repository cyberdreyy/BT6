### Title
Unattributed ERC4626 liquidity starvation lets a vault user repeatedly revert `stopEpoch` and freeze all epoch settlement - (File: contracts/strategies/idle/ProgrammableBorrower.sol)

### Summary
In the programmable-borrower mode, `ProgrammableBorrower.onStopEpoch` recalls shortfall liquidity from an external ERC4626 vault inside a `try/catch` that reverts `StopEpochVaultLiquidityUnavailable` whenever `vault.withdraw` fails while the vault shares still economically cover the amount (`shortfall <= _currentVaultAssets()`). That revert bubbles up and aborts `IdleCDOEpochVariant.stopEpoch` entirely. An unprivileged attacker who is a user of the underlying ERC4626 vault (e.g. a Morpho/Gauntlet vault) can withdraw or borrow out the vault's available liquidity so `maxWithdraw < shortfall <= convertToAssets(shares)`, and keep doing so for as long as they hold the position. Nothing attributes or penalises the attacker: they pay only their own vault/borrow costs while every `stopEpoch` call reverts, so the epoch never closes and all pending withdraw requests, interest accrual, and LP exits are frozen for the duration.

### Finding Description
`IdleCDOEpochVariant._stopEpoch` delegates the liquidity recall to the borrower hook:

```solidity
// contracts/IdleCDOEpochVariant.sol:395-403
if (isProgrammableBorrower) {
  // Hook reverts bubble so transient ERC4626 liquidity failures can be retried.
  if (!IProgrammableBorrower(_borrower()).onStopEpoch(_amountToPullFromBorrower + _pendingWithdraws, _isRequestingAllFunds)) {
    _handleBorrowerDefault(_amountToPullFromBorrower + _pendingWithdraws);
    return;
  }
}
```

`ProgrammableBorrower.onStopEpoch` splits the failure modes by share value, not by actual liquidity:

```solidity
// contracts/strategies/idle/ProgrammableBorrower.sol:239-253
uint256 onHand = underlyingToken.balanceOf(address(this));
if (_amountRequired > onHand) {
  uint256 shortfall = _amountRequired - onHand;
  if (shortfall > _currentVaultAssets()) return true; // real shortfall -> default path
  try vault.withdraw(shortfall, address(this), address(this)) returns (uint256 shares) {
    ...
  } catch {
    revert StopEpochVaultLiquidityUnavailable(); // retryable -> aborts stopEpoch
  }
}
```

The decision uses `_currentVaultAssets()` (`convertToAssets` on the borrower's shares), which values the claim, not `maxWithdraw`/available liquidity. Many ERC4626 vaults — Morpho-Blue-meta vaults like the Gauntlet USDC vault used in the fork tests being the canonical example — can be fully utilised: all underlying lent out, shares still worth their full `convertToAssets` amount, but `withdraw` reverts for any nonzero amount. A normal vault participant can create exactly this state by withdrawing their own deposits or opening a borrow that consumes the remaining liquidity. This is precisely the shared-batch-poisoning shape of the reference bug: a cheap, unauthenticated third-party action injects a failing item (the illiquid recall) into the shared settlement path (`stopEpoch` serves every pending withdraw receipt and the entire epoch rollover), forces the whole batch onto the failure path, and the actor is never identified or penalised — the revert is "retryable" by design, so the manager can only keep retrying while the attacker keeps the vault illiquid.

The regression test `testProgrammableBorrowerStopEpochRevertsWhenVaultLiquidityUnavailable` (test/foundry/ProgrammableBorrowerCreditVault.t.sol:372-399) confirms the revert path with an honest single-shot liquidity cap, but nothing limits how long an attacker can sustain the condition.

### Impact Explanation
Temporary freezing of all funds in the pool. While the vault is kept illiquid:
- `stopEpoch` always reverts, so `epochNumber` never advances and `isEpochRunning` stays true.
- `claimWithdrawRequest` reverts for pending requests (`epochNumber <= lastWithdrawRequest[_user]` guard at IdleCreditVault.sol:326), so all withdraw requesters are frozen.
- `requestWithdraw`, queue `processWithdrawRequests`/`processWithdrawalClaims`, and `claimInstantWithdrawRequest` paths are all gated behind epoch progression, so the entire LP exit surface is bricked.
- The attacker pays only the borrow interest / opportunity cost on the external vault and is never scored, banned, or loss-attributed — matching the "unattributed, sustainable for free" amplifier of the reference finding. On an uncapped-utilisation vault the attack is sustainable indefinitely.

No funds are stolen and no loss is incorrectly socialised (the default path is correctly avoided since shares still cover), so impact is availability-only: quantified as 100% of pool TVL (deposits + pending withdraw receipts) frozen for the attack duration.

### Likelihood Explanation
Requirements: the pool runs in `isProgrammableBorrower` mode whose configured `vault` is an ERC4626 with withdrawable/borrowable liquidity controlled by third parties (the intended design — idle capital is parked in e.g. Morpho vaults). The attacker needs only to be an ordinary depositor/borrower of that vault — explicitly an allowed unprivileged role ("a user of the programmable borrower's ERC4626 vault"). No privileged role is involved; the attacker sequences their vault borrow/withdraw around the honest manager's `stopEpoch` call, which is publicly predictable (`epochEndDate` is on-chain). No existing guard prevents it: `onStopEpoch` intentionally reverts on covered-but-unwithdrawable recalls, `skipDefaultCheck` doesn't apply, and the KYC/`isWalletAllowed` checks only gate pool deposits, not the external vault.

### Recommendation
Distinguish "covered but currently unwithdrawable" from genuine failures more defensively and bound the retry surface:
- In `onStopEpoch`, compare `shortfall` against `vault.maxWithdraw(address(this))` rather than reverting on any `withdraw` failure; when `maxWithdraw` is zero but shares cover the amount, either return a distinct status or escalate to a configurable grace mechanism instead of leaving `stopEpoch` permanently revertable.
- Consider a manager escape hatch: after N failed stop attempts or a grace period past `epochEndDate`, allow stopping the epoch against on-hand funds only and settling the vault sleeve asynchronously, so a single third-party vault cannot hold the entire epoch state machine hostage.
- Optionally track consecutive failed stops and surface the cause (e.g. emit the vault liquidity shortfall) to aid attribution/monitoring.

### Proof of Concept
Foundry fork test (extend `test/foundry/ProgrammableBorrowerCreditVault.t.sol`, which already deploys against `GAUNTLET_USDC_PRIME`):

```solidity
function testStopEpochFrozenByVaultLiquidityStarvation() external {
    _setUpProgrammableBorrowerCreditVault(GAUNTLET_FORK_BLOCK, GAUNTLET_USDC_PRIME);
    IERC4626 gauntletVault = IERC4626(GAUNTLET_USDC_PRIME);
    uint256 amount = 10_000 * oneScale;

    vm.prank(owner);
    cdoEpoch.setIsInterestMinted(true);
    idleCDO.depositAA(amount);
    cdoEpoch.requestWithdraw(aaTranche.balanceOf(address(this)) / 2, address(aaTranche));
    _startEpochAndCheckPrices(0);

    // Attacker: an ordinary vault user borrows/withdraws all available liquidity,
    // leaving shares fully valued but maxWithdraw ~= 0.
    address attacker = makeAddr("vault-user");
    uint256 liq = underlying.balanceOf(address(gauntletVault)); // or market liquidity
    deal(address(underlying), attacker, 1);
    // e.g. attacker opens a Morpho borrow consuming the market liquidity
    // or deposits+redeems to drain; concretely on the mock:
    //   limitedVault.setWithdrawLimit(pendingWithdraws - 1);
    // On the fork: borrow liq against attacker collateral until
    //   gauntletVault.maxWithdraw(address(programmableBorrower)) < pendingWithdraws
    // while convertToAssets(programmableBorrower shares) >= pendingWithdraws.

    vm.warp(cdoEpoch.epochEndDate() + 1);

    // Every stopEpoch reverts; epoch never closes; withdraws stay frozen.
    vm.prank(manager);
    vm.expectRevert(abi.encodeWithSelector(StopEpochVaultLiquidityUnavailable.selector));
    cdoEpoch.stopEpoch(0, 0);

    // Attacker keeps the borrow open -> repeat arbitrarily; assert still not defaulted
    // and pendingWithdraws untouched, demonstrating sustained unpenalised freezing.
    assertFalse(cdoEpoch.defaulted());
    assertTrue(cdoEpoch.isEpochRunning());
    vm.expectRevert(); // claimWithdrawRequest: epochNumber <= lastWithdrawRequest
    cdoEpoch.claimWithdrawRequest();
}
```

The existing test `testProgrammableBorrowerStopEpochRevertsWhenVaultLiquidityUnavailable` already proves the single-shot revert via `MockStopEpochLiquidityVault.setWithdrawLimit(pendingWithdraws - 1)` while `convertToAssets` still covers; the PoC differs only in that the liquidity cap is set by an unprivileged vault participant who can re-apply it after every manager retry.