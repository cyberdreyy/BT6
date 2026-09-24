### Title
`redeem`/`withdraw` in `ProvisionerV2` can be permanently DOSed when the pre-configured `pullFundsSubmitDataPointer` yield-source withdrawal reverts due to 100% utilization - ([File: v3/src/core/ProvisionerV2.sol])

### Summary
`ProvisionerV2.redeem()` and `ProvisionerV2.withdraw()` are public, ordinary-user-callable sync exit functions that internally call `_syncRedeem`, which calls `_pullFundsIfNeeded` to top up the vault's idle balance from a configured yield source whenever idle balance is insufficient [1](#0-0) . `_pullFundsIfNeeded` executes a hardcoded `vault.submit(data)` call that "reverts on failure," meaning any failure of the underlying pull (e.g., an AAVE/Compound withdraw reverting at 100% utilization) reverts the user's entire `redeem`/`withdraw` transaction [2](#0-1) . This mirrors the reported Perennial pattern where DSU-to-USDC conversion via a reserve backed by AAVE/Compound reverts at full utilization, DOSing time-critical user-facing withdrawal/order flows.

### Finding Description
`redeem(token, unitsIn, minTokensOut, receiver)` and `withdraw(token, tokensOut, maxUnitsIn, receiver)` are guarded only by `anyoneButVault`, `nonReentrant`, and `solvingNotPaused(token)` — no privileged role is required to call them [3](#0-2) [4](#0-3) . Both converge on `_syncRedeem`, which unconditionally calls `_pullFundsIfNeeded(token, tokensOut)` before exiting the vault [5](#0-4) .

`_pullFundsIfNeeded` checks the vault's idle balance; if insufficient, it computes the shortfall, stores it in transient storage, and calls `IBaseVault(MULTI_DEPOSITOR_VAULT).submit(data)` using SSTORE2-stored operations configured via `setTokenDetails` — and per the code comment this call "reverts on failure" [6](#0-5) . If the guardian has configured `pullFundsSubmitDataPointer` to withdraw idle funds from an external yield protocol like AAVE or Compound (the exact same lending markets the Perennial report is about), that withdraw call is subject to the same "supply minus debt" liquidity limitation: at 100% utilization the withdraw reverts, and since `submit` here propagates the revert, the user's `redeem`/`withdraw` transaction fails entirely, exactly analogous to `MultiInvoker`/`Manager`'s `_unwrap`→`reserve.redeem` failing at full utilization in the referenced report.

The broken invariant is the same: an operation that should degrade gracefully (partial fill from idle balance, or delayed settlement) instead hard-reverts the entire user transaction because it is coupled to an all-or-nothing external liquidity withdrawal that adversaries can force to fail by borrowing out the lending pool.

### Impact Explanation
While idle balance is depleted (either naturally, because vaults sweep idle funds into yield strategies to maximize returns, or because an attacker intentionally drives AAVE/Compound utilization to 100%), all ordinary users attempting `redeem`/`withdraw` for the affected token are blocked. This is a temporary freeze of user funds/exit liquidity — matching the "Medium temporary freezes" impact bucket. If withdrawal is time-critical (e.g., a user trying to exit ahead of an adverse price move or before a cap/fee change), they can be forced into a worse outcome exactly like the stop-loss/take-profit degradation scenario in the source report.

### Likelihood Explanation
Requires: (1) the vault's `pullFundsSubmitDataPointer` for a token to be configured to route through an AAVE/Compound-style withdraw (a normal, expected configuration for yield-bearing vaults, not a guardian misbehaving), and (2) vault idle balance below the requested redeem amount, which is a common steady state for actively-deployed vaults. The 100% utilization condition can occur naturally or be manufactured by any actor with enough capital to borrow out the lending pool's available liquidity — no privileged access or vault role is needed to trigger the DOS once preconditions are configured. This is the same feasibility profile as the referenced report.

### Recommendation
Do not let `_pullFundsIfNeeded`'s `vault.submit` revert propagate and hard-fail the entire user redemption. Options: (a) wrap the pull-funds submit call in try/catch (as is already done for `_pushFundsIfConfigured`, which "swallows failures") and fall back to a partial/queued redeem path if the pull fails; (b) queue/convert the failed sync redeem into an async request instead of reverting; (c) cap sync redeem to available idle liquidity and require the shortfall to go through the async solver flow, which can tolerate delay without blocking the whole call.

### Proof of Concept
1. Deploy `ProvisionerV2` + `MultiDepositorVault` + a yield-strategy `submit` operation for `token` that calls an AAVE-V3-style pool `withdraw()` (or a mock lending pool that reverts when `amount > available liquidity`).
2. Configure `tokensDetails[token].pullFundsSubmitDataPointer` via `setTokenDetails` to point to SSTORE2 data encoding a `withdraw` call on the mock lending pool.
3. Have a user deposit and vault sweeps most of `token` into the mock lending pool (idle balance < deposit).
4. Simulate 100% utilization: another borrower takes out debt against the mock pool so `available liquidity ≈ 0`.
5. Ordinary user calls `redeem(token, unitsIn, minTokensOut, receiver)` (or `withdraw`) requesting more than idle balance.
6. Assert the transaction reverts due to the mock pool's `withdraw` reverting inside `_pullFundsIfNeeded` → `vault.submit`, confirming the entire redeem is DOSed rather than degrading gracefully.
7. Repay/free the mock pool liquidity and show the same `redeem` call now succeeds, confirming the DOS window is real and price/timing-dependent.

### Citations

**File:** v3/src/core/ProvisionerV2.sol (L594-625)
```text
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
```

**File:** v3/src/core/ProvisionerV2.sol (L628-659)
```text
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
