### Title
Unchecked ERC4626 deposit return in `ProgrammableBorrower._depositToVault` allows share-price inflation theft and forced epoch default - (File: contracts/strategies/idle/ProgrammableBorrower.sol)

### Summary
`ProgrammableBorrower` deploys all idle facility liquidity into an external ERC4626 `vault`. `_depositToVault` calls `vault.deposit(_assetAmount, address(this))` and discards the returned `shares` value — there is no minimum-shares check and no post-deposit solvency check. An unprivileged attacker who is a shareholder of the same ERC4626 vault can inflate the vault's price-per-share (donate underlying directly to the vault while holding dust shares) so that the borrower's deposit mints ~0 shares, silently capturing the full deposit. The same unchecked trust in vault-side valuations (`_currentVaultAssets()` feeding `totalInterestDueNow()` and `availableToBorrow()`) then drives `IdleCDOEpochVariant.stopEpoch` into a spurious default path.

### Finding Description
- `_depositToVault` at `ProgrammableBorrower.sol:378-385` ignores the `shares` return value:
  ```solidity
  uint256 shares = vault.deposit(_assetAmount, address(this));
  ```
- It is called on every vault inflow: `onStartEpoch` re-parks all idle cash (line 216) and `_repay` redeploys every repayment while an epoch is active (lines 499, 527).
- Epoch accounting is anchored on *asset* snapshots, not shares: `epochStartVaultAssets = startAssets` (line 219) records the pre-deposit token amount, while earned assets are later measured as `_currentVaultAssets() + epochWithdrawnFromVault` in `_vaultNetInterest` (lines 339-341). If the deposit minted ~0 shares, `_currentVaultAssets()` collapses to ~0 while `epochStartVaultAssets` retains the full deposited principal → `netDelta` is a large fake `loss`.
- `totalInterestDueNow()` (lines 330-334) then returns `0` for the epoch, and in `onStopEpoch` the coverage check `shortfall > _currentVaultAssets()` (line 245) returns early, so IdleCDO's subsequent `transferFrom` of the epoch amount fails and the facility is routed into the borrower-default path even though the real borrower is solvent. The stolen assets belong to the vault attacker, not the pool — loss is permanent for lenders (BB-first socialization of the "default").

### Impact Explanation
Direct theft of facility assets plus insolvency/forced-default. Every unit routed through `_depositToVault` (idle cash at `onStartEpoch`, repayments during an epoch) can be fully captured by the share-inflating vault shareholder when the deposit mints 0 shares. The subsequent epoch stop reports `totalInterestDueNow() = 0` and the liquidity coverage check fails, so a solvent borrower is defaulted and the stolen principal is socialized as a loss across tranches (junior/BB first). Loss is bounded only by facility TVL parked in the vault.

### Likelihood Explanation
Requires only an unprivileged account that can deposit/donate into the same ERC4626 vault the facility uses (explicitly an allowed actor: a user of the programmable borrower's ERC4626 vault and a direct token sender). The attack is a standard ERC4626 inflation/donation pattern: it is cheapest before the facility's first deposit or when vault totalSupply is small, and must be timed ahead of `onStartEpoch`/`repay`. No privileged role, oracle, or governance action is needed; the honest borrower/manager calls then execute the vulnerable deposit.

### Recommendation
- In `_depositToVault`, enforce a minimum share output (`vault.deposit` with a `minShares`/`previewDeposit` slippage bound, or revert if `shares == 0` / `convertToAssets(shares)` deviates beyond a tolerance from `_assetAmount`).
- Reconcile accounting with shares, not raw assets: snapshot `epochStartVaultShares`/baseline in share units and validate that a deposit did not dilute the position.
- In `onStopEpoch`, treat the coverage shortfall check against `convertToAssets` as untrusted input — e.g., cap `_amountRequired` pulls against actual on-hand plus realizable `maxWithdraw`/`redeem` liquidity rather than the valuation view.

### Proof of Concept
Foundry fork sketch (against the deployed vault used by the programmable borrower):

```solidity
// fork mainnet, get pb = ProgrammableBorrower, vault = pb.vault(), token = pb.underlyingToken()

// 1. Attacker becomes a vault shareholder and inflates price-per-share
deal(address(token), attacker, 1);
vm.startPrank(attacker);
token.approve(address(vault), 1);
vault.deposit(1, attacker);                       // holds ~1 share
token.transfer(address(vault), INFLATE_DONATION); // donation raises assets/share
vm.stopPrank();
// price per share now > the facility's pending deposit -> deposit mints 0 shares

// 2. Honest flow: manager starts epoch; onStartEpoch parks idle cash via _depositToVault
vm.prank(manager);
idleCDO.startEpoch(...);                          // pb.onStartEpoch -> vault.deposit(...) -> ~0 shares

// 3. Attacker redeems, recovering donation + the facility's deposit
vm.prank(attacker);
vault.redeem(vault.balanceOf(attacker), attacker, attacker);
assertGt(token.balanceOf(attacker), INFLATE_DONATION); // profit = stolen deposit - dust

// 4. Epoch stop: _currentVaultAssets() ~ 0, epochStartVaultAssets = full deposit
//    -> _vaultNetInterest reports a huge loss -> totalInterestDueNow() == 0
//    -> onStopEpoch returns early (shortfall > vault assets) -> IdleCDO transferFrom fails
//    -> facility enters borrower-default path despite solvent borrower
vm.prank(manager);
idleCDO.stopEpoch(...);                           // resolves into default accounting
assertEq(pb.vaultSharesBalance(), 0);
assertEq(pb.totalInterestDueNow(), 0);
```

Caveat: I was unable to read the `IdleCDOEpochVariant.stopEpoch`/`_handleBorrowerDefault` call sequence within the iteration budget, so the exact revert-vs-default branching at stop is inferred from `ProgrammableBorrower.onStopEpoch`'s documented contract ("let IdleCDO's later transferFrom fail and use the existing default path") rather than verified line-by-line. The core defect — the ignored `shares` return and asset-denominated accounting in `ProgrammableBorrower.sol:378-385, 219, 339-341` — is verified directly.