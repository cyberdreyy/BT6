### Title
Swapping `MultiDepositorVault`'s `provisioner` freezes in-flight requests still escrowed in the old `ProvisionerV2` - ([File: v3/src/core/MultiDepositorVault.sol], [File: v3/src/core/ProvisionerV2.sol])

### Summary
`MultiDepositorVault.setProvisioner()` lets governance rotate the `provisioner` address at any time, but `ProvisionerV2` is not upgrade-aware: it references `MULTI_DEPOSITOR_VAULT` as an immutable and calls `enter`/`exit` on it as `address(this)`. Once the vault's `provisioner` storage variable points to a *new* `ProvisionerV2` instance, every vault-touching call still originating from the *old* `ProvisionerV2` (which holds tokens/units escrowed for requests placed before the swap) fails the `onlyProvisioner` check on the vault, because `msg.sender` (old provisioner) no longer equals `provisioner` (new provisioner). This mirrors the disclosed TellerV2 issue where rotating `collateralManager` stranded state that lived in the old manager.

### Finding Description
`MultiDepositorVault.enter`/`exit` are gated by `onlyProvisioner`, which checks `msg.sender == provisioner` [1](#0-0) . `provisioner` is a plain mutable storage slot updatable at any time via `setProvisioner`/`_setProvisioner` [2](#0-1) [3](#0-2) .

Meanwhile `ProvisionerV2` hardcodes the vault address as an immutable and always calls `IMultiDepositorVault(MULTI_DEPOSITOR_VAULT).enter(...)`/`.exit(...)` from itself [4](#0-3) . Ordinary users interact with `ProvisionerV2` via `requestDeposit`/`requestRedeem`, which escrow the user's tokens or vault units inside the provisioner contract and record a hash in `asyncRequestHashes` [5](#0-4) [6](#0-5) .

If governance later calls `setProvisioner(newProvisioner)` on the vault (a normal, expected operational action, e.g. deploying `ProvisionerV2` v2.1), any request still pending in the *old* `ProvisionerV2` breaks in every code path that calls the vault's `enter`/`exit`:
- `solveRequestsVault` → `_solveDepositVaultAutoPrice`/`_solveDepositVaultFixedPrice`/`_solveRedeemVaultAutoPrice`/`_solveRedeemVaultFixedPrice` all call `IMultiDepositorVault(MULTI_DEPOSITOR_VAULT).enter/exit(address(this), ...)` [7](#0-6) [8](#0-7) . Since `msg.sender` is the old provisioner, `onlyProvisioner` on the (now-updated) vault reverts with `Aera__CallerIsNotProvisioner`, so solving via the vault path is entirely dead for the old provisioner's queued requests.
- `cancelRequest`'s pre-deadline redeem cancellation-fee burn calls `IMultiDepositorVault(MULTI_DEPOSITOR_VAULT).exit(address(this), token, 0, cancellationFee, address(this))` whenever a nonzero cancellation fee applies [9](#0-8) . This call also reverts under the same `onlyProvisioner` mismatch, causing the entire `cancelRequest` transaction (and the user's self-cancel) to revert as long as the fee is configured to be nonzero, which is the normal production configuration.

### Impact Explanation
Users with pending async redeem requests in the old `ProvisionerV2` who try to self-cancel before the deadline are blocked outright (`cancelRequest` reverts) whenever a cancellation fee is configured, and the vault-mediated solving path (`solveRequestsVault`) is permanently disabled for those legacy requests. This is a freeze of the user's escrowed vault units for the remaining lifetime of the request (they cannot access the funds through the intended self-service cancellation flow) - matching Immunefi's "temporary freezing of user funds" impact class.

### Likelihood Explanation
Requires only two ordinary conditions: (1) governance performs a routine `setProvisioner` rotation (a supported, documented lifecycle action, not a misuse of privilege) while (2) a normal user has an outstanding `requestRedeem` and calls the standard `cancelRequest` function before the deadline with the standard nonzero cancellation-fee configuration. No malicious or colluding privileged actor is required; this is triggered purely by legitimate protocol operation plus a completely ordinary user call.

### Recommendation
Make `ProvisionerV2` upgrade-safe with respect to vault-held state: either (a) have the vault's `onlyProvisioner` check accept a list/mapping of authorized (including retired) provisioners rather than a single mutable address, or (b) require the old `ProvisionerV2` to fully drain/migrate all outstanding `asyncRequestHashes` (and escrowed balances) before `setProvisioner` can point the vault at a new provisioner, or (c) have `MultiDepositorVault.enter/exit` authorize by role rather than a single storage address so a retired provisioner instance retains the ability to settle its own outstanding requests.

### Proof of Concept
1. Deploy `MultiDepositorVault`, `ProvisionerV2` (`provisionerA`), set `vault.setProvisioner(provisionerA)`, configure a token with `asyncRedeemEnabled = true` and set `_redeemCancellationsEnabled = true` with a nonzero `_redeemCancellationFeeNumeraire`/dynamic fee via `setCancellationDetails`.
2. As a normal user, deposit into the vault to receive units, then call `provisionerA.requestRedeem(token, unitsIn, minTokensOut, tip, deadline, maxPriceAge, isFixedPrice)`, escrowing units in `provisionerA`.
3. As governance, deploy a new `ProvisionerV2` (`provisionerB`) and call `vault.setProvisioner(provisionerB)`.
4. As the same user, before `deadline`, call `provisionerA.cancelRequest(token, request)`.
5. Assert the call reverts with `Aera__CallerIsNotProvisioner` (bubbled from the vault's `onlyProvisioner` check during the fee-burn `exit` call), proving the user's escrowed units cannot be reclaimed through the intended flow while `provisionerA` remains the escrow holder but is no longer the vault's registered provisioner.

### Citations

**File:** v3/src/core/MultiDepositorVault.sol (L32-36)
```text
    modifier onlyProvisioner() {
        // Requirements: check that the caller is the provisioner
        require(msg.sender == provisioner, Aera__CallerIsNotProvisioner());
        _;
    }
```

**File:** v3/src/core/MultiDepositorVault.sol (L92-97)
```text
    /// @notice Sets the provisioner address
    /// @param provisioner_ The new provisioner address
    function setProvisioner(address provisioner_) external requiresAuth {
        // Effects: set the provisioner
        _setProvisioner(provisioner_);
    }
```

**File:** v3/src/core/MultiDepositorVault.sol (L143-154)
```text
    /// @notice Set the provisioner
    /// @param provisioner_ The provisioner address
    function _setProvisioner(address provisioner_) internal {
        // Requirements: check that the provisioner is not zero
        require(provisioner_ != address(0), Aera__ZeroAddressProvisioner());

        // Effects: set the provisioner
        provisioner = provisioner_;

        // Log that provisioner was set
        emit ProvisionerSet(provisioner_);
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

**File:** v3/src/core/ProvisionerV2.sol (L362-374)
```text
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
```

**File:** v3/src/core/ProvisionerV2.sol (L781-795)
```text
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

**File:** v3/src/core/ProvisionerV2.sol (L828-843)
```text

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

```

**File:** v3/src/core/ProvisionerV2.sol (L1100-1105)
```text
            // Effects: unset hash as used
            asyncRequestHashes[depositHash] = false;
            // Interactions: enter vault and route units to receiver
            IMultiDepositorVault(MULTI_DEPOSITOR_VAULT)
                .enter(address(this), token, tokensAfterTip, unitsOut, request.receiver);

```

**File:** v3/src/core/ProvisionerV2.sol (L1280-1286)
```text
            // Effects: unset hash as used
            asyncRequestHashes[redeemHash] = false;
            // Interactions: exit vault
            IMultiDepositorVault(MULTI_DEPOSITOR_VAULT)
                .exit(address(this), token, tokenOut, request.units, address(this));
            // Interactions: transfer tokens from provisioner to receiver
            token.safeTransfer(request.receiver, request.tokens);
```
