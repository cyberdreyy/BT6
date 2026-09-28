### Title
Unprivileged attacker can DoS epoch start by saturating the ERC4626 vault deposit cap - ([File: contracts/strategies/idle/ProgrammableBorrower.sol])

### Summary
`ProgrammableBorrower` parks idle facility funds in an external ERC4626 vault via `_depositToVault`, which calls `vault.deposit(_assetAmount, address(this))` unconditionally without consulting `vault.maxDeposit(address(this))`. If the vault is at its deposit cap or paused, `deposit` reverts, which reverts `onStartEpoch` and therefore `IdleCDOEpochVariant.startEpoch`. An unprivileged attacker can fill a capped vault (e.g., a MetaMorpho-style vault with market/supply caps) so the epoch can never start while the cap is saturated.

### Finding Description
`onStartEpoch` deposits the full idle underlying balance into the vault:

```solidity
// contracts/strategies/idle/ProgrammableBorrower.sol:216
_depositToVault(underlyingToken.balanceOf(address(this)), 0);
```

and `_depositToVault` performs a raw `vault.deposit` with no `maxDeposit` check or fallback:

```solidity
// contracts/strategies/idle/ProgrammableBorrower.sol:378-385
function _depositToVault(uint256 _assetAmount, uint256 _principalAssets) internal {
  if (_assetAmount == 0) return;
  uint256 shares = vault.deposit(_assetAmount, address(this));
  ...
}
```

Per ERC4626, `deposit` must revert when the amount exceeds `maxDeposit(receiver)`. The same unchecked call is also reached from `_repay` when an epoch is active (lines 499 and 527), so an attacker saturating the vault cap during a running epoch also bricks the honest borrower's `repay`/`executeRepay` path — repaid funds can never be redeployed.

Attack sequence (buffer phase):
1. Pool is in buffer; `epochAccountingActive == false`; lender funds sit as idle underlying on `ProgrammableBorrower` (or partially in vault shares).
2. Attacker (any EOA) deposits into the external ERC4626 vault until its supply/market cap is reached — standard deposits on vaults like MetaMorpho are permissionless.
3. Owner/manager calls `cdoEpoch.startEpoch(...)` → `onStartEpoch` → `_depositToVault` → `vault.deposit` reverts → the whole transaction reverts.
4. No `try/catch` or cap check exists; `setVault` is gated on `vault.balanceOf(address(this)) == 0` and `!epochAccountingActive` (line 165), so even the operator escape path only works if there are no existing vault shares, and regardless requires the attacker to stop griefing or the cap to be raised.

### Impact Explanation
Temporary freezing of all lender funds deposited during the buffer: while the vault cap stays saturated, `startEpoch` cannot execute, deposits cannot be put to work, and no epoch lifecycle (stop, withdraw fulfillment) can progress. During a running epoch, the same griefing blocks borrower repayment, potentially forcing the pool toward the default path at `stopEpoch`. Quantified loss = the entire pool TVL held by `ProgrammableBorrower` for the duration the cap remains filled (bounded by how long the attacker keeps capital parked in the vault or the cap stays exhausted).

### Likelihood Explanation
Medium. It requires the configured vault to have a reachable `maxDeposit` below `type(uint256).max` — true for MetaMorpho-style vaults (the Foundry test suite itself integrates `IMMVault`) — and an attacker willing to lock capital to saturate it. No privileged role or special timing is needed; the grieve is repeatable each time the cap frees up.

### Recommendation
Mirror the external report: cap the deposit amount against `vault.maxDeposit(address(this))` in `_depositToVault`, leaving the excess as idle on-hand underlying rather than reverting:

```solidity
function _depositToVault(uint256 _assetAmount, uint256 _principalAssets) internal {
  if (_assetAmount == 0) return;
  uint256 maxDepositable = vault.maxDeposit(address(this));
  if (_assetAmount > maxDepositable) _assetAmount = maxDepositable;
  if (_assetAmount == 0) return;
  ...
}
```

`_currentVaultAssets()`/`totalUnderlying()` already account for on-hand balance plus vault shares, so accounting stays consistent. For the `repay` path, the same capping prevents reverts; ensure `epochDepositedToVault`/`bufferInterest` bookkeeping uses the actually-deposited amount or treats the undeployed remainder as on-hand assets (they already are via `balanceOf`).

### Proof of Concept
Foundry fork (mirroring `test/foundry/ProgrammableBorrowerCreditVault.t.sol`, which already wires a real MetaMorpho `IMMVault`):

```solidity
function testDosStartEpochByFillingVaultCap() external {
  uint256 amount = 10_000 * oneScale;
  idleCDO.depositAA(amount); // buffer-phase deposit, funds held by ProgrammableBorrower

  // Attacker fills the MetaMorpho vault to its cap with their own USDC
  address attacker = makeAddr("attacker");
  deal(USDC, attacker, morphoVault.maxDeposit(address(programmableBorrower)) + amount, true);
  vm.startPrank(attacker);
  underlying.approve(address(morphoVault), type(uint256).max);
  morphoVault.deposit(morphoVault.maxDeposit(attacker), attacker);
  vm.stopPrank();

  assertEq(morphoVault.maxDeposit(address(programmableBorrower)), 0);

  // Honest manager tries to start the epoch -> reverts inside vault.deposit
  vm.prank(manager);
  vm.expectRevert();
  cdoEpoch.startEpoch();
}
```

A second variant warps into a running epoch, has the attacker refill the cap after the borrower draws, then shows `programmableBorrower.repay(0)` reverting at the `_depositToVault` call at line 527, blocking honest repayment until the cap frees.

Uncertainty: the exact revert behavior depends on the deployed vault's cap mechanics; on vaults with `maxDeposit == type(uint256).max` the attack is not economically feasible. The code-level absence of any cap check or fallback is confirmed at lines 216, 378–385, 499, and 527 of `contracts/strategies/idle/ProgrammableBorrower.sol`.