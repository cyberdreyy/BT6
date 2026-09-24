Based on my investigation, I found a genuine structural analog to the Cooler "unclaimed funds lost on ownership/role transfer" bug, located in Aera's v3 fee-vault/fee-calculator subsystem.

### Title
Fee recipient rotation permanently reassigns previously-accrued, unclaimed fees to the new recipient - ([File: v3/src/core/FeeVault.sol], [File: v3/src/core/BaseFeeCalculator.sol])

### Summary
`FeeVault.setFeeRecipient` immediately overwrites `feeRecipient` without first forcing a claim of fees accrued under the old recipient [1](#0-0) . The fee accrual state (`accruedFees`/`accruedProtocolFees`) in `BaseFeeCalculator`/`DelayedFeeCalculator` is keyed only by vault address, not by the fee recipient address at time of accrual [2](#0-1) [3](#0-2) . Consequently, whoever holds `feeRecipient` at the moment `claimFees()` is called receives the *entire* accrued fee balance, including amounts economically earned while a previous recipient held the role.

### Finding Description
Fees accrue continuously against the vault (via `_accrueFees` keyed on `_vaultAccruals[vault]`) independent of who the current `feeRecipient` is [4](#0-3) . `claimFees()` in `BaseFeeCalculator` reads and drains `vaultAccruals.accruedFees` for `msg.sender` (the vault), and returns it to be paid to whoever calls `FeeVault.claimFees()` under the `onlyFeeRecipient` modifier at that moment [5](#0-4) .

`setFeeRecipient(newFeeRecipient)` performs no accrual settlement/claim for the outgoing recipient — it is a pure effects-only state write:
```solidity
function setFeeRecipient(address newFeeRecipient) external requiresAuth {
    require(newFeeRecipient != address(0), Aera__ZeroAddressFeeRecipient());
    feeRecipient = newFeeRecipient;
    emit FeeRecipientUpdated(newFeeRecipient);
}
``` [1](#0-0) 

This exactly mirrors the Cooler root cause: a role-holder (old lender / old fee recipient) accumulates an unclaimed, entitled balance; the role is transitioned to a new holder (new lender / new fee recipient) via a state-only update; the new holder can then claim the accumulated balance that was economically earned by the previous holder, because the accrual bookkeeping is not partitioned per-holder — it's a single running total tied to the vault, not the recipient. Compare this to `AeraVaultV2`, which correctly avoids this class of bug by keying accrued fees per-recipient-address (`mapping(address => uint256) public fees`, accrued as `fees[feeRecipient] += newFee`) [6](#0-5) [7](#0-6)  — v3's `FeeVault`/`BaseFeeCalculator` regressed this protection by tracking accruals solely by vault address.

### Impact Explanation
The outgoing fee recipient permanently and irrecoverably loses all fees accrued but not yet claimed at the time of rotation — the new recipient can claim that balance in full. This is a direct theft of unclaimed yield from a legitimate stakeholder, matching the Medium-severity "temporary/permanent freezing or loss of user/stakeholder funds" category.

### Likelihood Explanation
The only precondition is a normal, expected operational action: the vault owner calling `setFeeRecipient` to rotate the recipient address (e.g., treasury address rotation) while fees have accrued and not yet been claimed. No malicious guardian/solver/oracle collusion is required — this is a pure accounting/ordering flaw triggered by routine fee-recipient rotation, exactly analogous to the Cooler `approveTransfer`/`transferOwnership` sequence occurring before `claimRepaid`.

### Recommendation
In `setFeeRecipient`, force-claim (or checkpoint/settle) any outstanding `accruedFees` to the outgoing `feeRecipient` before switching to the new recipient, e.g. call `feeCalculator.claimFees(...)` and transfer the resulting amount to the old `feeRecipient` prior to updating storage — mirroring the suggested Cooler fix of calling `claimRepaid` before completing the ownership transfer.

### Proof of Concept
1. Deploy a `MultiDepositorVault`/`SingleDepositorVault` with `DelayedFeeCalculator`, register it, and set non-zero `tvl`/`performance` fees.
2. Advance time and submit a snapshot via `submitSnapshot` so that `_accrueFees` accumulates `accruedFees > 0` for the vault (wait past `DISPUTE_PERIOD`).
3. As the vault owner, call `setFeeRecipient(recipientB)` while `recipientA` (the original `feeRecipient`) has not yet called `claimFees()`.
4. As `recipientB`, call `FeeVault.claimFees()` and assert `recipientB` receives the full previously-accrued `accruedFees` balance (via `FEE_TOKEN.balanceOf(recipientB)` increasing by that amount).
5. As `recipientA`, call `claimFees()` and assert it reverts with `Aera__CallerIsNotFeeRecipient` (or `Aera__NoFeesToClaim` after the drain), proving `recipientA`'s earned fees were irrecoverably transferred to `recipientB`.

### Citations

**File:** v3/src/core/FeeVault.sol (L93-102)
```text
    /// @inheritdoc IFeeVault
    function setFeeRecipient(address newFeeRecipient) external requiresAuth {
        // Requirements: check that the new fee recipient is not the zero address
        require(newFeeRecipient != address(0), Aera__ZeroAddressFeeRecipient());

        // Effects: set the new fee recipient
        feeRecipient = newFeeRecipient;
        // Log the fee recipient updated event
        emit FeeRecipientUpdated(newFeeRecipient);
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

**File:** v3/src/core/BaseFeeCalculator.sol (L100-122)
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
```

**File:** v3/src/core/Types.sol (L168-176)
```text
/// @notice Tracks fee configuration and accrued fees for a vault
struct VaultAccruals {
    /// @notice Current fee rates for the vault
    Fee fees;
    /// @notice Accrued fees for the vault fee recipient
    uint112 accruedFees;
    /// @notice Total protocol fees accrued but not claimed
    uint112 accruedProtocolFees;
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

**File:** v2/AeraVaultV2.sol (L72-76)
```text
    /// @notice Fee earned amount for each prior fee recipient.
    mapping(address => uint256) public fees;

    /// @notice Total fee earned and unclaimed amount by all fee recipients.
    uint256 public feeTotal;
```

**File:** v2/AeraVaultV2.sol (L663-665)
```text
        // Effects: accrue fee to fee recipient and remember new fee total.
        fees[feeRecipient] += newFee;
        feeTotal += newFee;
```
