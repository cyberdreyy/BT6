### Title
Unguarded ERC4626 `deposit` inside `onStartEpoch` lets any vault user permanently block `startEpoch` and freeze pool funds - (File: contracts/strategies/idle/ProgrammableBorrower.sol)

### Summary
`IdleCDOEpochVariant.startEpoch` calls `_startEpochProgrammableBorrower`, which performs an unguarded external call into `IProgrammableBorrower.onStartEpoch`. That hook internally calls `vault.deposit` on a third-party ERC4626 vault with no try/catch. Any unprivileged user of that shared vault can make `deposit` revert (e.g., by filling the vault's deposit/supply cap with their own funds), which bricks `startEpoch` and leaves LP funds stuck in the buffer phase — the same bug class as the StakingModule report (a core flow doing an unsafe external call whose revert DoSes the whole protocol).

### Finding Description
In `contracts/IdleCDOEpochVariant.sol`, `startEpoch` sends the epoch funds to the borrower via `sendFundsToBorrower` (wrapped in try/catch) and then calls `_startEpochProgrammableBorrower(_pendingWithdraws)`:

- `contracts/IdleCDOEpochVariant.sol:295-303` — `sendFundsToBorrower` is try/catch'd, but `_startEpochProgrammableBorrower` is not.
- `contracts/IdleCDOEpochVariant.sol:991-995` — `IProgrammableBorrower(_borrower()).onStartEpoch(_pendingWithdraws)` is a raw external call.
- `contracts/strategies/idle/ProgrammableBorrower.sol:216` — `onStartEpoch` calls `_depositToVault(underlyingToken.balanceOf(address(this)), 0)`, which at `ProgrammableBorrower.sol:380` executes `vault.deposit(_assetAmount, address(this))` with no error handling.

A revert anywhere in that chain (vault `deposit` reverting because `maxDeposit` is exhausted by an attacker, vault paused, vault reverting `convertToAssets` inside `onStartEpoch` at line 206) bubbles all the way up and reverts `startEpoch`. Unlike `stopEpoch`, where `onStopEpoch` failures were deliberately made retryable and transfer failures route to `_handleBorrowerDefault`, `startEpoch` has no failure path: funds were already transferred to the borrower contract but epoch accounting never activates, `isEpochRunning` stays false, `allowInstantWithdraw` never gets set, and pending withdraw requests can never be claimed.

Note the asymmetry: the developers already recognized this hazard — `sendFundsToBorrower` is wrapped in try/catch precisely because the borrower-side transfer can fail (`IdleCDOEpochVariant.sol:295-303`) — but the immediately following `onStartEpoch` hook, which performs a strictly riskier external call into a shared, permissionless ERC4626 vault, is left unguarded.

### Impact Explanation
Temporary freezing of all pool funds with quantified exposure equal to `totUnderlyings - pendingInstant` (the full epoch deployment, line 294). Once `sendFundsToBorrower` succeeds but `onStartEpoch` reverts, the pool sits in a stuck state: deposits were just disabled (`_pause()` at line 246 actually executes before the revert — the whole tx reverts, but subsequent `startEpoch` retries hit the same revert), withdraw requests are disabled and cannot mature because no epoch ever runs, and `claimInstantWithdrawRequest` is gated behind `allowInstantWithdraw` which is only set on a successful `startEpoch` (line 292). Recovery requires privileged intervention (`emergencyExitVault`/`setVault`/`rescueTokens` on ProgrammableBorrower, which itself reverts while `vault.balanceOf != 0` and epoch accounting state is inconsistent), i.e. funds are frozen for at least as long as the attacker maintains the vault-cap condition plus operator response — matching the "already existing users will lose access to their funds" impact of the source finding.

### Likelihood Explanation
The attacker is any unprivileged user of the ERC4626 vault that `ProgrammableBorrower` parks idle funds in — explicitly in scope ("a user of the programmable borrower's ERC4626 vault"). Many production ERC4626 vaults (Morpho-style curated vaults, capped vaults) enforce supply caps or have pause switches; filling remaining cap headroom costs the attacker only deposit fees and is fully reversible for them afterward. The attack is also griefable opportunistically: any transient deposit-block condition during the buffer window forces a failed `startEpoch` attempt after funds have moved. The honest borrower cannot prevent it because `onStartEpoch` unconditionally deposits the full on-hand balance.

### Recommendation
Mirror the `sendFundsToBorrower` pattern: wrap the `onStartEpoch` hook in try/catch inside `_startEpochProgrammableBorrower` (or make `onStartEpoch` catch vault `deposit` failures and keep the funds on-hand instead of reverting). A revert-safe deposit should fall back to holding the underlying on the contract (it still counts toward `epochStartVaultAssets`/`totalUnderlying`) and emit an error event, so a blocked vault deposit degrades to "funds undeployed" rather than freezing the entire epoch state machine.

### Proof of Concept
Foundry fork/unit sketch (adapt to existing `test/foundry/ProgrammableBorrowerCreditVault.t.sol` harness, which already deploys `cdoEpoch`, `programmableBorrower`, `strategy`, and a `morphoVault`/`vault` mock):

```solidity
function testStartEpochDoSViaVaultDepositCap() external {
    uint256 amount = 10_000 * oneScale;
    vm.prank(owner);
    cdoEpoch.setIsInterestMinted(true);

    idleCDO.depositAA(amount);           // LP deposits during buffer
    _startEpochAndCheckPrices(0);        // epoch 0 runs normally

    // epoch 0 stops; pool enters buffer, borrower contract holds idle underlying
    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.prank(manager);
    cdoEpoch.stopEpoch(0, 0);
    vm.warp(cdoEpoch.epochEndDate() + cdoEpoch.bufferPeriod() + 1);

    // Attacker: unprivileged ERC4626 vault user fills the vault's deposit cap
    uint256 headroom = vault.maxDeposit(address(programmableBorrower));
    deal(USDC, attacker, headroom);
    vm.startPrank(attacker);
    IERC20(USDC).approve(address(vault), headroom);
    vault.deposit(headroom, attacker);   // cap now exhausted; maxDeposit(pb) == 0
    vm.stopPrank();

    // startEpoch: sendFundsToBorrower succeeds, then onStartEpoch -> vault.deposit reverts
    vm.prank(manager);
    vm.expectRevert();                   // ERC4626 deposit-cap revert bubbles up
    cdoEpoch.startEpoch();

    // DoS confirmed: epoch never starts, claims stay frozen while attacker holds cap
    assertFalse(cdoEpoch.isEpochRunning());
    assertFalse(cdoEpoch.allowInstantWithdraw());
    // withdraw-request claimants cannot claim; LP funds sit locked in the borrower contract
}
```

For a generic revert path (no cap), a mock vault with `setRevertDeposit(true)` demonstrates the identical failure: `vault.deposit` revert → `onStartEpoch` revert → `startEpoch` revert, with no fallback or error log anywhere in the chain.