This confirms the analog: `submit()` requires each operation call to succeed (`require(success, Aera__SubmissionFailed(i, result))` at [1](#0-0) ), so any external yield-source call embedded in the pre-registered submit payload (e.g., an Aave-style `withdraw`/`deposit` on a paused/broken lending pool) will bubble up and revert the entire `submit()` call — and by extension revert the caller in `ProvisionerV2`.

### Title
Sync redeem/withdraw permanently revert when the configured yield-source `submit` operation targets a paused/broken lending pool - ([File: v3/src/core/ProvisionerV2.sol])

### Summary
`ProvisionerV2.redeem`/`withdraw` route through `_syncRedeem` → `_pullFundsIfNeeded`, which — whenever the vault's idle balance of the redeem token is insufficient — replays a pre-configured `pullFundsSubmitDataPointer` payload via `IBaseVault(MULTI_DEPOSITOR_VAULT).submit(data)` and explicitly documents "reverts on failure" [2](#0-1) . `BaseVault._executeSubmit` requires every embedded call to succeed or the whole `submit` reverts [3](#0-2) . If the pull-funds payload's target (e.g. an Aave-style lending pool integration used as the vault's yield source) is paused or reverts, ordinary users cannot redeem/withdraw at all, mirroring the reported Aave-pause DoS pattern.

### Finding Description
Path: any user calls `ProvisionerV2.redeem()` or `withdraw()` (both `anyoneButVault`, ordinary user entrypoints) [4](#0-3) . These call `_syncRedeem`, which calls `_pullFundsIfNeeded(token, tokensOut)` before exiting the vault [5](#0-4) . `_pullFundsIfNeeded` only triggers when `token.balanceOf(MULTI_DEPOSITOR_VAULT) < tokensOut`, in which case it unconditionally calls `IBaseVault(MULTI_DEPOSITOR_VAULT).submit(data)` with no try/catch [6](#0-5) . Inside `submit`, `_executeSubmit` performs `ctx.target.call{value: ctx.value}(callData)` and requires `success` or reverts the entire transaction with `Aera__SubmissionFailed` [7](#0-6) . If the configured pull-funds target is a paused/broken third-party lending protocol (the yield source funds were pushed into via the symmetric `_pushFundsIfConfigured` path [8](#0-7) ), that external call reverts, and this failure is *not* swallowed for the pull path (unlike the push path, which explicitly "swallows failures" via a raw `.call`). The broken guard is the absence of any fallback/try-catch on the pull-funds `submit` call, unlike the analogous `refundDeposit` function which does wrap its `exit` call in try/catch to fall back to the sender [9](#0-8) .

### Impact Explanation
Once idle vault balance of the token drops below any user's requested `tokensOut` (which will happen whenever funds have been pushed to the yield source and enough time/volume has passed), every subsequent `redeem`/`withdraw` call for that token reverts as long as the third-party lending protocol remains paused/broken. This is a temporary freeze of user principal and unclaimed yield with no way for ordinary users to exit sync redeem for that token until governance reconfigures `pullFundsSubmitDataPointer` or the protocol unpauses — matching the live bounty's Medium "temporary freezing of funds" impact tier.

### Likelihood Explanation
Preconditions are realistic and do not require any privileged/malicious actor: (1) the vault admin has configured `pushFundsSubmitDataPointer`/`pullFundsSubmitDataPointer` to route idle liquidity into an external lending market (the intended, documented use of these hooks), and (2) that external market pauses or reverts on withdraw (a known, historically occurring event for third-party lending pools, e.g. Aave `LendingPool` pause). Both are independent of Aera's own guardians/solvers, so this is triggered purely by ordinary user redeem calls plus a faulty/paused external dependency the vault relies on.

### Recommendation
Wrap the `submit` call in `_pullFundsIfNeeded` in a try/catch (mirroring the pattern already used in `refundDeposit`) so that when the yield-source pull operation fails, the redeem/withdraw path can gracefully fall back (e.g., queue the redeem as an async request, or partially fulfil from idle balance and revert only the excess) instead of unconditionally reverting the entire user-facing transaction.

### Proof of Concept
1. Deploy `ProvisionerV2` + `MultiDepositorVault` on a fork with a token configured with `pullFundsSubmitDataPointer` pointing to a `submit` payload that calls `withdraw`/`redeem` on a mock/paused Aave-style lending pool integration.
2. Push enough of the token to the yield source via a normal `deposit` so that `MULTI_DEPOSITOR_VAULT` idle balance < a subsequent redeemer's `tokensOut`.
3. Pause the mock lending pool (simulate Aave's `LendingPool.paused() == true`, causing its `withdraw` to revert).
4. Call `Provisioner.redeem(token, unitsIn, minTokensOut, receiver)` as an ordinary user.
5. Assert the transaction reverts with `Aera__SubmissionFailed` bubbled from `BaseVault.submit`, proving redeem is fully blocked and the user's funds/units are frozen while the pool remains paused, with no fallback path available in `ProvisionerV2`.

### Citations

**File:** v3/src/core/BaseVault.sol (L416-420)
```text

                //slither-disable-next-line arbitrary-send-eth
                (bool success, bytes memory result) = ctx.target.call{ value: ctx.value }(callData);
                // Requirements: check that the submission succeeded
                require(success, Aera__SubmissionFailed(i, result));
```

**File:** v3/src/core/ProvisionerV2.sol (L252-256)
```text
        // Interactions: exit vault, fallback to sender
        try IMultiDepositorVault(MULTI_DEPOSITOR_VAULT).exit(receiver, token, tokenAmount, unitsAmount, receiver) { }
        catch {
            IMultiDepositorVault(MULTI_DEPOSITOR_VAULT).exit(receiver, token, tokenAmount, unitsAmount, sender);
        }
```

**File:** v3/src/core/ProvisionerV2.sol (L593-659)
```text
    /// @inheritdoc IProvisionerV2
    function redeem(IERC20 token, uint256 unitsIn, uint256 minTokensOut, address receiver)
        external
        anyoneButVault
        nonReentrant
        solvingNotPaused(token)
        returns (uint256 tokensOut)
    {
        // Requirements: units in and min tokens out are positive
        _validateNonZeroAmounts(unitsIn, minTokensOut);
        // Requirements: receiver is not zero address
        require(receiver != address(0), Aera__ZeroAddressReceiver());
        // Requirements: check that the caller does not have its units locked
        require(userUnitsRefundableUntil[msg.sender] < block.timestamp, Aera__UnitsLocked());

        // Requirements: sync redeems are enabled
        TokenDetailsV2 storage tokenDetails = _requireSyncRedeemsEnabled(token);

        // Requirements + Interactions: validate price age and derive effective multiplier
        (uint256 effectiveMultiplier, uint256 priceTimestamp) = _prepareSyncRedeem(tokenDetails.syncRedeemMultiplier);

        // Interactions: convert units to tokens with effective multiplier (floor rounding favors protocol)
        tokensOut = _unitsToTokensFloorIfActive(token, unitsIn, effectiveMultiplier);
        // Requirements: tokens out meets min tokens out
        require(tokensOut >= minTokensOut, Aera__MinTokensOutNotMet());

        // Interactions: compute redeem value in numeraire for epoch cap accounting
        uint256 epochRedeemNumeraire =
            PRICE_FEE_CALCULATOR.convertTokenToNumeraire(MULTI_DEPOSITOR_VAULT, token, tokensOut);

        // Requirements + Effects + Interactions: sync redeem
        _syncRedeem(token, tokensOut, unitsIn, receiver, epochRedeemNumeraire, priceTimestamp);
    }

    /// @inheritdoc IProvisionerV2
    function withdraw(IERC20 token, uint256 tokensOut, uint256 maxUnitsIn, address receiver)
        external
        anyoneButVault
        nonReentrant
        solvingNotPaused(token)
        returns (uint256 unitsIn)
    {
        // Requirements: tokens out and max units in are positive
        _validateNonZeroAmounts(maxUnitsIn, tokensOut);
        // Requirements: receiver is not zero address
        require(receiver != address(0), Aera__ZeroAddressReceiver());
        // Requirements: check that the caller does not have its units locked
        require(userUnitsRefundableUntil[msg.sender] < block.timestamp, Aera__UnitsLocked());

        // Requirements: sync redeems are enabled
        TokenDetailsV2 storage tokenDetails = _requireSyncRedeemsEnabled(token);

        // Requirements + Interactions: validate price age and derive effective multiplier
        (uint256 effectiveMultiplier, uint256 priceTimestamp) = _prepareSyncRedeem(tokenDetails.syncRedeemMultiplier);

        // Interactions: convert tokens out to units in (ceil rounding favors protocol)
        unitsIn = _tokensToUnitsCeilIfActive(token, tokensOut, effectiveMultiplier);
        // Requirements: units in does not exceed max units in
        require(unitsIn <= maxUnitsIn, Aera__MaxUnitsInExceeded());

        // Interactions: compute redeem value in numeraire for epoch cap accounting
        uint256 epochRedeemNumeraire =
            PRICE_FEE_CALCULATOR.convertTokenToNumeraire(MULTI_DEPOSITOR_VAULT, token, tokensOut);

        // Requirements + Effects + Interactions: sync redeem
        _syncRedeem(token, tokensOut, unitsIn, receiver, epochRedeemNumeraire, priceTimestamp);
    }
```

**File:** v3/src/core/ProvisionerV2.sol (L942-965)
```text
    /// @notice Pull funds from yield source if vault idle balance is insufficient
    /// @param token The redeem token
    /// @param tokensOut The amount of tokens needed for the redeem
    function _pullFundsIfNeeded(IERC20 token, uint256 tokensOut) internal {
        // Interactions: check vault's idle balance of the redeem token
        uint256 idleBalance = token.balanceOf(MULTI_DEPOSITOR_VAULT);
        if (idleBalance >= tokensOut) return;

        // Requirements: pull-funds submit data must be configured
        address pointer = tokensDetails[token].pullFundsSubmitDataPointer;
        require(pointer != address(0), Aera__PullFundsSubmitDataNotSet());

        // Effects: compute shortfall and store in transient storage
        uint256 shortfall;
        unchecked {
            // Unchecked: idleBalance < tokensOut checked above
            shortfall = tokensOut - idleBalance;
        }
        _storeAmount(shortfall);

        // Interactions: read submit data from SSTORE2 and call vault.submit (reverts on failure)
        bytes memory data = SSTORE2.read(pointer);
        IBaseVault(MULTI_DEPOSITOR_VAULT).submit(data);
    }
```

**File:** v3/src/core/ProvisionerV2.sol (L967-984)
```text
    /// @notice Push deposited funds to yield source if configured
    /// @param tokenDetails The token details storage reference
    /// @param tokensIn The amount of tokens deposited
    function _pushFundsIfConfigured(TokenDetailsV2 storage tokenDetails, uint256 tokensIn) internal {
        // Interactions: check if push-funds submit data is configured (packed in tokensDetails slot)
        address pointer = tokenDetails.pushFundsSubmitDataPointer;
        if (pointer == address(0)) return;

        // Effects: store amount in transient storage
        _storeAmount(tokensIn);

        // Interactions: read submit data from SSTORE2 and call vault.submit (swallow failures)
        bytes memory data = SSTORE2.read(pointer);
        // solhint-disable no-unchecked-calls
        // slither-disable-next-line unchecked-lowlevel
        MULTI_DEPOSITOR_VAULT.call(abi.encodeCall(IBaseVault.submit, (data)));
        // solhint-enable no-unchecked-calls
    }
```

**File:** v3/src/core/ProvisionerV2.sol (L1024-1049)
```text
    function _syncRedeem(
        IERC20 token,
        uint256 tokensOut,
        uint256 unitsIn,
        address receiver,
        uint256 epochRedeemNumeraire,
        uint256 priceTimestamp
    ) internal {
        // Effects: roll epoch if PFC timestamp changed
        uint256 epochRedeemedNumeraire = _rollEpochIfNeeded(priceTimestamp);

        // Interactions: compute epoch cap
        (uint256 epochCapNumeraire,) = _computeEpochCap();

        // Requirements + Effects: check epoch cap and track redemption
        _requireEpochCapNotExceeded(epochRedeemNumeraire, epochCapNumeraire, epochRedeemedNumeraire);

        // Interactions: pull funds from yield source if vault idle balance is insufficient
        _pullFundsIfNeeded(token, tokensOut);

        // Interactions: exit vault — burns units from caller, sends tokens to receiver
        IMultiDepositorVault(MULTI_DEPOSITOR_VAULT).exit(msg.sender, token, tokensOut, unitsIn, receiver);

        // Log redeemed event
        emit Redeemed(msg.sender, receiver, token, unitsIn, tokensOut);
    }
```
