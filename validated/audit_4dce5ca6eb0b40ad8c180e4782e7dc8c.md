### Title
Vault fee rate change is not checkpointed against prior accrual period, causing retroactive fee misapplication to depositors - ([File: v3/src/core/BaseFeeCalculator.sol])

### Summary
`BaseFeeCalculator::setVaultFees` (and `setProtocolFees`) overwrites `VaultAccruals.fees` immediately, without first accruing fees for the elapsed period at the *old* rate. The next fee accrual (`PriceAndFeeCalculatorV2::_accrueFees` via `setAnchorPrice`, or `DelayedFeeCalculator::_accrueFees` via `submitSnapshot`/`accrueFees`/claim hooks) then applies the *new* rate over the entire `timeDelta`/`totalDuration` since the last checkpoint — including the portion of time that already elapsed under the old rate. This is the exact root-cause pattern of the referenced Truflation report: a rate is changed without checkpointing the reward/fee state first, so the new rate is retroactively applied to time periods that occurred under the old rate.

### Finding Description
`setVaultFees` in [1](#0-0)  directly mutates `vaultAccruals.fees` with no call to accrue pending fees at the pre-existing rate:

```
function setVaultFees(address vault, uint16 tvl, uint16 performance) external requiresVaultAuth(vault) {
    ...
    VaultAccruals storage vaultAccruals = _vaultAccruals[vault];
    vaultAccruals.fees = Fee({ tvl: tvl, performance: performance });
    emit VaultFeesSet(vault, tvl, performance);
}
```

Downstream, `PriceAndFeeCalculatorV2::_accrueFees` computes `timeDelta = timestamp - vaultPriceState.anchorTimestamp + accrualLag` and then charges `vaultAccruals.fees.tvl`/`.performance` (the *current*, possibly just-changed, rate) over that *entire* delta: [2](#0-1) . Likewise, `DelayedFeeCalculator::_accrueFees` charges the current `vaultAccruals.fees` rate over `totalDuration = snapshotTimestamp - lastFeeAccrual` [3](#0-2) .

Sequence:
1. Vault has an outstanding, unaccrued period since `lastFeeAccrual`/`anchorTimestamp` (normal operating condition — accrual only happens on price updates/snapshots, not continuously).
2. Vault authority calls `setVaultFees` (or protocol authority calls `setProtocolFees`) to raise the TVL/performance fee rate. No accrual happens as part of this call.
3. On the next ordinary, expected flow event — `setAnchorPrice` (PriceAndFeeCalculatorV2) or `submitSnapshot`/`accrueFees`/`claimFees` (DelayedFeeCalculator) — `_accrueFees` computes fees for the *whole* unaccrued time window using the *new*, higher rate, not split at the point the rate changed.
4. This increases `vaultAccruals.accruedFees`/`accruedProtocolFees` beyond what depositors should owe for the pre-change period, reducing the feeToken balance retained for depositors and thus the value backing outstanding vault shares (units) held by ordinary depositors who took no action.

No guard checks for a pending unaccrued period before permitting a rate change, and no split-accrual logic exists to freeze the old rate for elapsed time.

### Impact Explanation
Depositors passively holding vault shares suffer value dilution because feeToken is siphoned to the fee recipient/protocol at a rate that was not in effect for the already-elapsed period — this is a retroactive charge on unclaimed yield/value that ordinary users never agreed to and cannot avoid, matching the "loss of user funds/unclaimed yield" impact bar. The magnitude scales with how long the fee period since the last checkpoint is and how large the rate change is (bounded by `MAX_TVL_FEE`/`MAX_PERFORMANCE_FEE`).

### Likelihood Explanation
This requires only a completely ordinary governance action — the vault owner/accountant adjusting fee parameters, which is an expected, supported operation, not malicious collusion — combined with the pre-existing gap between fee checkpoints that exists in both `PriceAndFeeCalculatorV2` (price updates are periodic, not continuous) and `DelayedFeeCalculator` (dispute-period based snapshots). Any rate change performed before the next accrual event triggers the bug; the accrual event does not itself have to be manipulated.

### Recommendation
In `BaseFeeCalculator::setVaultFees` and `setProtocolFees`, force accrual of pending fees at the currently-stored rate (e.g., call each fee-calculator subclass's internal accrue routine) before overwriting `vaultAccruals.fees`/`protocolFees`, so that the old rate is checkpointed against the already-elapsed period and only future time uses the new rate.

### Proof of Concept
Local Foundry fork outline:
1. Deploy `PriceAndFeeCalculatorV2`, register a vault, call `setAnchorPrice` once to set an initial `anchorTimestamp`/price and `setVaultFees(vault, tvlLow, 0)`.
2. Advance time by `T` seconds (no further price update).
3. Call `setVaultFees(vault, tvlHigh, 0)` — this changes the rate with zero accrual performed.
4. Call `setAnchorPrice` with the same price at `timestamp = anchorTimestamp + T`.
5. Assert `vaultAccruals.accruedFees` equals `tvlHigh` applied over the full `T`, not `tvlLow` over `T`; compare against the expected split value (`tvlLow` for the pre-change duration) to show the depositor-borne overcharge equals `(tvlHigh - tvlLow) * TVL * T / ONE_IN_BPS / SECONDS_PER_YEAR`. [1](#0-0) [4](#0-3) [5](#0-4)

### Citations

**File:** v3/src/core/BaseFeeCalculator.sol (L87-98)
```text
    function setVaultFees(address vault, uint16 tvl, uint16 performance) external requiresVaultAuth(vault) {
        // Requirements: check that the fees are less than the maximum allowed
        require(tvl <= MAX_TVL_FEE, Aera__TvlFeeTooHigh());
        require(performance <= MAX_PERFORMANCE_FEE, Aera__PerformanceFeeTooHigh());

        // Effects: set the vault fees
        VaultAccruals storage vaultAccruals = _vaultAccruals[vault];
        vaultAccruals.fees = Fee({ tvl: tvl, performance: performance });

        // Log new vault fees
        emit VaultFeesSet(vault, tvl, performance);
    }
```

**File:** v3/src/core/PriceAndFeeCalculatorV2.sol (L443-479)
```text
    function _accrueFees(address vault, uint256 price, uint256 timestamp) internal {
        VaultPriceStateV2 storage vaultPriceState = _vaultPriceStates[vault];

        uint256 timeDelta;
        unchecked {
            timeDelta = timestamp - vaultPriceState.anchorTimestamp + vaultPriceState.accrualLag;
        }

        // Interactions: get the current total supply
        uint256 currentTotalSupply = IERC20(vault).totalSupply();
        uint256 minTotalSupply = Math.min(currentTotalSupply, uint256(vaultPriceState.lastTotalSupply));
        uint256 minUnitPrice = Math.min(price, uint256(vaultPriceState.anchorPrice));

        uint256 tvl = Math.mulDiv(minUnitPrice, minTotalSupply, UNIT_PRICE_PRECISION);

        VaultAccruals storage vaultAccruals = _vaultAccruals[vault];
        uint256 vaultFeesEarned = _calculateTvlFee(tvl, vaultAccruals.fees.tvl, timeDelta);

        uint256 protocolFeesEarned = _calculateTvlFee(tvl, protocolFees.tvl, timeDelta);

        if (price > vaultPriceState.highestPrice) {
            uint256 profit = Math.mulDiv(price - vaultPriceState.highestPrice, minTotalSupply, UNIT_PRICE_PRECISION);
            vaultFeesEarned += _calculatePerformanceFee(profit, vaultAccruals.fees.performance);
            protocolFeesEarned += _calculatePerformanceFee(profit, protocolFees.performance);

            // Effects: update the highest price
            vaultPriceState.highestPrice = uint128(price);
        }

        // Effects: update the accrued fees
        vaultAccruals.accruedFees += vaultFeesEarned.toUint112();
        vaultAccruals.accruedProtocolFees += protocolFeesEarned.toUint112();

        // Effects: update the last total supply and last fee accrual
        vaultPriceState.lastTotalSupply = currentTotalSupply.toUint128();
        vaultPriceState.accrualLag = 0;
    }
```

**File:** v3/src/core/DelayedFeeCalculator.sol (L165-207)
```text
    /// @notice Accrues fees for a vault based on its pending snapshot
    /// @param vaultSnapshot The storage pointer to the vault's state
    /// @param vaultAccruals The storage pointer to the vault's accruals
    /// @param lastFeeAccrualCached The last fee accrual timestamp cached to avoid re-reading from storage
    /// @dev Updates the vault's state including lastFeeAccrual, lastHighestProfit, and accruedFees
    /// @dev Deletes pending snapshot if dispute period has passed
    /// @return protocolFeesEarned The earned protocol fees
    /// @return vaultFeesEarned The earned vault fees
    function _accrueFees(
        VaultSnapshot storage vaultSnapshot,
        VaultAccruals storage vaultAccruals,
        uint256 lastFeeAccrualCached
    ) internal returns (uint256 protocolFeesEarned, uint256 vaultFeesEarned) {
        uint256 snapshotTimestamp = vaultSnapshot.timestamp;
        if (lastFeeAccrualCached >= snapshotTimestamp || vaultSnapshot.finalizedAt > block.timestamp) {
            // nothing to accrue
            return (0, 0);
        }

        // pending snapshot has become active, accrue fees
        (uint256 vaultPerformanceFeeEarned, uint256 protocolPerformanceFeeEarned) = _calculatePerformanceFees(
            vaultAccruals.fees.performance, vaultSnapshot.highestProfit, vaultSnapshot.lastHighestProfit
        );

        (uint256 vaultTvlFeeEarned, uint256 protocolTvlFeeEarned) = _calculateTvlFees(
            vaultAccruals.fees.tvl, vaultSnapshot.averageValue, snapshotTimestamp, lastFeeAccrualCached
        );

        // Effects: update the vault's state
        vaultSnapshot.lastHighestProfit = vaultSnapshot.highestProfit;
        vaultSnapshot.lastFeeAccrual = uint32(snapshotTimestamp);
        vaultAccruals.accruedFees += (vaultPerformanceFeeEarned + vaultTvlFeeEarned).toUint112();
        vaultAccruals.accruedProtocolFees += (protocolPerformanceFeeEarned + protocolTvlFeeEarned).toUint112();

        // Effects: delete the pending snapshot
        vaultSnapshot.averageValue = 0;
        vaultSnapshot.highestProfit = 0;
        vaultSnapshot.timestamp = 0;
        vaultSnapshot.finalizedAt = 0;

        protocolFeesEarned = protocolPerformanceFeeEarned + protocolTvlFeeEarned;
        vaultFeesEarned = vaultPerformanceFeeEarned + vaultTvlFeeEarned;
    }
```
