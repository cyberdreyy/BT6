### Title
Uninitialized ERC4626 vault permits first-depositor share inflation against `ProgrammableBorrower` - (`contracts/strategies/idle/ProgrammableBorrower.sol`)

### Summary
`ProgrammableBorrower.initialize` approves the configured ERC4626 vault, but does not verify that the vault has initialized liquidity, a nonzero share supply, or a safe `convertToAssets` ratio. An attacker can front-run the first `onStartEpoch` deposit, execute a classic ERC4626 first-depositor/donation inflation against the vault, and cause the borrower adapter to receive a rounded-down share position that does not represent its deposited assets.

### Finding Description
`ProgrammableBorrower` treats `vault.deposit` as the initialization point for the external vault relationship and later values its position through `vault.convertToAssets`.

```solidity
// contracts/strategies/idle/ProgrammableBorrower.sol:125-134
underlyingToken = IERC20Detailed(_underlyingToken);
vault = IERC4626(_vault);
...
_allowUnlimitedSpend(_underlyingToken, _vault);
```

```solidity
// contracts/strategies/idle/ProgrammableBorrower.sol:214-222
uint256 startAssets = underlyingToken.balanceOf(address(this)) + currentVaultAssets;
_depositToVault(underlyingToken.balanceOf(address(this)), 0);
epochStartVaultAssets = startAssets;
```

```solidity
// contracts/strategies/idle/ProgrammableBorrower.sol:545-549
uint256 shares = vault.balanceOf(address(this));
return shares == 0 ? 0 : vault.convertToAssets(shares);
```

No check requires `vault.totalSupply() != 0`, `vault.totalAssets() != 0`, a minimum initial share supply, or a known-safe exchange rate. This is analogous to consuming an uninitialized Uniswap V3 observation buffer: the integration records the external contract but never establishes the prerequisite state needed for safe operation.

### Impact Explanation
An unprivileged vault user can become the ERC4626 vault's first depositor and directly donate assets to inflate the vault's assets-per-share before the credit vault's first epoch deposit. When `ProgrammableBorrower.onStartEpoch` deposits the pool's idle funds, the manipulated vault mints an abnormally small share amount to the adapter.

The resulting loss is approximately:

```text
manipulated_conversion_ratio × minted_shares < deposited_assets
```

For a standard integer-rounding ERC4626, the attacker can capture close to half of the deposited amount after accounting for the initial seed deposit. The stolen funds reduce vault assets attributable to the credit vault, which propagates as a vault loss through `vaultLoss()` / `totalInterestDueNow()` at `stopEpoch` and ultimately impairs AA/BB tranche NAV.

### Likelihood Explanation
Likelihood depends on deploying `ProgrammableBorrower` with an empty or insufficiently initialized ERC4626 vault. The factory accepts the vault as deployment input and immediately initializes approvals, but performs no liquidity or share-supply initialization check.

Attack requirements:

1. Attacker is an ordinary ERC4626 vault user.
2. Vault starts with zero shares or attacker-controlled negligible supply.
3. Attacker front-runs the first `onStartEpoch` deposit with a small mint and a large direct donation.
4. The vault uses vulnerable integer share rounding.

No privileged role, oracle manipulation inside Idle, borrower cooperation, or timing assumptions beyond transaction ordering are required.

### Recommendation
Require the ERC4626 vault to be initialized before enabling programmable-borrower mode:

- Validate `vault.totalSupply()` and `vault.totalAssets()` during `initialize`.
- Require a minimum immutable liquidity seed or an established minimum share supply.
- Reject suspicious conversion ratios at initialization and before the first deposit.
- Prefer depositing through slippage-protected vault APIs where available, or compute expected shares with `previewDeposit` and enforce a minimum share output.
- Optionally seed the vault during deployment before approving it.

The key invariant is that the borrower adapter must never become the vault's effective first depositor.

### Proof of Concept
A reproducible Foundry PoC would deploy a minimal ERC4626 whose share minting is vulnerable to first-depositor donation inflation, deploy `ProgrammableBorrower` against it, then sequence:

```solidity
// 1. Attacker seeds the empty ERC4626 vault.
vault.deposit(1, attacker);

// 2. Attacker directly donates a large amount of underlying to the vault.
underlying.transfer(address(vault), donation);

// 3. Honest manager starts the epoch. ProgrammableBorrower deposits its
//    idle underlying into the inflated vault.
cdo.startEpoch();

// 4. Attacker redeems its controlling shares and receives the pool's deposit
//    plus donation, net of the intentionally abandoned seed amount.
vault.redeem(vault.balanceOf(attacker), attacker, attacker);

// 5. The CDO later observes reduced or zero attributable vault assets through
//    convertToAssets and realizes the loss/default at stopEpoch.
cdo.stopEpochWithDuration(0, 0, epochDuration, 0);
```

The demonstrated loss equals the underlying deposited by `ProgrammableBorrower` minus the assets represented by the rounded shares it received, up to nearly the full deposit after subtracting the attacker's seed.