### Title
Cancelled/refunded async deposit and redeem requests in `ProvisionerV2` send funds to `request.receiver` instead of the paying `request.user`, causing fund loss on misdirected/unowned receiver addresses - (File: v3/src/core/ProvisionerV2.sol)

### Summary
`requestDeposit`/`requestRedeem` in `ProvisionerV2` let `msg.sender` (the payer) specify an arbitrary `receiver` address that only needs to be non-zero, with no requirement that `receiver == msg.sender` or that `receiver` has approved the sender (unlike sync `deposit`/`mint`, which enforce `_requireValidReceiver`). [1](#0-0)  When the request is later self-cancelled via `cancelRequest` (payer-only, gated by `msg.sender == request.user`) or expires and is refunded via `refundRequest`, the escrowed tokens/units are routed to `request.receiver` via `_clearHashAndTransferRefund`, only falling back to the payer if the transfer to `receiver` reverts. [2](#0-1)  This is the exact root-cause pattern from the Dinari report: the entity that authorized/paid for the cancellation is not the entity that receives the refunded escrow.

### Finding Description
1. An ordinary user (payer) calls `requestDeposit(token, tokensIn, minUnitsOut, solverTip, deadline, maxPriceAge, isFixedPrice, receiver)` with `receiver` set to some address they do not fully control (e.g., a mistyped address, an exchange deposit address, or an address they mistakenly believe is theirs). Tokens are pulled from `msg.sender` into the provisioner: `token.safeTransferFrom(msg.sender, address(this), tokensIn)`, and the request hash binds `msg.sender` as `request.user` and the chosen `receiver` separately. [3](#0-2) 
2. The only guard on `receiver` is non-zero-address and deadline/tip sanity checks in `_validateRequest`; there is no ownership/approval check as exists for sync deposits (`_requireValidReceiver`). [4](#0-3) 
3. The payer (`request.user`) then calls `cancelRequest(token, request)`. The only authorization check is `msg.sender == request.user`, which passes since the payer is cancelling their own request. [5](#0-4) 
4. `cancelRequest` (and identically `refundRequest` for expired requests) calls `_clearHashAndTransferRefund(token, request, requester, refundAmount, transferToken)`, which invokes `_transferWithFallback(transferToken, requester, request.receiver, amount)` — sending the refund to `request.receiver`, not to `requester`/`request.user`, unless the transfer to `receiver` itself fails. [6](#0-5) [7](#0-6) 
5. If `receiver` is a valid, receive-capable address the payer does not control (not simply address(0) or a reverting contract), the fallback never triggers, and the payer permanently loses the escrowed `tokenAmount`/`unitsAmount` to that receiver upon their own cancellation.

This mirrors the Dinari `SellOrderProcessor`/`BuyOrderIssuer` bug precisely: cancellation refunds go to the "recipient" field instead of the paying "requester" field, and the payer has no way to redirect funds to themselves once they realize the receiver is wrong.

### Impact Explanation
An ordinary user who creates an async deposit or redeem request with an incorrect/foreign `receiver` and then cancels (or lets it expire and calls `refundRequest`) has their escrowed principal (deposit tokens or vault units) sent to that receiver instead of back to themselves — a direct, permanent loss of user funds. This matches Immunefi Medium severity for "temporary freezing"/loss-of-funds-on-cancellation style issues, and here it is worse than temporary: funds go to a third party with no recovery path if the receiver doesn't return them voluntarily.

### Likelihood Explanation
Fully triggerable by an ordinary user with no privileged role: it only requires calling the public `requestDeposit`/`requestRedeem` overload with an `receiver` parameter, followed by a self-service `cancelRequest` (or waiting past `deadline` and calling public `refundRequest`). No guardian, solver, oracle, or admin involvement is needed; the vulnerable code path (choose non-self receiver, then cancel/expire) is entirely user-driven and always reachable since `_depositCancellationsEnabled`/`_redeemCancellationsEnabled` are the only toggles, both intended to be on for normal operation.

### Recommendation
In `_clearHashAndTransferRefund` (and the parallel refund paths in `_solveDepositVaultAutoPrice`, `_solveDepositVaultFixedPrice`, `_solveRedeemVaultAutoPrice`, `_solveRedeemVaultFixedPrice`, `_solveRequestDirect`), route cancellation/expiry refunds to `request.user` (the payer) instead of `request.receiver`, since `receiver` is only meant to receive successfully solved proceeds, not returned escrow. Alternatively, require `receiver == msg.sender` (or an explicit approval mechanism analogous to `depositReceiverApprovals`) for async `requestDeposit`/`requestRedeem`, consistent with the sync `deposit`/`mint` receiver validation via `_requireValidReceiver`.

### Proof of Concept
1. Fork mainnet at the block where `ProvisionerV2` and `MultiDepositorVault` are deployed and configured with `asyncDepositEnabled = true` and `_depositCancellationsEnabled = true` for a whitelisted token.
2. As `payer` (an ordinary EOA with token balance and approval to `Provisioner`), call `provisioner.requestDeposit(token, tokensIn, minUnitsOut, 0, deadline, maxPriceAge, false, receiverAddr)` where `receiverAddr` is a fresh EOA controlled by a different party (e.g. `attackerOrWrongAddr`), not approved via `setDepositReceiverApproval`. Assert `token.balanceOf(payer)` decreased by `tokensIn` and provisioner holds the tokens.
3. As `payer`, call `provisioner.cancelRequest(token, request)` with the matching `RequestV2` struct (before `deadline`, accepting any configured cancellation fee).
4. Assert `token.balanceOf(receiverAddr)` increased by the refunded amount and `token.balanceOf(payer)` did NOT increase — confirming the payer's own cancellation sent their escrowed funds to a third-party `receiver` instead of back to themselves.
5. Repeat with `refundRequest` after `deadline` elapses to show the same misdirection on expiry-based refunds.

### Citations

**File:** v3/src/core/ProvisionerV2.sol (L304-319)
```text
    /// @inheritdoc IProvisionerV2
    function refundRequest(IERC20 token, RequestV2 calldata request) external nonReentrant {
        // Requirements: deadline is in the past or authorized
        require(
            request.deadline < block.timestamp || isAuthorized(msg.sender, msg.sig),
            Aera__DeadlineInFutureAndUnauthorized()
        );

        if (_isRequestTypeDeposit(request.requestType)) {
            // Effects + interactions: clear hash, emit refund event, and transfer full token amount with fallback
            _clearHashAndTransferRefund(token, request, request.user, request.tokens, token);
        } else {
            // Effects + interactions: clear hash, emit refund event, and transfer full unit amount with fallback
            _clearHashAndTransferRefund(token, request, request.user, request.units, IERC20(MULTI_DEPOSITOR_VAULT));
        }
    }
```

**File:** v3/src/core/ProvisionerV2.sol (L323-348)
```text
    function cancelRequest(IERC20 token, RequestV2 calldata request) external nonReentrant {
        // Requirements: self-cancel caller must be request user
        require(msg.sender == request.user, Aera__CallerIsNotRequestUser());

        if (_isRequestTypeDeposit(request.requestType)) {
            // Requirements: deposit cancellation toggle must be enabled
            require(_depositCancellationsEnabled, Aera__DepositRequestCancellationDisabled());

            uint256 refundAmount = request.tokens;
            // Requirements: pre-deadline self-cancel applies cancellation fee
            if (block.timestamp < request.deadline) {
                uint256 cancellationFee = _computeDepositCancellationFeeTokens(token);
                // Requirements: cancellation fee cannot exceed escrowed amount
                require(cancellationFee <= refundAmount, Aera__CancellationFeeExceedsRequestAmount());
                if (cancellationFee != 0) {
                    unchecked {
                        // unchecked: safe because cancellationFee <= refundAmount is enforced above
                        refundAmount -= cancellationFee;
                    }
                    // Interactions: transfer cancellation fee to vault
                    token.safeTransfer(MULTI_DEPOSITOR_VAULT, cancellationFee);
                }
            }

            // Effects + interactions: clear hash, emit refund event, and transfer net token amount with fallback
            _clearHashAndTransferRefund(token, request, msg.sender, refundAmount, token);
```

**File:** v3/src/core/ProvisionerV2.sol (L763-794)
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
```

**File:** v3/src/core/ProvisionerV2.sol (L1356-1375)
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
```

**File:** v3/src/core/ProvisionerV2.sol (L1496-1535)
```text
    /// @notice Reverts if the caller is not the receiver and the receiver has not approved the caller
    /// @param receiver The address to validate
    function _requireValidReceiver(address receiver) internal view {
        // Requirements: caller is receiver or receiver has approved caller
        require(receiver == msg.sender || depositReceiverApprovals[receiver][msg.sender], Aera__ReceiverNotApproved());
    }

    /// @notice Reverts if the solving gate is set and reports that solving is paused
    /// @param token The ERC20 token being solved
    function _checkSolvingNotPaused(IERC20 token) internal view {
        if (SOLVING_GATE_ENABLED) {
            address gate = solvingGate;
            require(gate == address(0) || !ISolvingGate(gate).paused(address(this), token), Aera__SolvingPaused());
        }
    }

    /// @notice Reverts if the caller is the vault
    function _checkCallerNotVault() internal view {
        // Requirements: check that the caller is not the vault
        require(msg.sender != MULTI_DEPOSITOR_VAULT, Aera__CallerIsVault());
    }

    /// @notice Validates common request parameters shared by requestDeposit and requestRedeem
    /// @param receiver The address receiving funds when request is solved
    /// @param solverTip The tip offered to the solver
    /// @param deadline Timestamp until which the request is valid
    /// @param isFixedPrice Whether the request is a fixed price request
    function _validateRequest(address receiver, uint256 solverTip, uint256 deadline, bool isFixedPrice) internal view {
        // Requirements: receiver is not zero address
        require(receiver != address(0), Aera__ZeroAddressReceiver());
        // Requirements: deadline is in the future and not too far in the future
        require(deadline > block.timestamp, Aera__DeadlineInPast());
        unchecked {
            require(deadline - block.timestamp <= MAX_SECONDS_TO_DEADLINE, Aera__DeadlineTooFarInFuture());
        }
        // Requirements: vault is not paused in the PriceAndFeeCalculator
        require(!PRICE_FEE_CALCULATOR.isVaultPaused(MULTI_DEPOSITOR_VAULT), Aera__PriceAndFeeCalculatorVaultPaused());
        // Requirements: fixed price requests cannot have a solver tip
        require(solverTip == 0 || !isFixedPrice, Aera__FixedPriceSolverTipNotAllowed());
    }
```
