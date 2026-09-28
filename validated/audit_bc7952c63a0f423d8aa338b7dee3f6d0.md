### Title
ProgrammableBorrower deposits pool funds into the ERC4626 vault with no min-shares check — first-depositor share inflation steals the epoch principal - (File: contracts/strategies/idle/ProgrammableBorrower.sol)

### Summary
Analogous to the File Browser report — where an attacker-controlled value (`$FILE`) is substituted unsanitized into a privileged execution context — `ProgrammableBorrower._depositToVault` feeds the pool's entire idle balance into an external ERC4626 `deposit()` with no `minSharesOut` guard and no protection against an inflated share price. Any user of the configured ERC4626 vault (an explicitly in-scope unprivileged actor) can perform the classic first-depositor/donation inflation attack, causing the pool's deposits to mint near-zero shares and donating the principal to the attacker's position.

### Finding Description
`ProgrammableBorrower` parks all idle underlying in an external ERC4626 `vault`. Deposits happen in three places, all funneled through `_depositToVault`: [1](#0-0) 

```go
uint256 shares = vault.deposit(_assetAmount, address(this));
```

The return value `shares` is only emitted, never validated. The callers:

- `onStartEpoch` deposits `underlyingToken.balanceOf(address(this))` — the entire epoch's pooled liquidity sent by IdleCDOEpochVariant (`_depositToVault(balance, 0)`, line 216).
- `_repay` redeploys repaid principal into the vault during active epochs (line 527).

An attacker who is an ordinary depositor in the same ERC4626 vault can:

1. Be the first depositor: mint 1 wei of shares with a tiny deposit.
2. Donate a large amount `D` of underlying directly to the vault, inflating `convertToAssets(1 share)` to ≈ `D`.
3. Wait for (or front-run) `startEpoch` on `IdleCDOEpochVariant`, which calls `sendInterestAndDeposits` → `onStartEpoch` → `_depositToVault` with the pool's balance `P`.
4. `vault.deposit(P)` mints `floor(P * 1 / (D + 1))` shares — 0 or near-0 shares for `P < D`.
5. Attacker redeems their 1 share for ≈ `D + P`, stealing the pool's deposit.

Even partial inflation (vault not empty, but attacker sandwiches a donation between totalAssets reads) yields the same effect: the pool's deposit buys shares at an inflated rate, and the attacker withdraws at a profit.

The accounting then makes it worse: `epochStartVaultAssets` is snapshotted from the pre-deposit asset total (line 214–219), so the loss is not visible as `vaultLoss` until `onStopEpoch`, where `_currentVaultAssets()` (≈0 shares → 0 assets) produces a massive `loss` that is socialized into `totalInterestDueNow` → `expectedEpochInterest` → tranche prices via `_updateAccounting`, i.e. BB-first loss socialization of stolen principal. [2](#0-1) [3](#0-2) 

### Impact Explanation
Direct theft of pool principal. Quantified loss: up to the entire idle balance deployed at `onStartEpoch` (or each in-epoch repayment redeployed by `_repay`), bounded by `P - attackerSharesValue`. With `D ≥ P` the attacker's cost `D` is recovered plus `≈ P` profit; the pool records a vault loss equal to `P`, socialized across tranche holders BB-first at the next `stopEpoch`. This is direct theft with insolvency-grade magnitude for the facility, satisfying the severity bar.

### Likelihood Explanation
Requires: (a) the pool is configured with `isProgrammableBorrower` and a vault that accepts third-party deposits (any standard ERC4626, e.g. an ERC4626 vault where the attacker can be first depositor or hold a large share position); (b) an epoch start or an active-epoch `repay` occurring while the share price is inflated. The attacker is a pure EOA vault user — no privileged role, no KYC needed. Front-running `startEpoch` in the same block is sufficient since the donation persists until the deposit executes. No existing guard stops it: `_skimDonatedAssets` only protects the CDO/strategy's own token balance, not the external vault's `convertToAssets`; `nonReentrant` is irrelevant; `setVault` only checks `asset()` matching.

### Recommendation
Add a minimum-shares parameter / slippage check to `_depositToVault` (e.g. `require(shares >= minShares, ...)`, or compare `vault.convertToAssets(shares)` against `_assetAmount` within a tolerance), or require the vault to be seeded/whitelisted so share price cannot be manipulated by third parties. Alternatively compute expected shares via `previewDeposit` and revert on deviation beyond a small rounding bound.

### Proof of Concept
Foundry-style sketch (fork test against a real ERC4626, or a mock vault with donation support):

```solidity
// Attacker is a plain EOA vault user.
// 1. Vault is empty; attacker deposits 1 wei, then donates D = 10_000e18.
vault.deposit(1, attacker);
underlying.mint(attacker, D);
underlying.transfer(address(vault), D); // inflate convertToAssets

// 2. Honest manager calls startEpoch -> onStartEpoch -> _depositToVault(P)
vm.prank(manager);
idleCDO.startEpoch(); // pool sends P = 9_000e18 to ProgrammableBorrower

// ProgrammableBorrower received floor(P * totalSupply / totalAssets) = 0 shares
assertEq(vault.balanceOf(address(programmableBorrower)), 0);

// 3. Attacker redeems everything: D + P
vm.prank(attacker);
vault.redeem(vault.balanceOf(attacker), attacker, attacker);
assertGt(underlying.balanceOf(attacker), D + P - 1e6);
```

The same path is reachable mid-epoch via `repay`, which redeploys repaid principal with `_depositToVault(totalRepaidAssets, ...)` (line 527) under the same missing-slippage condition.

### Citations

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L337-347)
```text
  function _vaultNetInterest() internal view returns (uint256 interest, uint256 loss) {
    if (!epochAccountingActive) return (0, 0);
    uint256 earnedAssets = _currentVaultAssets() + epochWithdrawnFromVault;
    uint256 principalAssets = epochStartVaultAssets + epochDepositedToVault;
    int256 netDelta = bufferedVaultDelta + int256(earnedAssets) - int256(principalAssets);
    if (netDelta > 0) {
      interest = uint256(netDelta);
    } else {
      loss = uint256(-netDelta);
    }
  }
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L378-385)
```text
  function _depositToVault(uint256 _assetAmount, uint256 _principalAssets) internal {
    if (_assetAmount == 0) return;
    uint256 shares = vault.deposit(_assetAmount, address(this));
    if (epochAccountingActive && _principalAssets != 0) {
      epochDepositedToVault += _principalAssets;
    }
    emit DepositedIntoVault(_assetAmount, shares);
  }
```

**File:** contracts/IdleCDOEpochVariant.sol (L270-274)
```text
    // transfer in this contract funds from interest payment (if any) and buffer deposits sent to the strategy
    uint256 _totEpochDeposits = _strategy.totEpochDeposits();
    // If interest is minted we do not transfer interest to the strategy
    uint256 _toSend = isInterestMinted ? _totEpochDeposits : lastEpochInterest + _totEpochDeposits;
    _strategy.sendInterestAndDeposits(_toSend);
```
