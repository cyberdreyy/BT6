## Analysis Result

I found a valid analog vulnerability with the same root-cause pattern as the report (an address updated in a "parent" registry without migrating/preserving state tracked against the old address, orphaning already-accrued value).

### Title
Switching `feeCalculator` on `FeeVault` permanently strands already-accrued fees tracked by the old calculator - ([File: v3/src/core/FeeVault.sol])

### Summary
`FeeVault.setFeeCalculator()` [1](#0-0)  simply overwrites the `feeCalculator` reference without reading or migrating the vault's pending `accruedFees`/`accruedProtocolFees` from the previous calculator. Because `BaseFeeCalculator` keys accrued fee state by `_vaultAccruals[msg.sender]` (the vault address), and only the vault itself — via `FeeVault.claimFees()`/`claimProtocolFees()` — ever calls `feeCalculator.claimFees()`/`claimProtocolFees()` on the *currently configured* calculator, all fees already earned in the old calculator become permanently unreachable once the vault points at a new calculator.

### Finding Description
`FeeVault` stores a single mutable pointer `feeCalculator` [2](#0-1) . The accrual bookkeeping (`accruedFees`, `accruedProtocolFees`) lives in `BaseFeeCalculator._vaultAccruals[vault]` on whichever calculator contract is currently referenced, keyed by the calling vault's address [3](#0-2) .

`setFeeCalculator()` is `requiresAuth`-gated (owner action) and does the following, with no guard on outstanding balances:
```
function setFeeCalculator(IFeeCalculator newFeeCalculator) external requiresAuth {
    feeCalculator = newFeeCalculator;
    ...
    if (address(newFeeCalculator) != address(0)) {
        newFeeCalculator.registerVault();
    }
}
``` [1](#0-0) 

After this call, `feeRecipient.claimFees()` and `claimProtocolFees()` on `FeeVault` only ever call into the **new** calculator's `claimFees()`/`claimProtocolFees()`, which reads `_vaultAccruals[msg.sender]` (i.e. the vault) from the **new** calculator's own storage — a fresh mapping entry, unrelated to the old calculator's storage [4](#0-3) . The old calculator's `_vaultAccruals[vault].accruedFees`/`accruedProtocolFees` remain non-zero in the old contract's storage but there is no code path by which `FeeVault` (or anyone) will ever call that old calculator again for that vault, since `feeCalculator` has been overwritten and `FeeVault` has no generic "call arbitrary target" escape hatch (unlike `AeraVaultV2.execute()` in v2) [5](#0-4) . The fee tokens corresponding to those accrued-but-unclaimed amounts sit forever in the vault's `FEE_TOKEN` balance, undistributed, since the new calculator's fresh accrual state doesn't know about them.

This mirrors the reported pattern precisely: an address (`locker` → here `feeCalculator`) is updated at the parent/registry level, but the state that was tracked against the old address (unclaimed locker funds → here unclaimed accrued fees) is not migrated, and the old reference becomes permanently unreachable through the normal claim path, freezing legitimately earned value.

### Impact Explanation
Any `feeRecipient`/`protocolFeeRecipient` fees that accrued but were not claimed prior to an owner calling `setFeeCalculator()` become permanently unclaimable through the vault's `claimFees()`/`claimProtocolFees()` functions. The underlying `FEE_TOKEN` remains locked in the vault's balance indefinitely. This is a freezing of legitimately earned, unclaimed yield belonging to the fee recipient (an ordinary economic participant, not a privileged guardian/accountant/solver/treasury role).

### Likelihood Explanation
Trigger requires only a single owner call to `setFeeCalculator()` — a normal, expected administrative operation (e.g., upgrading to a new fee-calculation implementation) — combined with any nonzero pending `accruedFees`/`accruedProtocolFees` balance at the time of the switch, which is the common case since fees accrue continuously and are typically claimed periodically rather than immediately before every calculator upgrade.

### Recommendation
Before/atomically with switching `feeCalculator`, force settlement of outstanding balances: either (a) require `setFeeCalculator` to first call `claimFees()`/`claimProtocolFees()` on the current calculator and distribute proceeds, or (b) have `setFeeCalculator` read and carry forward `accruedFees`/`accruedProtocolFees` from the old calculator into the new one, or (c) retain a reference to prior calculators (similar to the report's recommendation to not overwrite the old locker) so a claim path against stale calculators remains available until fully drained.

### Proof of Concept
1. Deploy `DelayedFeeCalculator` (`calcA`) and a `FeeVault`-derived vault referencing `calcA`; set `vaultFees`, accrue TVL/performance fees via `submitSnapshot` over time so `_vaultAccruals[vault].accruedFees > 0`.
2. As owner, call `vault.setFeeCalculator(calcB)` (a freshly deployed second `DelayedFeeCalculator`) without calling `claimFees()` first.
3. Call `vault.claimFees()` as `feeRecipient`: assert it reverts with `Aera__NoFeesToClaim()` or returns 0, because `calcB._vaultAccruals[vault].accruedFees == 0`.
4. Query `calcA`'s internal accrual (or via a helper/getter) for `vault` and assert `accruedFees` is still > 0, proving the fee is stranded — and demonstrate there is no external function on `FeeVault`/`calcA` reachable by `feeRecipient` to retrieve it, while `FEE_TOKEN.balanceOf(vault)` still holds the corresponding tokens.

### Citations

**File:** v3/src/core/FeeVault.sol (L33-34)
```text
    /// @notice Address of the fee calculator contract
    IFeeCalculator public feeCalculator;
```

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

**File:** v3/src/core/FeeVault.sol (L104-126)
```text
    /// @inheritdoc IFeeVault
    function claimFees() external onlyFeeRecipient returns (uint256 feeRecipientFees, uint256 protocolFees) {
        address protocolFeeRecipient;

        // Interactions: claim the fees
        (feeRecipientFees, protocolFees, protocolFeeRecipient) =
            feeCalculator.claimFees(FEE_TOKEN.balanceOf(address(this)));

        // Requirements: check that the fee recipient has earned fees
        require(feeRecipientFees != 0, Aera__NoFeesToClaim());

        // Interactions: transfer the fees to the fee recipient
        FEE_TOKEN.safeTransfer(msg.sender, feeRecipientFees);
        // Log the fees claimed event
        emit FeesClaimed(msg.sender, feeRecipientFees);

        if (protocolFees != 0) {
            // Interactions: transfer the protocol fees to the protocol fee recipient
            FEE_TOKEN.safeTransfer(protocolFeeRecipient, protocolFees);
            // Log the protocol fees claimed event
            emit ProtocolFeesClaimed(protocolFeeRecipient, protocolFees);
        }
    }
```

**File:** v3/src/core/BaseFeeCalculator.sol (L26-29)
```text
    /// @notice A mapping of vault addresses to their associated state
    mapping(address vault => VaultAccruals vaultAccruals) internal _vaultAccruals;
    /// @notice A mapping of vault addresses to their assigned accountant
    mapping(address vault => address accountant) public vaultAccountant;
```

**File:** v3/src/core/BaseFeeCalculator.sol (L100-139)
```text
    /// @inheritdoc IFeeCalculator
    function claimFees(uint256 feeTokenBalance) external virtual returns (uint256, uint256, address) {
        // Effects: hook called before claiming fees
        _beforeClaimFees();

        VaultAccruals storage vaultAccruals = _vaultAccruals[msg.sender];

        uint256 vaultEarnedFees = vaultAccruals.accruedFees;
        uint256 protocolEarnedFees = vaultAccruals.accruedProtocolFees;
        uint256 claimableProtocolFee = Math.min(feeTokenBalance, protocolEarnedFees);
        uint256 claimableVaultFee;
        unchecked {
            claimableVaultFee = Math.min(feeTokenBalance - claimableProtocolFee, vaultEarnedFees);
        }

        // Effects: update accrued fees
        unchecked {
            vaultAccruals.accruedProtocolFees = uint112(protocolEarnedFees - claimableProtocolFee);
            vaultAccruals.accruedFees = uint112(vaultEarnedFees - claimableVaultFee);
        }

        return (claimableVaultFee, claimableProtocolFee, protocolFeeRecipient);
    }

    /// @inheritdoc IFeeCalculator
    function claimProtocolFees(uint256 feeTokenBalance) external virtual returns (uint256, address) {
        // Effects: hook called before claiming protocol fees
        _beforeClaimProtocolFees();

        VaultAccruals storage vaultAccruals = _vaultAccruals[msg.sender];
        uint256 accruedFees = vaultAccruals.accruedProtocolFees;
        uint256 claimableProtocolFee = Math.min(feeTokenBalance, accruedFees);

        // Effects: update accrued protocol fees
        unchecked {
            vaultAccruals.accruedProtocolFees = uint112(accruedFees - claimableProtocolFee);
        }

        return (claimableProtocolFee, protocolFeeRecipient);
    }
```
