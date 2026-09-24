### Title
Disabling a token's async deposit/redeem support strands already-submitted requests in `ProvisionerV2` - ([File: v3/src/core/ProvisionerV2.sol])

### Summary
`ProvisionerV2.setTokenDetails()`/`removeToken()` let an admin flip `asyncDepositEnabled`/`asyncRedeemEnabled` for a token at any time [1](#0-0) . Users who already called `requestDeposit`/`requestRedeem` for that token (transferring their tokens or vault units into the Provisioner and recording an `asyncRequestHash`) have no way to have their request solved or refunded once the token is disabled, because both solving entry points either revert outright or silently skip the request without unsetting its hash. This mirrors the reported UXD pattern where unwhitelisting a collateral asset traps already-open positions that can no longer be closed/redeemed.

### Finding Description
`requestDeposit`/`requestRedeem` only check that the async flag is enabled at request-creation time and immediately pull the user's tokens (deposit) or vault units (redeem) into the Provisioner, recording `asyncRequestHashes[hash] = true` [2](#0-1) .

If an authorized admin subsequently calls `setTokenDetails(token, {asyncDepositEnabled: false, asyncRedeemEnabled: false, ...})` or `removeToken(token)` [1](#0-0) , the outstanding request for that token can no longer progress through either public solving path:

- `solveRequestsDirect` explicitly `require`s the relevant enabled flag and reverts the whole call for that token if it's off [3](#0-2) .
- `solveRequestsVault` → `_solveRequestsVault` checks the same flag per request; if disabled it just emits `AsyncDepositDisabled`/`AsyncRedeemDisabled` and `continue`s to the next request — it never reaches the deadline check, never unsets `asyncRequestHashes[hash]`, and never triggers the refund branch that only exists inside `_solveDepositVaultAutoPrice`/`_solveRedeemVaultAutoPrice`/fixed-price variants [4](#0-3) .

Those refund branches (which transfer funds back after `deadline < block.timestamp`) are only reachable from inside the per-token-enabled branch of the same functions, so once the token is disabled the request is permanently unreachable by any code path that would return the escrowed tokens/units to the user. The hash stays "used" forever with no bypass path found (no unconditional self-cancel function was located that ignores the token's enabled flags).

### Impact Explanation
Users' tokens (pending deposit) or vault units (pending redeem) that were already escrowed in `ProvisionerV2` for a token whose async flag is later disabled become permanently stuck with no code path to reclaim them — a full freezing of user funds, not merely a temporary block, matching Immunefi Medium (or higher) freezing-of-funds severity.

### Likelihood Explanation
Requires only a routine/expected admin action (`setTokenDetails` or `removeToken`, both `requiresAuth`) performed while user requests are pending for that token — no collusion or malicious intent needed; this is normal operational risk management (e.g., deprecating a collateral) that an ordinary user's already-submitted request would be caught by.

### Recommendation
Ensure disabling a token still allows already-created requests to be refunded: either (a) allow the deadline/refund branch to execute regardless of the current enabled flag (only gate acceptance of *new* requests on the flag), or (b) provide an explicit, always-available cancel/refund function for stranded requests keyed only on hash existence and deadline, independent of `tokensDetails[token]` enabled bits.

### Proof of Concept
1. Fork mainnet, deploy/attach to the live `ProvisionerV2`/`MultiDepositorVault` for a token with `asyncRedeemEnabled = true`.
2. As a user, call `requestRedeem(token, unitsIn, minTokensOut, tip, deadline, maxPriceAge, false, receiver)` — confirm units are pulled from the user and `asyncRequestHashes[hash] == true`.
3. As admin, call `setTokenDetails(token, details_with_asyncRedeemEnabled_false)`.
4. Advance time past `deadline`.
5. Call `solveRequestsVault(token, [request], "", "")` — assert `RedeemRefunded` is NOT emitted and `asyncRequestHashes[hash]` is still `true`.
6. Call `solveRequestsDirect(token, [request])` — assert it reverts with `Aera__AsyncRedeemDisabled()`.
7. Assert the user's units remain locked in `ProvisionerV2` with no available function to retrieve them.

### Citations

**File:** v3/src/core/ProvisionerV2.sol (L412-436)
```text
    function solveRequestsDirect(IERC20 token, RequestV2[] calldata requests) external nonReentrant {
        // Requirements: vault is not paused in the priceAndFeeCalculator
        require(!PRICE_FEE_CALCULATOR.isVaultPaused(MULTI_DEPOSITOR_VAULT), Aera__PriceAndFeeCalculatorVaultPaused());

        uint256 length = requests.length;
        TokenDetailsV2 storage tokenDetails = tokensDetails[token];
        for (uint256 i = 0; i < length; i++) {
            RequestV2 calldata request = requests[i];
            RequestType requestType = request.requestType;

            // Requirements: direct solves can only solve fixed price requests
            require(!_isRequestTypeAutoPrice(requestType), Aera__AutoPriceSolveNotAllowed());

            if (_isRequestTypeDeposit(requestType)) {
                // Requirements: async deposit is enabled
                require(tokenDetails.asyncDepositEnabled, Aera__AsyncDepositDisabled());
            } else {
                // Requirements: async redeem is enabled
                require(tokenDetails.asyncRedeemEnabled, Aera__AsyncRedeemDisabled());
            }

            // Requirements + Effects + Interactions: solve direct request
            _solveRequestDirect(token, request);
        }
    }
```

**File:** v3/src/core/ProvisionerV2.sol (L498-540)
```text
    /// @inheritdoc IProvisionerV2
    function setTokenDetails(IERC20 token, TokenDetailsV2 calldata details) external requiresAuth {
        // Requirements: check that the token is not the vault's own unit token
        require(address(token) != MULTI_DEPOSITOR_VAULT, Aera__InvalidToken());

        // Requirements: all multipliers are within valid range [MIN_MULTIPLIER, ONE_IN_BPS]
        _requireValidMultiplier(details.asyncDepositMultiplier);
        _requireValidMultiplier(details.asyncRedeemMultiplier);
        _requireValidMultiplier(details.syncDepositMultiplier);
        _requireValidMultiplier(details.syncRedeemMultiplier);

        // Effects: set token details
        tokensDetails[token] = details;

        if (details.syncRedeemEnabled) {
            // Requirements: sync redeem configuration must be set before enabling sync redeems on any token
            require(_syncRedeemMaxPriceAge > 0, Aera__SyncRedeemNotConfigured());
        }

        if (
            details.asyncDepositEnabled || details.asyncRedeemEnabled || details.syncDepositEnabled
                || details.syncRedeemEnabled
        ) {
            // Requirements: check that the token can be priced
            // convertUnitsToToken instead of convertTokensToUnits to avoid having to call token.decimals()
            require(
                PRICE_FEE_CALCULATOR.convertUnitsToToken(MULTI_DEPOSITOR_VAULT, token, ONE_UNIT) != 0,
                Aera__TokenCantBePriced()
            );
        }

        // Log token details set event
        emit TokenDetailsSet(token, details);
    }

    /// @inheritdoc IProvisionerV2
    function removeToken(IERC20 token) external requiresAuth {
        // Effects: remove tokensDetails
        delete tokensDetails[token];

        // Log token removed event
        emit TokenRemoved(token);
    }
```

**File:** v3/src/core/ProvisionerV2.sol (L763-856)
```text
    /// @inheritdoc IProvisionerV2
    function requestDeposit(
        IERC20 token,
        uint256 tokensIn,
        uint256 minUnitsOut,
        uint256 solverTip,
        uint256 deadline,
        uint256 maxPriceAge,
        bool isFixedPrice,
        address receiver
    ) public anyoneButVault returns (bytes32 depositHash) {
        // Requirements: token amount and min units out are positive, async deposits are enabled
        _validateNonZeroAmounts(minUnitsOut, tokensIn);
        require(tokensDetails[token].asyncDepositEnabled, Aera__AsyncDepositDisabled());

        // Requirements: common request validation
        _validateRequest(receiver, solverTip, deadline, isFixedPrice);

        RequestType requestType = _getRequestType(isFixedPrice, true);

        // Interactions: transfer tokens from sender to provisioner
        token.safeTransferFrom(msg.sender, address(this), tokensIn);

        depositHash = _getRequestHashParams(
            token, msg.sender, receiver, requestType, tokensIn, minUnitsOut, solverTip, deadline, maxPriceAge
        );

        // Requirements: hash has not been used
        require(!asyncRequestHashes[depositHash], Aera__HashCollision());

        // Effects: set hash as used
        asyncRequestHashes[depositHash] = true;

        // Log deposit requested event
        emit DepositRequested(
            msg.sender,
            receiver,
            token,
            tokensIn,
            minUnitsOut,
            solverTip,
            deadline,
            maxPriceAge,
            isFixedPrice,
            depositHash
        );
    }

    /// @inheritdoc IProvisionerV2
    function requestRedeem(
        IERC20 token,
        uint256 unitsIn,
        uint256 minTokensOut,
        uint256 solverTip,
        uint256 deadline,
        uint256 maxPriceAge,
        bool isFixedPrice,
        address receiver
    ) public anyoneButVault returns (bytes32 redeemHash) {
        // Requirements: units amount and min token out are positive, async redeems are enabled
        _validateNonZeroAmounts(unitsIn, minTokensOut);
        require(tokensDetails[token].asyncRedeemEnabled, Aera__AsyncRedeemDisabled());

        // Requirements: common request validation
        _validateRequest(receiver, solverTip, deadline, isFixedPrice);

        RequestType requestType = _getRequestType(isFixedPrice, false);

        // Interactions: transfer units from sender to provisioner
        IERC20(MULTI_DEPOSITOR_VAULT).safeTransferFrom(msg.sender, address(this), unitsIn);

        redeemHash = _getRequestHashParams(
            token, msg.sender, receiver, requestType, minTokensOut, unitsIn, solverTip, deadline, maxPriceAge
        );

        // Requirements: hash has not been used
        require(!asyncRequestHashes[redeemHash], Aera__HashCollision());

        // Effects: set hash as used
        asyncRequestHashes[redeemHash] = true;

        // Log redeem requested event
        emit RedeemRequested(
            msg.sender,
            receiver,
            token,
            minTokensOut,
            unitsIn,
            solverTip,
            deadline,
            maxPriceAge,
            isFixedPrice,
            redeemHash
        );
```

**File:** v3/src/core/ProvisionerV2.sol (L881-927)
```text
        for (uint256 i = 0; i < length; i++) {
            request = requests[i];
            if (_isRequestTypeDeposit(request.requestType)) {
                // Requirements: async deposit is enabled
                if (!tokenDetails.asyncDepositEnabled) {
                    // Log async deposit disabled event
                    emit AsyncDepositDisabled(i);
                    continue;
                }

                if (!depositsExist) {
                    depositsExist = true;
                    token.forceApprove(MULTI_DEPOSITOR_VAULT, type(uint256).max);
                }

                if (_isRequestTypeAutoPrice(request.requestType)) {
                    // Requirements + Effects + Interactions: solve auto price deposit
                    solverTip += _solveDepositVaultAutoPrice(
                        token, tokenDetails.asyncDepositMultiplier, request, priceAge, i
                    );
                } else {
                    // Requirements + Effects + Interactions: solve fixed price deposit
                    solverTip += _solveDepositVaultFixedPrice(
                        token, tokenDetails.asyncDepositMultiplier, request, priceAge, i
                    );
                }
            } else {
                // Requirements: async redeem is enabled
                if (!tokenDetails.asyncRedeemEnabled) {
                    // Log async redeem disabled event
                    emit AsyncRedeemDisabled(i);
                    continue;
                }

                if (_isRequestTypeAutoPrice(request.requestType)) {
                    // Requirements + Effects + Interactions: solve auto price redeem
                    solverTip += _solveRedeemVaultAutoPrice(
                        token, tokenDetails.asyncRedeemMultiplier, request, priceAge, i
                    );
                } else {
                    // Requirements + Effects + Interactions: solve fixed price redeem
                    solverTip += _solveRedeemVaultFixedPrice(
                        token, tokenDetails.asyncRedeemMultiplier, request, priceAge, i
                    );
                }
            }
        }
```
