### Title
`ProvisionerV2`'s immutable `PRICE_FEE_CALCULATOR` desyncs from `MultiDepositorVault`'s mutable `feeCalculator` after `setFeeCalculator` - ([File: v3/src/core/ProvisionerV2.sol], [File: v3/src/core/FeeVault.sol])

### Summary
`FeeVault.setFeeCalculator` lets vault governance repoint a `MultiDepositorVault`'s `feeCalculator` to a new `PriceAndFeeCalculatorV2` instance at any time. `ProvisionerV2`, however, binds to a `PriceAndFeeCalculatorV2` instance as an **immutable** `PRICE_FEE_CALCULATOR` at construction and has no setter to follow the vault's migration. This is the same root cause as the openQ report: the "hub" reference (`openQ`/`feeCalculator`) is upgradable in one contract but frozen in a dependent contract, so the two contracts permanently disagree about which pricing/fee/pause engine is authoritative for the vault.

### Finding Description
`FeeVault` stores `feeCalculator` as mutable storage and exposes: [1](#0-0) 
so any vault (e.g. `MultiDepositorVault`) can be migrated to a brand-new `PriceAndFeeCalculatorV2` deployment via governance/`requiresAuth`.

Meanwhile `ProvisionerV2`, which is the sole entry/exit point for that same `MultiDepositorVault`, binds to the calculator once, forever, in its constructor: [2](#0-1) [3](#0-2) 

Every user-facing conversion, pause check, deposit-cap check, and epoch/price-age check in `ProvisionerV2` (`deposit`, `mint`, `requestDeposit`/`requestRedeem`, `solveRequestsDirect`, sync `redeem`, `maxDeposit`, cancellation-fee math, etc.) reads exclusively from this immutable `PRICE_FEE_CALCULATOR`, e.g.: [4](#0-3) [5](#0-4) [6](#0-5) 

If the vault owner ever calls `setFeeCalculator` to move fee accrual/price-anchoring to a new `PriceAndFeeCalculatorV2` instance (a normal, permitted operation with no restriction tying it to the Provisioner's immutable reference), two independent, unsynchronized sources of truth now exist for the same vault:
- The **new** calculator becomes the vault's fee-accrual/pause/anchor engine used for `claimFees` and (per its own doc) is where accountants are expected to push future price/pause updates.
- `ProvisionerV2` keeps using the **old** calculator instance for all pricing (`convertTokenToNumeraire`, `convertUnitsToNumeraire`, `convertNumeraireToToken`), for `isVaultPaused` gating of `solveRequestsVault`/`solveRequestsDirect`/`requestDeposit`, and for `getAnchorTimestamp`/epoch redeem-cap logic — none of which get further legitimate updates once the vault has migrated off it.

No guard anywhere checks `PRICE_FEE_CALCULATOR == MultiDepositorVault(vault).feeCalculator()` before executing deposits/redeems, so this cross-contract invariant (Provisioner and Vault must reference the same live pricing/pause engine) is silently broken, exactly mirroring the openQ finding where `ClaimManagerV1.openQ` could be updated while bounty contracts kept referencing the old instance.

### Impact Explanation
Once desynced, the old `PriceAndFeeCalculatorV2` instance is a "dead" contract for governance purposes — no one legitimately updates its anchor price, pauses/unpauses it, or advances its accountant-driven state — yet `ProvisionerV2` continues to treat it as ground truth for every deposit/redeem price conversion and for the `isVaultPaused` safety gate. This can freeze user funds/yield (async requests and sync deposits/redeems keyed to a calculator that will never reflect the vault's real, current price/pause state) or let stale/manipulable pricing on the abandoned calculator mis-price mints/redeems relative to the vault's actual current NAV, since price and fee accrual invariants that the calculator's own `PriceAndFeeCalculatorV2` docs assume ("Vault registration workflow... once registered a vault can have its price updated by an authorized entity... Accrues fees on each anchor update") are being maintained on a different, disconnected instance than the one gatekeeping user transactions. This matches a Medium-severity temporary-freeze/mispricing impact analogous to the cited openQ report.

### Likelihood Explanation
Requires only a single, ordinary, permitted governance call to `FeeVault.setFeeCalculator` (documented as a supported operation — even setting it to zero is explicitly allowed) with no check that the Provisioner is also migrated. Given `PRICE_FEE_CALCULATOR` in `ProvisionerV2` is immutable, there is no way to correct the desync short of deploying (and users migrating to) an entirely new Provisioner. This makes the break in the shared-reference invariant certain and irreversible any time a `feeCalculator` migration is performed on a live `MultiDepositorVault`/`ProvisionerV2` pair, which the codebase's own `setFeeCalculator` function explicitly supports as a normal maintenance action, not an attack.

### Recommendation
Either (a) make `PRICE_FEE_CALCULATOR` in `ProvisionerV2` a mutable, authorized-settable reference that must be updated atomically with `FeeVault.setFeeCalculator` (e.g., have `setFeeCalculator` also call a paired setter on the registered Provisioner, or have the Provisioner read `MultiDepositorVault.feeCalculator()` dynamically instead of caching an immutable copy), or (b) forbid `setFeeCalculator` from changing the calculator once the vault has live Provisioner(s) attached, forcing a full vault+provisioner redeployment for calculator migrations, consistent with the openQ report's recommended remediations.

### Proof of Concept
1. Deploy `PriceAndFeeCalculatorV2` A, `MultiDepositorVault` V (via `MultiDepositorVaultFactory`) with `feeCalculator = A`, and `ProvisionerV2` P constructed with `PRICE_FEE_CALCULATOR = A` and `MULTI_DEPOSITOR_VAULT = V`.
2. Register V with A, set thresholds/initial price, enable sync deposits for a token in P; perform a normal user `deposit()` through P — confirm pricing/pause reads go to A (`assert address(P.PRICE_FEE_CALCULATOR()) == address(V.feeCalculator())`).
3. As V's owner (`requiresAuth`), call `V.setFeeCalculator(B)` where B is a freshly deployed `PriceAndFeeCalculatorV2`; register V with B and set B's price/pause state to diverge from A (e.g., pause V in B, or move B's anchor price materially).
4. Assert `address(P.PRICE_FEE_CALCULATOR()) != address(V.feeCalculator())`.
5. Call `P.isVaultPaused-gated` functions (e.g. `solveRequestsDirect`, `requestDeposit`) and show they still succeed/fail based on stale calculator A's state (e.g., V is "paused" in B but P still permits deposits/redeems because A shows unpaused), and that `P.maxDeposit()`/conversion calls use A's stale anchor price rather than B's updated price — demonstrating divergent, unsynchronized accounting between the vault's live fee/price engine and the Provisioner's frozen reference.

### Citations

**File:** v3/src/core/FeeVault.sol (L80-91)
```text
    /// @inheritdoc IFeeVault
    function setFeeCalculator(IFeeCalculator newFeeCalculator) external requiresAuth {
        // Effects: set the new fee calculator
        feeCalculator = newFeeCalculator;
        // Log the fee calculator updated event
        emit FeeCalculatorUpdated(address(newFeeCalculator));

        // Interactions: register vault only if the new calculator is not address(0)
        if (address(newFeeCalculator) != address(0)) {
            newFeeCalculator.registerVault();
        }
    }
```

**File:** v3/src/core/ProvisionerV2.sol (L55-60)
```text
    /// @notice The price and fee calculator contract
    IPriceAndFeeCalculatorV2 public immutable PRICE_FEE_CALCULATOR;

    /// @notice The multi depositor vault contract
    address public immutable MULTI_DEPOSITOR_VAULT;

```

**File:** v3/src/core/ProvisionerV2.sol (L148-163)
```text
    constructor(
        IPriceAndFeeCalculatorV2 priceAndFeeCalculator,
        address multiDepositorVault,
        bool solvingGateEnabled,
        address owner_,
        Authority authority_
    ) Auth2Step(owner_, authority_) {
        // Requirements: immutables are not zero addresses
        require(address(priceAndFeeCalculator) != address(0), Aera__ZeroAddressPriceAndFeeCalculator());
        require(multiDepositorVault != address(0), Aera__ZeroAddressMultiDepositorVault());

        // Effects: set immutables
        PRICE_FEE_CALCULATOR = priceAndFeeCalculator;
        MULTI_DEPOSITOR_VAULT = multiDepositorVault;
        SOLVING_GATE_ENABLED = solvingGateEnabled;
    }
```

**File:** v3/src/core/ProvisionerV2.sol (L412-414)
```text
    function solveRequestsDirect(IERC20 token, RequestV2[] calldata requests) external nonReentrant {
        // Requirements: vault is not paused in the priceAndFeeCalculator
        require(!PRICE_FEE_CALCULATOR.isVaultPaused(MULTI_DEPOSITOR_VAULT), Aera__PriceAndFeeCalculatorVaultPaused());
```

**File:** v3/src/core/ProvisionerV2.sol (L672-682)
```text
        // Interactions: read the anchor snapshot from PFC so epoch rollover and cap sizing use the same basis
        epochTimestamp = PRICE_FEE_CALCULATOR.getAnchorTimestamp(MULTI_DEPOSITOR_VAULT);
        epochRedeemedNumeraire = (epochTimestamp == _syncRedeemEpochTimestamp) ? _syncRedeemEpochRedeemedNumeraire : 0;

        // Requirements: paused vaults should expose a safe zero-cap view instead of reverting through PFC
        if (PRICE_FEE_CALCULATOR.isVaultPaused(MULTI_DEPOSITOR_VAULT)) {
            return (epochTimestamp, 0, epochRedeemedNumeraire, 0);
        }

        (epochCapNumeraire, epochStartTvlNumeraire) = _computeEpochCap();
    }
```

**File:** v3/src/core/ProvisionerV2.sol (L702-713)
```text
    function maxDeposit() external view returns (uint256) {
        if (PRICE_FEE_CALCULATOR.isVaultPaused(MULTI_DEPOSITOR_VAULT)) return 0;

        // Interactions: get current total supply
        uint256 totalSupply = IERC20(MULTI_DEPOSITOR_VAULT).totalSupply();
        // Interactions: convert total supply to numeraire with protocol-favoring ceil rounding
        uint256 totalAssets =
            PRICE_FEE_CALCULATOR.convertUnitsToNumeraire(MULTI_DEPOSITOR_VAULT, totalSupply, Math.Rounding.Ceil);

        // Return max of 0 or difference between deposit cap and total assets
        return totalAssets < depositCap ? depositCap - totalAssets : 0;
    }
```
