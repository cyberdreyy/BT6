### Title
`depositDuringEpoch` mints shares and disburses funds based on the raw `_amount` instead of the actually received balance delta - (contracts/IdleCDOEpochVariant.sol)

### Summary
The external bug class — value is checked/relied upon but the unsanitized caller input is what flows downstream — maps directly onto `depositDuringEpoch` in `IdleCDOEpochVariant.sol`. The sibling function `_deposit` in `IdleCDOCreditVault.sol` correctly mints shares from the *received* balance delta (`_contractTokenBalance(_token) - _preBal`), proving the codebase knows the correct pattern. `depositDuringEpoch` instead computes `_minted`, `mintStrategyTokens`, and the borrower disbursement all from the raw `_amount` parameter. For an underlying that does not deliver `_amount` on `transferFrom` (fee-on-transfer / deflationary ERC20), the vault mints full-price shares and sends the borrower tokens it never received.

### Finding Description
In `IdleCDOCreditVault._deposit` the mint is based on the received delta: `_transferUnderlyingsFrom(msg.sender, address(this), _amount)` is executed, then `_mintSharesAtCurrPrice(_contractTokenBalance(_token) - _preBal, ...)` is called — the "sanitized" value. `depositDuringEpoch` at `contracts/IdleCDOEpochVariant.sol:656-733` performs:

1. `_transferUnderlyingsFrom(msg.sender, address(this), _amount)` (line ~685) — receives `received <= _amount`.
2. `_minted = (_amount + trancheInterest) * _trancheTotSupply / expectedFinal` (line ~724) — uses raw `_amount`, not the received delta.
3. `_mintShares(_tranche, msg.sender, _minted, _amount)` (line ~725).
4. `IdleCreditVault(strategy).mintStrategyTokens(_amount)` (line ~730) — inflates `getContractValue`/`_managedContractValue` accounting by the full `_amount`.
5. `_transferUnderlyings(_borrower(), _amount)` (line ~732) — pushes the full `_amount` to the borrower even though only `received` arrived.

So the vault pays out (in tranche shares, strategy-token accounting, and hard tokens to the borrower) strictly more than it collected whenever `received < _amount`.

### Impact Explanation
With a fee-on-transfer underlying token (e.g. fee `f`), an unprivileged KYC-passed lender deposits `_amount`, the vault receives `_amount - f`, but:
- the attacker is minted shares priced on `_amount + trancheInterest`,
- `expectedEpochInterest`, `mintStrategyTokens(_amount)`, and the NAV base are all credited as if `_amount` arrived,
- the borrower is sent the full `_amount` from vault reserves (net of the epoch buffer logic), with the shortfall `f` covered by other depositors' funds.

The attacker then withdraws/redeems the inflated shares at epoch end (or claims via `requestWithdraw`/`claimWithdrawRequest`), extracting value equal to `f` per deposit plus the full prorated interest on phantom principal. Repeatable each epoch → direct theft of other tranche holders' NAV and vault insolvency by cumulative skimmed fees. In the extreme, once strategy-token accounting is inflated above real holdings, later withdrawals revert, permanently freezing remaining user funds behind the Default-only path.

### Likelihood Explanation
Reachable in the running-epoch phase by any `isWalletAllowed` lender in fixed-APR (non-AYS, non-programmable) deployments — `depositDuringEpoch` is explicitly enabled for them. The precondition is an underlying whose `transferFrom` delivers less than the requested amount (fee-on-transfer/rebasing tokens). Vaults are deployable via `IdleCreditVaultFactory` with arbitrary ERC20 underlyings, so this is configuration-dependent rather than hypothetical; the inconsistency with `_deposit`'s delta-based mint shows the delta pattern was intended but missed here. Severity is conditional on the token choice, hence Medium likelihood where applicable.

### Recommendation
Mirror the `_deposit` pattern: capture `uint256 _preBal = _contractTokenBalance(token)` before `_transferUnderlyingsFrom`, compute `received = _contractTokenBalance(token) - _preBal`, and use `received` (not `_amount`) in the interest calculation, the `_minted` formula, `_mintShares`, `mintStrategyTokens`, `expectedEpochInterest` accounting, and the borrower transfer — or explicitly `require` the underlying is non-deflationary and revert when `received != _amount`.

### Proof of Concept
Foundry fork-style test sketch (mock FoT token charging 1%):

```solidity
// Assume: vault = IdleCDOEpochVariant deployment, isEpochRunning == true,
// msg.sender is wallet-allowed, tranche has nonzero supply, AYS/programmable off.
MockFoTToken tok; // transferFrom moves 99% of amount
uint256 amount = 1_000_000e6;

uint256 vaultBalBefore = tok.balanceOf(address(vault));
uint256 sharesBefore   = tranche.balanceOf(attacker);

// attacker approves and calls depositDuringEpoch
tok.approve(address(vault), amount);
uint256 minted = vault.depositDuringEpoch(amount, address(aaTranche));

// vault only received 0.99*amount but:
assertEq(tok.balanceOf(address(vault)) - vaultBalBefore, amount * 99 / 100);
// minted shares priced on full amount + trancheInterest
assertGt(minted * vault.virtualPrice(address(aaTranche)) / 1e18, amount * 99 / 100);
// strategy tokens minted / borrower transfer accounted for full `amount`
// -> shortfall of amount/100 is socialized across existing NAV holders.
```

The attacker repeats across epochs or redeems minted shares, withdrawing `amount`-denominated value while having contributed only `0.99 * amount`.