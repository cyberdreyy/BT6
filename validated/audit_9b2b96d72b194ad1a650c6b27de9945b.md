### Title
ERC4626 share-price inflation in `ProgrammableBorrower._depositToVault` lets a vault depositor steal pooled lender funds - ([File: contracts/strategies/idle/ProgrammableBorrower.sol])

### Summary
`ProgrammableBorrower` parks all idle pool funds in an external ERC4626 `vault` and trusts `vault.deposit`/`convertToAssets` output for its epoch accounting baseline (`epochStartVaultAssets`, `_currentVaultAssets`, `_vaultNetInterest`). There is no minimum-shares check, no `deposit` slippage guard, and no virtual-shares requirement on the configured vault. Analogous to the wrangler `--commit-hash` injection — where an attacker-controlled string is interpolated into a privileged execution context — here an attacker-controlled value (the vault share price, manipulable by any unprivileged vault depositor via the classic first-deposit/donation inflation) is injected directly into the facility's privileged interest and principal accounting.

### Finding Description
`_depositToVault` (called from `onStartEpoch` with the full on-hand balance, `contracts/strategies/idle/ProgrammableBorrower.sol:216`, and from `_repay`, line 499/527) performs `vault.deposit(_assetAmount, address(this))` and records `epochStartVaultAssets` from the pre-deposit asset snapshot, not from shares actually received:

```solidity
uint256 startAssets = underlyingToken.balanceOf(address(this)) + currentVaultAssets;
_depositToVault(underlyingToken.balanceOf(address(this)), 0);
epochStartVaultAssets = startAssets;
```

If the vault is susceptible to share inflation (empty/fresh vault — the config only checks `asset()` matches, `initialize` line 119), an attacker can:
1. Deposit 1 wei as first depositor, minting 1 share.
2. Donate a large amount of underlying directly to the vault, inflating `convertToAssets(1)` to ~D.
3. Wait for `onStartEpoch`/`_depositToVault` (or a `_repay` during an active epoch) to deposit pool principal P. With share price ≈ D, the facility receives `floor(P/D)` shares — attacker chooses D so the facility gets 0–1 shares for a large deposit.
4. Redeem the attacker's share, recovering donation plus a fraction of P (up to ~P/2 profit in the standard OpenZeppelin-style rounding split).

The accounting then makes it worse: `epochStartVaultAssets` was snapshotted as the full pre-deposit asset value, so `_vaultNetInterest` records the stranded deposit as a vault `loss` (lines 337–347), driving `totalInterestDueNow()` to 0. At `onStopEpoch`, if `shortfall > _currentVaultAssets()` the hook returns `true` (line 245) and IdleCDO's `transferFrom` fails, pushing the pool onto the default path where the loss is socialized to BB tranche holders — even though the funds were stolen, not lost to market risk.

### Impact Explanation
Direct theft of lender principal: the attacker redeems vault shares and pockets the rounding-trapped portion of each pool deposit (each `_depositToVault` call during the epoch — `onStartEpoch` idle balance and every `_repay` re-deposit — is a separate injection point). Residual loss falls on BB tranche holders via the default waterfall. Quantified: for deposit P and inflation D, attacker profit ≈ P − ceil(P/D)·(redeemed value); with P = 10M USDC a ≈5M USDC-denominated profit is achievable against a fresh vault with negligible upfront cost beyond the temporary donation.

### Likelihood Explanation
The vault address is only constrained by `IERC4626(_vault).asset() == underlyingToken` (`initialize`, `setVault` lines 158–168). No check enforces a vault with virtual shares/decimals offset (OpenZeppelin ≥4.9 style), an existing supply floor, or a minimum first deposit. If the facility is ever pointed at a fresh ERC4626 (a legitimate deployment pattern for isolated yield sleeves), any unprivileged EOA — explicitly an allowed attacker ("a user of the programmable borrower's ERC4626 vault") — can be the first depositor and execute the inflation before `onStartEpoch`. Even on a non-empty vault, a flash-loaned donation can inflate the share price for a single transaction in vaults without inflation protection.

### Recommendation
- Require the configured vault to implement inflation protection (OpenZeppelin ERC4626 with decimals offset / virtual shares) at `initialize`/`setVault`, e.g. verify `totalSupply() > MIN_SUPPLY` and attempt a probe deposit.
- Pass a `minShares` bound: replace `vault.deposit(_assetAmount, address(this))` with a check that `shares >= _assetAmount * minRatio` (or use `previewDeposit` and revert on deviation beyond a few bps).
- Snapshot `epochStartVaultAssets` from `convertToAssets(shares received) + prior position` rather than the pre-deposit balance, so a rounding-trapped deposit cannot be booked as principal.

### Proof of Concept
Foundry fork test sketch (against a fork block where the facility and a fresh ERC4626 vault exist):

```solidity
// 1. Attacker (any EOA) is first depositor of `vault`
underlying.approve(address(vault), 1);
vault.deposit(1, attacker);              // 1 share
uint256 D = 20_000_000e6;
underlying.transfer(address(vault), D);  // donation inflates share price

// 2. Honest owner starts epoch -> onStartEpoch deposits idle pool balance P
cdo.startEpoch();                        // ProgrammableBorrower._depositToVault(P, 0)
uint256 pbShares = vault.balanceOf(address(pb));
assertEq(pbShares, P / D);               // e.g. P=10M -> 0 shares

// 3. Attacker redeems, recovering D + ~P
vault.redeem(vault.balanceOf(attacker), attacker, attacker);
assertGt(underlying.balanceOf(attacker), D + P / 2);

// 4. stopEpoch: shortfall > _currentVaultAssets() -> transferFrom fails -> default path
cdo.stopEpoch();                         // _handleBorrowerDefault socializes loss to BB
```