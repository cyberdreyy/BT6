### Title
Blacklisted `receiver` in `ProvisionerV2.solveRequestsVault` batch causes hard revert that freezes unrelated users' pending deposit/redeem requests - ([File: v3/src/core/ProvisionerV2.sol])

### Summary
`ProvisionerV2._solveRequestsVault` iterates a solver-supplied `requests` array and, for redeem requests that are still within their deadline, performs a hard `token.safeTransfer(request.receiver, ...)` with no failure fallback [1](#0-0) . If `request.receiver` is an address blacklisted by a blacklistable `holdToken` such as USDC/USDT, this transfer reverts, and because the loop is a single unguarded internal call chain (no try/catch), the entire `solveRequestsVault` transaction reverts — blocking the solving of every other, unrelated deposit/redeem request batched in the same call. This mirrors the referenced Sherlock report's root cause: a hard token transfer to a potentially blacklisted party embedded inside a multi-item processing loop, where one bad recipient DoSes the whole batch.

### Finding Description
`requestRedeem`/`requestDeposit` let any ordinary user create an async request and freely choose an arbitrary `receiver` address, which is not validated against token-level blacklist status [2](#0-1) [3](#0-2) .

A solver later batches many such requests via `solveRequestsVault`, which calls the internal `_solveRequestsVault` loop [4](#0-3) . For a still-valid ("not expired") auto-price or fixed-price redeem request, the code does:
- `IMultiDepositorVault(MULTI_DEPOSITOR_VAULT).exit(...)` to burn units and pull `holdToken` to the provisioner, then
- `token.safeTransfer(request.receiver, tokenOutAfterTip)` (auto price) / `token.safeTransfer(request.receiver, request.tokens)` (fixed price) — a plain hard transfer with **no** try/catch or fallback [5](#0-4) [6](#0-5) .

Notably, the codebase already recognizes and mitigates this exact risk for the *expired/refund* branch of the same functions, using `_transferWithFallback`, which retries to a fallback `requester` address if the primary `safeTransfer` to `receiver` fails [7](#0-6) . This fallback is applied in `_clearHashAndTransferRefund`, `cancelRequest`, `refundRequest`, and the "deadline passed" branches of `_solveDepositVaultAutoPrice`/`_solveRedeemVaultAutoPrice`/`_solveRedeemVaultFixedPrice` [8](#0-7) . However, the "solved" (not-yet-expired) success path for redeems at lines 1231 and 1286 was overlooked and uses a bare `safeTransfer` instead of `_transferWithFallback`.

Because `_solveRequestsVault` processes an array of heterogeneous requests from many different users in one transaction with no per-item isolation (no try/catch around each `_solveDepositVault*`/`_solveRedeemVault*` call), a revert triggered by one blacklisted receiver propagates out and reverts the whole batch, including all other unrelated users' deposits and redeems that were otherwise valid and ready to solve.

### Impact Explanation
This freezes yield/liquidity for all other legitimate users whose deposit/redeem requests are batched alongside the blacklisted one — none of them can be solved via `solveRequestsVault` until the solver identifies and excludes the offending request from the batch. Until it is excluded, users’ pending redemptions are stuck (temporary freeze of funds), matching a live Immunefi Medium-severity "temporary freeze of funds" classification, analogous to the cited report’s "additional debt incurred until blacklisted party is fixed" impact. An attacker can grief batches deliberately by submitting redeem requests with `receiver` set to an address already blacklisted on USDC/USDT (e.g., a sanctioned address), since there is no receiver validation preventing this.

### Likelihood Explanation
Feasibility is high and does not require any privileged role: any ordinary user can call `requestRedeem`/`requestDeposit` with an arbitrary `receiver`, including a currently-blacklisted address, and the solver (an authorized but non-malicious actor simply doing normal batch processing) will naturally include multiple pending requests in one `solveRequestsVault` call for gas efficiency. The bug only requires (1) `holdToken` being a blacklistable stablecoin (USDC/USDT — both explicitly named in scope per the report analog) and (2) at least one batched redeem request's `receiver` being blacklisted, either organically (a legitimate LP getting blacklisted after submitting a request) or adversarially (attacker deliberately choosing a known-blacklisted `receiver`).

### Recommendation
Apply the same `_transferWithFallback` pattern used in the refund branches to the "solved" success-path token transfers in `_solveRedeemVaultAutoPrice` (line ~1231) and `_solveRedeemVaultFixedPrice` (line ~1286), so a failed transfer to `request.receiver` falls back to `request.user` (or is skipped/queued) instead of reverting the entire batch. Additionally, consider isolating each request's processing in `_solveRequestsVault` with a try/catch (or `call`) so a single bad request cannot block unrelated ones, emitting a failure event (similar to `AsyncDepositDisabled`/`AsyncRedeemDisabled`) instead of halting the loop.

### Proof of Concept
1. Deploy `MultiDepositorVault` + `ProvisionerV2` on a fork with `holdToken` = real USDC (or a mock ERC20 with a `blacklist(address)`/`isBlacklisted` transfer-reverting hook mimicking USDC's `blacklister` behavior).
2. User A calls `requestRedeem(USDC, unitsA, ..., receiver = A)` — a normal, valid redeem request.
3. User B calls `requestRedeem(USDC, unitsB, ..., receiver = maliciousReceiver)`, where `maliciousReceiver` is pre-blacklisted by USDC (use USDC's real blacklist on a mainnet fork, or set the mock token's blacklist flag for `maliciousReceiver`).
4. Authorized solver calls `solveRequestsVault(USDC, [requestA, requestB], "", "")` in a single transaction.
5. Assert the transaction reverts (due to `token.safeTransfer(maliciousReceiver, ...)` reverting inside `_solveRedeemVaultAutoPrice`/`_solveRedeemVaultFixedPrice`), and that `requestA` — despite being fully valid and ready — was not solved (its `asyncRequestHashes` entry remains `true`, and User A did not receive any USDC), demonstrating funds freeze for an uninvolved user caused solely by another user's blacklisted receiver.

### Citations

**File:** v3/src/core/ProvisionerV2.sol (L763-809)
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
```

**File:** v3/src/core/ProvisionerV2.sol (L811-857)
```text
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
    }
```

**File:** v3/src/core/ProvisionerV2.sol (L868-927)
```text
    function _solveRequestsVault(IERC20 token, RequestV2[] calldata requests) internal {
        // Requirements: vault is not paused in the priceAndFeeCalculator
        require(!PRICE_FEE_CALCULATOR.isVaultPaused(MULTI_DEPOSITOR_VAULT), Aera__PriceAndFeeCalculatorVaultPaused());

        // Interactions: get price age
        uint256 priceAge = block.timestamp - PRICE_FEE_CALCULATOR.getVaultPriceTimestamp(MULTI_DEPOSITOR_VAULT);

        uint256 solverTip;
        RequestV2 calldata request;

        uint256 length = requests.length;
        TokenDetailsV2 memory tokenDetails = tokensDetails[token];
        bool depositsExist;
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

**File:** v3/src/core/ProvisionerV2.sol (L1224-1234)
```text
            // Effects: unset hash as used
            asyncRequestHashes[redeemHash] = false;
            // Interactions: exit vault
            IMultiDepositorVault(MULTI_DEPOSITOR_VAULT)
                .exit(address(this), token, tokenOut, request.units, address(this));

            // Interactions: transfer tokens from provisioner to receiver
            token.safeTransfer(request.receiver, tokenOutAfterTip);

            // Log redeem solved event
            emit RedeemSolved(redeemHash);
```

**File:** v3/src/core/ProvisionerV2.sol (L1280-1287)
```text
            // Effects: unset hash as used
            asyncRequestHashes[redeemHash] = false;
            // Interactions: exit vault
            IMultiDepositorVault(MULTI_DEPOSITOR_VAULT)
                .exit(address(this), token, tokenOut, request.units, address(this));
            // Interactions: transfer tokens from provisioner to receiver
            token.safeTransfer(request.receiver, request.tokens);

```

**File:** v3/src/core/ProvisionerV2.sol (L1356-1383)
```text
    /// @notice Clears request hash, emits refund event, and transfers amount with requester fallback
    /// @param token The token used to derive the request hash
    /// @param request The request to clear
    /// @param requester The fallback recipient when transfer to request.receiver fails
    /// @param amount The amount to transfer to the receiver (or requester on fallback)
    /// @param transferToken The token to transfer (deposit token for deposits, vault units for redeems)
    function _clearHashAndTransferRefund(
        IERC20 token,
        RequestV2 calldata request,
        address requester,
        uint256 amount,
        IERC20 transferToken
    ) internal {
        bytes32 requestHash = _getRequestHash(token, request);
        // Requirements: hash has been set
        require(asyncRequestHashes[requestHash], Aera__HashNotFound());
        // Effects: unset hash as used
        asyncRequestHashes[requestHash] = false;
        // Interactions: transfer amount to receiver, fallback to requester on transfer failure
        _transferWithFallback(transferToken, requester, request.receiver, amount);

        // Log refund event
        if (_isRequestTypeDeposit(request.requestType)) {
            emit DepositRefunded(requestHash);
        } else {
            emit RedeemRefunded(requestHash);
        }
    }
```

**File:** v3/src/core/ProvisionerV2.sol (L1390-1395)
```text
    function _transferWithFallback(IERC20 token, address requester, address receiver, uint256 amount) internal {
        // Interactions: transfer to receiver, fallback to requester on transfer failure
        if (receiver == requester || !token.trySafeTransfer(receiver, amount)) {
            token.safeTransfer(requester, amount);
        }
    }
```
