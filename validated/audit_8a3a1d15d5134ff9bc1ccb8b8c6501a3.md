## Finding [1](#0-0) [2](#0-1) 

### Title
`ProvisionerV2`/`MultiDepositorVault` mint units based on nominal `tokenAmount` instead of actual received balance for fee-on-transfer tokens - ([File: v3/src/core/MultiDepositorVault.sol])

### Summary
`MultiDepositorVault.enter()` pulls `tokenAmount` via `safeTransferFrom` and then unconditionally `_mint`s `unitsAmount` to the recipient, without verifying that `tokenAmount` was actually received. `ProvisionerV2.deposit()`/`mint()`/`_syncDeposit()` compute `unitsOut` from the nominal `tokensIn` value supplied by the caller (via `_tokensToUnitsFloorIfActive`), not from the vault's post-transfer balance delta. If a token configured via `setTokenDetails` takes a fee-on-transfer, the vault receives less than `tokenAmount` but still mints units as if it received the full nominal amount, exactly mirroring the wfCash root cause: shares/units are minted 1:1 against a deposit amount that the vault never actually custodies.

### Finding Description
An ordinary depositor calls `ProvisionerV2.deposit(token, tokensIn, minUnitsOut, receiver)`. `_tokensToUnitsFloorIfActive` converts the caller-specified nominal `tokensIn` to `unitsOut` via the oracle/price calculator, with no reference to actual token balance received. `_syncDeposit` then calls `IMultiDepositorVault(MULTI_DEPOSITOR_VAULT).enter(msg.sender, token, tokenAmount, unitAmount, receiver)`. `MultiDepositorVault.enter()` does `token.safeTransferFrom(sender, address(this), tokenAmount)` and unconditionally `_mint(recipient, unitsAmount)` — there is no balance-before/balance-after check anywhere in this path (confirmed no `balanceOf` checks exist in the repo's transfer-in path). For a fee-on-transfer ERC20 configured as an accepted deposit token, the vault's actual token balance increases by `tokenAmount - fee`, while units minted are calculated from the full nominal `tokenAmount`. This is the same broken invariant as the wfCash finding: total units minted are not 1:1 backed by the vault's actual custodied assets, and the deficit compounds with every fee-on-transfer deposit.

### Impact Explanation
Every deposit of a fee-on-transfer token creates a shortfall equal to the transfer fee between vault assets and units minted. As with the wfCash case, the vault becomes progressively under-collateralized; later redeemers (via `ProvisionerV2.redeem`/async redeem, which call `exit()` and transfer real tokens out) cannot all be satisfied, and the last redeemers of that token lose access to funds — a freezing/loss of user funds condition.

### Likelihood Explanation
This requires an admin (`requiresAuth`) to have configured a fee-on-transfer token via `setTokenDetails` with deposits enabled — a legitimate integration/config decision, not a compromised/malicious actor. Given that requirement, any ordinary depositor using that token triggers the shortfall with no special preconditions; this matches the accepted (Medium) severity and root cause of the referenced Notional wfCash issue.

### Recommendation
In `MultiDepositorVault.enter()`, measure the actual token balance delta (`balanceOf(address(this))` before/after `safeTransferFrom`) and mint units/proceed downstream calculations based on the actually-received amount, or disallow fee-on-transfer tokens from being configured as deposit assets in `ProvisionerV2.setTokenDetails`.

### Proof of Concept
1. Deploy a mock fee-on-transfer ERC20 (e.g., 2% fee on transfer) and configure it via `ProvisionerV2.setTokenDetails` with `syncDepositEnabled = true`.
2. User A calls `ProvisionerV2.deposit(feeToken, 1000e18, minUnitsOut, userA)`. Assert `MultiDepositorVault` actual token balance is `980e18` (2% fee deducted) while `unitsOut` minted equals value computed from `1000e18`.
3. Assert total vault unit supply now represents `1000e18` worth of the token but vault custodies only `980e18`.
4. Have all unit holders attempt to redeem via `ProvisionerV2.redeem`/async redeem flow; assert the last redeemer's `exit()` call reverts or under-pays due to insufficient token balance in the vault, demonstrating the shortfall/frozen funds.

### Citations

**File:** v3/src/core/ProvisionerV2.sol (L170-198)
```text
    function deposit(IERC20 token, uint256 tokensIn, uint256 minUnitsOut, address receiver)
        external
        nonReentrant
        anyoneButVault
        solvingNotPaused(token)
        returns (uint256 unitsOut)
    {
        // Requirements: receiver is valid
        _requireValidReceiver(receiver);

        // Requirements: token amount and min units out are positive
        _validateNonZeroAmounts(minUnitsOut, tokensIn);

        // Requirements: sync deposits are enabled
        TokenDetailsV2 storage tokenDetails = _requireSyncDepositsEnabled(token);

        // Interactions: convert token amount to units out
        unitsOut = _tokensToUnitsFloorIfActive(token, tokensIn, tokenDetails.syncDepositMultiplier);
        // Requirements: units out meets min units out
        require(unitsOut >= minUnitsOut, Aera__MinUnitsOutNotMet());
        // Requirements + interactions: convert new total units to numeraire and check against deposit cap
        _requireDepositCapNotExceeded(unitsOut);

        // Effects + interactions: sync deposit
        _syncDeposit(token, tokensIn, unitsOut, receiver);

        // Interactions: push funds to yield source if configured (swallows failures)
        _pushFundsIfConfigured(tokenDetails, tokensIn);
    }
```

**File:** v3/src/core/MultiDepositorVault.sol (L61-74)
```text
    function enter(address sender, IERC20 token, uint256 tokenAmount, uint256 unitsAmount, address recipient)
        external
        whenNotPaused
        onlyProvisioner
    {
        // Interactions: pull tokens from the sender
        if (tokenAmount > 0) token.safeTransferFrom(sender, address(this), tokenAmount);

        // Effects: mint units to the recipient
        _mint(recipient, unitsAmount);

        // Log the enter event
        emit Enter(sender, recipient, token, tokenAmount, unitsAmount);
    }
```
