## Analog Found

This report's pattern is a mutable cross-contract binding (`Comptroller.uniV3LpVault`) that other functions assume stays in sync, causing in-flight positions to become unusable when the admin repoints it. The direct analog in Aera's V3 code is `MultiDepositorVault.setProvisioner`, which lets the admin repoint the vault's authorized `provisioner` while a *previous* `ProvisionerV2` instance is still holding escrowed user funds tied to pending async redeem requests.

### Root cause

`MultiDepositorVault.exit()` is gated by `onlyProvisioner`, which checks `msg.sender == provisioner` against the **current, mutable** `provisioner` storage variable, not against the immutable `MULTI_DEPOSITOR_VAULT` reference each `ProvisionerV2` instance holds: [1](#0-0) [2](#0-1) 

Meanwhile `ProvisionerV2.MULTI_DEPOSITOR_VAULT` is immutable — a given `ProvisionerV2` instance can never point anywhere else: [3](#0-2) 

When a user calls the fully permissionless, ordinary-user function `cancelRequest` to self-cancel a pending redeem before its deadline (with a non-zero configured cancellation fee), it calls `IMultiDepositorVault(MULTI_DEPOSITOR_VAULT).exit(...)` to burn the fee portion of units: [4](#0-3) 

If the admin has since called `setProvisioner` on the vault to point to a new `ProvisionerV2` instance (e.g. an upgrade), the old instance is no longer `provisioner`, so this `exit()` call reverts with `Aera__CallerIsNotProvisioner`. The user's escrowed units for that pending redeem request become stuck in the old provisioner until the request's `deadline` passes, at which point `refundRequest` (which transfers the escrowed vault-unit ERC20 balance directly, without calling `exit`) becomes usable instead: [5](#0-4) 

This exactly mirrors the reported pattern: a privileged, non-malicious admin action (`setProvisioner`/`_setUniV3LpVault`) that changes one half of a mutually-dependent pair of contracts, silently breaking a specific user-facing function (`cancelRequest`/`withdrawToken`) on positions/requests that predate the change, without any check preventing the admin action or migrating outstanding state.

### Caveat

Unlike the original C4 finding (permanent stuck NFTs / broken liquidation), here the freeze is **temporary and bounded**: once `request.deadline` passes, `refundRequest` fully recovers the escrowed units without touching the vault's `onlyProvisioner` gate. Deposit-side cancellations are also unaffected since their fee transfer is a plain `token.safeTransfer` to the vault, not an `enter`/`exit` call. Only pre-deadline self-cancellation of redeem requests (with `_redeemCancellationFeeNumeraire != 0`) submitted against a provisioner instance that is later superseded is affected — this matches Immunefi's "Medium: temporary freezing of funds" category rather than a permanent-loss/High finding.

### Title
Provisioner rotation via `MultiDepositorVault.setProvisioner` breaks pre-deadline self-cancellation of pending redeem requests on the superseded provisioner - ([File: v3/src/core/ProvisionerV2.sol])

### Summary
`MultiDepositorVault.setProvisioner` allows the admin to repoint the vault's authorized provisioner to a new `ProvisionerV2` instance. Any redeem request that was created against the old provisioner and is still pending can no longer be self-cancelled before its deadline, because `cancelRequest`'s fee-burn path calls `MultiDepositorVault.exit()`, which is gated by `onlyProvisioner` against the vault's current (now different) provisioner address.

### Finding Description
`requestRedeem` escrows the user's vault units inside the calling `ProvisionerV2` instance. If the admin later calls `MultiDepositorVault.setProvisioner(newProvisioner)`, the old `ProvisionerV2` instance's immutable `MULTI_DEPOSITOR_VAULT` reference still points at the vault, but the vault's `onlyProvisioner` modifier now rejects calls from it. Any user calling the permissionless `cancelRequest` on a pending redeem before its `deadline` (with a configured non-zero cancellation fee) triggers `IMultiDepositorVault(MULTI_DEPOSITOR_VAULT).exit(...)`, which reverts with `Aera__CallerIsNotProvisioner()`. No check exists in `setProvisioner` or `cancelRequest` accounting for outstanding requests in the previously-bound provisioner.

### Impact Explanation
Users with pending redeem requests against a superseded `ProvisionerV2` cannot exercise their self-cancel right until `request.deadline` passes, at which point they must instead use `refundRequest`. This is a temporary freeze of user-escrowed vault units, matching Immunefi's Medium severity "temporary freezing of user funds" category.

### Likelihood Explanation
Requires only two ordinary, in-scope preconditions: (1) a standard admin `setProvisioner` call (an intended, documented operation, not a malicious/colluding action), and (2) at least one outstanding redeem request created against the old provisioner before that rotation, with `redeemCancellationFeeNumeraire` configured non-zero. No special privileges beyond normal admin operation are required to trigger; the affected user's own permissionless transaction (`cancelRequest`) reliably reverts.

### Recommendation
Either (a) disallow calling `setProvisioner` while the old provisioner has any outstanding `asyncRequestHashes`, or (b) have `MultiDepositorVault.exit`/`enter` accept calls from any provisioner ever registered (e.g., an allow-list of historical provisioners) rather than only the current one, or (c) migrate/settle all outstanding requests atomically as part of the provisioner rotation.

### Proof of Concept
1. Deploy `MultiDepositorVault` with `ProvisionerV2` instance `P1` as `provisioner`; configure `redeemCancellationsEnabled = true` and `redeemCancellationFeeNumeraire > 0` via `setCancellationDetails`.
2. User calls `P1.requestRedeem(token, unitsIn, minTokensOut, solverTip, deadline, maxPriceAge, isFixedPrice)` — units are transferred from user to `P1`, `asyncRequestHashes[hash] = true`.
3. Admin deploys `P2` and calls `MultiDepositorVault.setProvisioner(address(P2))`.
4. Before `deadline`, user calls `P1.cancelRequest(token, request)`.
5. Assert the call reverts with `Aera__CallerIsNotProvisioner` (raised inside `MultiDepositorVault.exit` via the `onlyProvisioner` modifier), while `asyncRequestHashes[hash]` remains `true` and the user's units remain stuck in `P1`.
6. Warp past `deadline` and call `P1.refundRequest(token, request)` — assert it succeeds, confirming only a temporary (deadline-bounded) freeze.

### Citations

**File:** v3/src/core/MultiDepositorVault.sol (L28-36)
```text
    /// @notice Role that can mint/burn vault units
    address public provisioner;

    /// @notice Ensures caller is the provisioner
    modifier onlyProvisioner() {
        // Requirements: check that the caller is the provisioner
        require(msg.sender == provisioner, Aera__CallerIsNotProvisioner());
        _;
    }
```

**File:** v3/src/core/MultiDepositorVault.sol (L76-97)
```text
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

    /// @notice Sets the provisioner address
    /// @param provisioner_ The new provisioner address
    function setProvisioner(address provisioner_) external requiresAuth {
        // Effects: set the provisioner
        _setProvisioner(provisioner_);
    }
```

**File:** v3/src/core/ProvisionerV2.sol (L55-63)
```text
    /// @notice The price and fee calculator contract
    IPriceAndFeeCalculatorV2 public immutable PRICE_FEE_CALCULATOR;

    /// @notice The multi depositor vault contract
    address public immutable MULTI_DEPOSITOR_VAULT;

    /// @notice Whether this provisioner supports solving status gating
    bool public immutable SOLVING_GATE_ENABLED;

```

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

**File:** v3/src/core/ProvisionerV2.sol (L349-378)
```text
        } else {
            // Requirements: redeem cancellation toggle must be enabled
            require(_redeemCancellationsEnabled, Aera__RedeemRequestCancellationDisabled());

            // Interactions: compute redeem request size in numeraire with protocol-favoring rounding
            uint256 requestNumeraire = PRICE_FEE_CALCULATOR.convertUnitsToNumeraire(
                MULTI_DEPOSITOR_VAULT, request.units, Math.Rounding.Ceil
            );
            // Requirements: redeem self-cancel must not exceed configured cap
            require(requestNumeraire <= _redeemCancellationCapNumeraire, Aera__RequestAmountExceedsRefundCap());

            uint256 refundAmount = request.units;
            // Requirements: pre-deadline self-cancel applies cancellation fee
            if (block.timestamp < request.deadline) {
                uint256 cancellationFee = _computeRedeemCancellationFeeUnits(requestNumeraire);
                // Requirements: cancellation fee cannot exceed escrowed amount
                require(cancellationFee <= refundAmount, Aera__CancellationFeeExceedsRequestAmount());
                if (cancellationFee != 0) {
                    unchecked {
                        // unchecked: safe because cancellationFee <= refundAmount is enforced above
                        refundAmount -= cancellationFee;
                    }
                    // Interactions: burn cancellation fee units from provisioner escrow
                    IMultiDepositorVault(MULTI_DEPOSITOR_VAULT)
                        .exit(address(this), token, 0, cancellationFee, address(this));
                }
            }

            // Effects + interactions: clear hash, emit refund event, and transfer net unit amount with fallback
            _clearHashAndTransferRefund(token, request, msg.sender, refundAmount, IERC20(MULTI_DEPOSITOR_VAULT));
```
