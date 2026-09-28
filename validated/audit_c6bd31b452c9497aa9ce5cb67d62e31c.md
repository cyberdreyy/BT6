### Title
`setVault` uses `safeApprove(vault, 0)` which reverts for ERC20s that disallow zero-amount approvals, permanently blocking vault migration - (File: contracts/strategies/idle/ProgrammableBorrower.sol)

### Summary
`ProgrammableBorrower.setVault` resets the old vault allowance with `underlyingToken.safeApprove(address(vault), 0)` (line 166) before granting unlimited spend to the new vault. For ERC20 tokens that revert on `approve(spender, 0)` (e.g., BNB-style tokens), this call always reverts, making it impossible to ever migrate the facility to a new vault.

### Finding Description
`ProgrammableBorrower` is the borrower adapter used by `IdleCDOEpochVariant` for revolving-credit facilities. It keeps undrawn capital inside an ERC4626 vault and grants standing approvals via `_allowUnlimitedSpend`, which is implemented with `safeIncreaseAllowance(spender, type(uint256).max)` (line 612).

Vault migration is handled by `setVault` (lines 158-170):

```solidity
if (epochAccountingActive || vault.balanceOf(address(this)) != 0) revert NotAllowed();
underlyingToken.safeApprove(address(vault), 0);   // line 166
vault = IERC4626(_vault);
_allowUnlimitedSpend(address(underlyingToken), _vault);
```

The zero-approval reset assumes `approve(x, 0)` always succeeds. Some ERC20s revert on zero-amount `approve` calls (the reported token is BNB, which `idle-tranches` targets on BSC deployments via chain-specific variants). For such an underlying, `safeApprove(vault, 0)` propagates the token's revert and `setVault` can never complete, even though the function's own guards (no active epoch, zero share balance) are satisfied.

The same bug class exists at initialization-adjacent paths: `_allowUnlimitedSpend` relies on `safeIncreaseAllowance`, which under OpenZeppelin resolves to `approve(currentAllowance + value)`. On tokens that reject allowance changes from a nonzero value without an explicit reset (USDT-style), this is handled correctly by OZ, but the zero-reset path in `setVault` is precisely the pattern flagged in the reference report — an unconditional `approve(0)` on a token that forbids it.

### Impact Explanation
If the configured ERC4626 vault must be migrated (vault deprecation, fee change, security incident in the external vault, or a better yield venue), the manager/owner cannot repoint `vault`. Because `setVault` reverts, the facility is stuck with the old vault integration for the rest of the contract's lifetime on that proxy. Depositor funds parked in the old vault can still be unwound via `emergencyExitVault`, but the facility cannot resume normal operation (new epochs deposit into `vault` via `_depositToVault`/`onStartEpoch`), so user capital is effectively frozen in an unwound state until a contract upgrade — a temporary freezing of funds matching the original Medium-severity impact on `rebalanceAll`.

### Likelihood Explanation
Requires the credit vault's `underlyingToken` to be an ERC20 that reverts on `approve(spender, 0)`. This is a minority of tokens (BNB is the canonical example, and it is a supported deployment chain), so likelihood is conditional on deployment configuration rather than attacker action — the trigger is a routine honest manager call to `setVault`.

### Recommendation
Avoid the zero-amount `approve` on the underlying. Replace the reset with a conditional pattern, e.g.:

```solidity
uint256 allowance = underlyingToken.allowance(address(this), address(vault));
if (allowance != 0) {
    underlyingToken.safeDecreaseAllowance(address(vault), allowance); // or a try/catch approve(0)
}
vault = IERC4626(_vault);
_allowUnlimitedSpend(address(underlyingToken), _vault);
```

Using `safeDecreaseAllowance` (or skipping the reset entirely, since the old vault is already unwound and trusted) avoids ever passing `0` to `approve` on tokens that forbid it.

### Proof of Concept
A Foundry test would: deploy `ProgrammableBorrower` pointing at an ERC4626 whose asset is a mock token that reverts on `approve(_, 0)`; run through an epoch; unwind the vault position; then call `setVault(newVault)` as manager and observe a revert originating from the token's `approve`, confirming migration is permanently bricked.