### Title
Unprotected recipient transfer in active-request solve paths of `ProvisionerV2` allows a blacklisted/malicious receiver to permanently DoS the async solving queue - ([File: v3/src/core/ProvisionerV2.sol])

### Summary
`_solveDepositVaultAutoPrice`, `_solveDepositVaultFixedPrice`, `_solveRedeemVaultAutoPrice`, and `_solveRedeemVaultFixedPrice` in `ProvisionerV2.sol` only use the fallback-protected `_transferWithFallback` helper on the *expired/refund* branch. On the *active* (pre-deadline) branch they call `token.safeTransfer(request.receiver, ...)` directly with no fallback, inside a loop (`_solveRequestsVault`) that is not wrapped in try/catch per-request. A user can create a deposit/redeem request with `receiver` set to an address that is (or becomes) blacklisted by the underlying ERC20 (e.g. USDC), causing the transfer to revert and reverting the *entire* `solveRequestsVault` batch, blocking solving of all other unrelated requests bundled in the same transaction.

### Finding Description
`requestDeposit`/`requestRedeem` let the caller freely choose a `receiver` distinct from `msg.sender`/`request.user` [1](#0-0) . When a solver later batches these requests through `solveRequestsVault` → `_solveRequestsVault`, each request is processed in a `for` loop with no isolation between iterations [2](#0-1) .

For requests whose `deadline` has not yet passed, the deposit/redeem solve helpers route funds straight to `request.receiver` with a raw `safeTransfer`/`enter`/`exit` call and **no fallback**:
- Redeem fixed-price active branch: `token.safeTransfer(request.receiver, request.tokens);` [3](#0-2) 
- Redeem auto-price active branch: `token.safeTransfer(request.receiver, tokenOutAfterTip);` [4](#0-3) 
- Deposit active branches: `.enter(address(this), token, tokensNeeded/tokensAfterTip, unitsOut/units, request.receiver);` [5](#0-4) [6](#0-5)  (and `enter`/`exit` calls `safeTransferFrom`/`safeTransfer` internally in `MultiDepositorVault` [7](#0-6) ).

By contrast, the expired/refund path in these very same helpers explicitly uses the fallback-protected helper: `_transferWithFallback(token, request.user, request.receiver, request.tokens);` [8](#0-7) , and `_transferWithFallback` itself falls back to the requester if the receiver transfer fails: [9](#0-8) . This asymmetry shows the developers were aware of the blacklist/revert risk and mitigated it only for the "deadline passed" path, leaving the "active/happy path" unprotected.

Because `solveRequestsVault` has no per-request try/catch (unlike the `postSolveSubmitData` call, which is explicitly wrapped with a raw `.call` to swallow failures [10](#0-9) ), any single reverting transfer aborts the whole transaction, reverting state changes for every other request batched in that call.

### Impact Explanation
A malicious user submits (or is the receiver of) a redeem/deposit request for a blacklistable asset (e.g. USDC) and gets that receiver address blacklisted. As soon as a solver includes this request together with other legitimate pending requests in a `solveRequestsVault` batch, the whole batch reverts, freezing the processing of all bundled legitimate users' deposits/redemptions until the solver manually identifies and excludes the poisoned request. This is a temporary freezing of user funds/yield realization (delayed redemption/deposit settlement) affecting real, deployed token flows (USDC is an explicitly supported blacklist-capable asset), matching an Immunefi Medium "temporary freezing of funds" impact.

### Likelihood Explanation
Feasible with only an ordinary user transaction: create a request with `receiver` set to an address that is/can become blacklisted by the token issuer (no privileged role needed), then wait for a solver to batch-process pending requests, which is a normal permissionless/keeper operation flow (`solveRequestsVault` is `requiresAuth`, but it is the routine expected happy-path operation, and the attacker only needs to get one request queued to poison whichever batch a solver later constructs).

### Recommendation
Apply the same `_transferWithFallback` pattern (or wrap the recipient-facing transfer in try/catch with fallback to `request.user`) in the active-branch of `_solveDepositVaultAutoPrice`, `_solveDepositVaultFixedPrice`, `_solveRedeemVaultAutoPrice`, and `_solveRedeemVaultFixedPrice`, so a blacklisted/failing receiver cannot revert the entire batch; alternatively, isolate each request's solve logic behind a try/catch inside `_solveRequestsVault` so one failing request cannot block unrelated requests in the same call.

### Proof of Concept
1. Deploy/fork mainnet with `ProvisionerV2` configured for USDC with `asyncRedeemEnabled = true`.
2. User A calls `requestRedeem(USDC, unitsIn, minTokensOut, 0, deadline, maxPriceAge, true, receiverX)` where `receiverX` is an address already on USDC's blacklist (or use a mock ERC20 with a `blacklist` mapping causing `transfer` to revert for `receiverX`, to simulate USDC behavior).
3. User B calls `requestRedeem(USDC, ..., receiver = userB)` — a normal legitimate request.
4. Solver calls `solveRequestsVault(USDC, [requestA, requestB], "", "")` before either deadline expires.
5. Assert the transaction reverts (because `_solveRedeemVaultFixedPrice`/`AutoPrice`'s `token.safeTransfer(receiverX, ...)` reverts), even though User B's redeem should have succeeded — demonstrating that User B's legitimate redemption is blocked/frozen by User A's poisoned request until the solver manually removes it from the batch.

### Citations

**File:** v3/src/core/ProvisionerV2.sol (L400-406)
```text
        // Interactions: post-solve submit (swallow failures)
        if (postSolveSubmitData.length > 0) {
            // solhint-disable no-unchecked-calls
            // slither-disable-next-line unchecked-lowlevel
            MULTI_DEPOSITOR_VAULT.call(abi.encodeCall(IBaseVault.submit, (postSolveSubmitData)));
            // solhint-enable no-unchecked-calls
        }
```

**File:** v3/src/core/ProvisionerV2.sol (L811-827)
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

**File:** v3/src/core/ProvisionerV2.sol (L1100-1104)
```text
            // Effects: unset hash as used
            asyncRequestHashes[depositHash] = false;
            // Interactions: enter vault and route units to receiver
            IMultiDepositorVault(MULTI_DEPOSITOR_VAULT)
                .enter(address(this), token, tokensAfterTip, unitsOut, request.receiver);
```

**File:** v3/src/core/ProvisionerV2.sol (L1108-1114)
```text
        } else {
            // Effects: unset hash as used
            asyncRequestHashes[depositHash] = false;
            // Interactions: transfer tokens from provisioner to receiver, fallback to requester on transfer failure
            _transferWithFallback(token, request.user, request.receiver, request.tokens);
            // Log deposit refunded event
            emit DepositRefunded(depositHash);
```

**File:** v3/src/core/ProvisionerV2.sol (L1157-1162)
```text
            // Effects: unset hash as used
            asyncRequestHashes[depositHash] = false;
            // Interactions: enter vault and route units to receiver
            IMultiDepositorVault(MULTI_DEPOSITOR_VAULT)
                .enter(address(this), token, tokensNeeded, request.units, request.receiver);

```

**File:** v3/src/core/ProvisionerV2.sol (L1226-1231)
```text
            // Interactions: exit vault
            IMultiDepositorVault(MULTI_DEPOSITOR_VAULT)
                .exit(address(this), token, tokenOut, request.units, address(this));

            // Interactions: transfer tokens from provisioner to receiver
            token.safeTransfer(request.receiver, tokenOutAfterTip);
```

**File:** v3/src/core/ProvisionerV2.sol (L1283-1286)
```text
            IMultiDepositorVault(MULTI_DEPOSITOR_VAULT)
                .exit(address(this), token, tokenOut, request.units, address(this));
            // Interactions: transfer tokens from provisioner to receiver
            token.safeTransfer(request.receiver, request.tokens);
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

**File:** v3/src/core/MultiDepositorVault.sol (L61-90)
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

    /// @inheritdoc IMultiDepositorVault
    function exit(address sender, IERC20 token, uint256 tokenAmount, uint256 unitsAmount, address recipient)
        external
        whenNotPaused
        onlyProvisioner
    {
        // Effects: burn units from the sender
        _burn(sender, unitsAmount);

        // Interactions: transfer tokens to the recipient
        if (tokenAmount > 0) token.safeTransfer(recipient, tokenAmount);

        // Log the exit event
        emit Exit(sender, recipient, token, tokenAmount, unitsAmount);
    }
```
