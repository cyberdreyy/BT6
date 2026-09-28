### Title
Missing storage gap in `GuardedLaunchUpgradable` exposes all upgradeable credit-vault CDOs to storage collision on upgrade - (File: contracts/GuardedLaunchUpgradable.sol)

### Summary
`GuardedLaunchUpgradable` is an abstract upgradeable base inherited by `IdleCDO` and every variant, including the in-scope credit vaults `IdleCDOCreditVault` and `IdleCDOEpochVariant`. It declares two storage variables (`limit`, `governanceRecoveryFund`) but reserves no `__gap` slots. Any future version of this base contract that adds state variables will shift the storage layout of every inheriting contract, including `IdleCDOStorage`, which holds critical credit-vault fields such as `unclaimedFees`, `priceAA`/`priceBB`, `lastNAVAA`/`lastNAVBB`, `trancheAPRSplitRatio`, `isProgrammableBorrower`, and `isBBDepositEnabled`.

### Finding Description
Upgradeable contracts in this codebase follow the OpenZeppelin storage-gap convention: `IdleCDOStorage.sol` reserves `uint256[43] private __gap` and explicitly documents that new variables must be appended above the gap and the gap decremented. `GuardedLaunchUpgradable`, which sits ahead of `IdleCDOStorage` in the linearized inheritance of `IdleCDO`, does not follow the same convention:

```solidity
abstract contract GuardedLaunchUpgradable is Initializable, OwnableUpgradeable, ReentrancyGuardUpgradeable {
  uint256 public limit;
  address internal governanceRecoveryFund;
  ...
}
```

Because C3 linearization places base-contract storage before derived storage, adding even a single variable to `GuardedLaunchUpgradable` in an upgrade would shift every slot of `IdleCDOStorage` (and of `IdleCDOEpochVariant`'s own epoch variables like `epochEndDate`, `withdrawsRequestsByEpoch`, `lossRecoveryPriceByEpoch`, `postDefaultRequests`) by one or more slots. On a deployed proxy this silently reinterprets existing slot values: e.g., an address field read as a `uint256` NAV or epoch counter produces arbitrary large values, corrupting the epoch state machine, withdrawal accounting, and tranche pricing.

Note: `GuardedLaunchUpgradable` itself inherits `OwnableUpgradeable`/`ReentrancyGuardUpgradeable`, which have their own gaps, but that does not protect slots declared in `GuardedLaunchUpgradable` itself — any variable appended there still lands immediately before `IdleCDOStorage`'s layout in the proxy.

### Impact Explanation
If a future upgrade adds storage to `GuardedLaunchUpgradable`, every variable in `IdleCDOStorage` and the epoch-variant children is read from the wrong slot. Consequences include corrupted `priceAA`/`priceBB` (tranche mint/redeem at wrong prices → direct theft or insolvency), corrupted `epochEndDate`/`epochNumber` (withdrawal requests bound to wrong epochs → permanent freezing of lender funds), corrupted `lossRecoveryPriceByEpoch`/`defaultPendingClaimBasis` (wrong recovery distribution), and a corrupted `strategy`/`token` pointer (total loss of accounting). Impact is conditional on an upgrade occurring, matching the Medium severity of the source report.

### Likelihood Explanation
Likelihood is low-to-moderate: it requires a future implementation upgrade that adds state to `GuardedLaunchUpgradable` or changes its variable order. There is no attacker trigger — this is a latent upgrade-safety defect. However, the codebase is actively maintained (`isProgrammableBorrower`, `isBBDepositEnabled`, `managementFee` were recently appended to `IdleCDOStorage`), so upgrades are routine, and the same pattern could easily be applied to the unprotected base contract by a developer following the file's own comment conventions.

### Recommendation
Append a storage gap at the end of `GuardedLaunchUpgradable` and instruct that new variables be added above it:

```solidity
// contracts/GuardedLaunchUpgradable.sol
uint256[48] private __gap;
```

Additionally, verify the linearized storage layout of each deployed proxy variant (`IdleCDOCreditVault`, `IdleCDOEpochVariant`, `IdleCDOEpochVariantPrefunded`, `IdleCreditVaultWriteOffEscrow` — which is itself upgradeable with a flat, gapless layout but no derived contracts, so its risk is limited to its own variable reordering) with `openzeppelin-upgrades` storage-layout checks in CI.

### Proof of Concept
A runnable PoC requires a Foundry setup deploying an `IdleCDOEpochVariant` proxy, snapshotting `getContractValue()`/`priceAA`/`epochEndDate`, then upgrading to a modified `GuardedLaunchUpgradable` with one extra `uint256` variable and asserting the shifted reads. Due to index/tooling limits in this session I could not execute a fork test, but the structural defect is confirmed directly from source: `GuardedLaunchUpgradable.sol` lines 23–25 declare storage with no trailing `__gap`, while `IdleCDOStorage.sol` lines 114–148 document and implement the gap convention that the base contract fails to follow.