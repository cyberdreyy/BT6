### Title
Fee-on-transfer tokens cause `ProvisionerV2`/`MultiDepositorVault` to mint vault units against tokens that were never received - ([File: v3/src/core/ProvisionerV2.sol], [File: v3/src/core/MultiDepositorVault.sol])

### Summary
`ProvisionerV2.deposit`/`mint` compute `unitsOut`/`tokensIn` from the user-supplied `tokensIn`/`tokenAmount` value and then call `MultiDepositorVault.enter`, which performs `token.safeTransferFrom(sender, address(this), tokenAmount)` and unconditionally mints `unitsAmount` of vault shares based on that same nominal amount. If the deposit `token` charges a fee on transfer, the vault receives less than `tokenAmount`, yet units are minted as if the full nominal amount arrived, exactly mirroring the disclosed Allo `_fundPool` root cause (nominal amount used for internal accounting instead of the balance delta actually received).

### Finding Description
In `ProvisionerV2.deposit` (and `mint`), the token amount supplied by the caller is converted to units via `_tokensToUnitsFloorIfActive`/`_unitsToTokensCeilIfActive` and then passed straight into `_syncDeposit`, which calls `IMultiDepositorVault(MULTI_DEPOSITOR_VAULT).enter(msg.sender, token, tokenAmount, unitAmount, receiver)`. [1](#0-0) 

`MultiDepositorVault.enter` then pulls the tokens with `safeTransferFrom` and mints units based on the nominal `tokenAmount`/`unitsAmount` passed in, with no balance-before/after check: [2](#0-1) 

The same unguarded pattern recurs in the async solve paths, where `_solveDepositVaultAutoPrice`/`_solveDepositVaultFixedPrice` call `enter(address(this), token, tokensAfterTip, unitsOut, request.receiver)` using the nominal amount from the stored request, not the actual balance delta: [3](#0-2) [4](#0-3) 

If `token` deducts a transfer fee, `unitsOut`/`unitsAmount` is computed and minted against the pre-fee amount while the vault's actual token balance increases by less. This is the same broken invariant as the reported Allo issue: `poolAmount` (here, vault units backing per token) is inflated relative to actual custodied tokens, diluting other unit holders and creating a shortfall for future redemptions. Notably, `AeraVaultV1.depositToken` already guards against exactly this by measuring `token.balanceOf(address(this))` before and after the transfer and using the delta: [5](#0-4) 
showing Aera's own v1 codebase already recognized and mitigated the fee-on-transfer risk with the exact "balance delta" pattern recommended in the Allo report, while the v3 `ProvisionerV2`/`MultiDepositorVault` path reintroduces the unguarded nominal-amount pattern.

### Impact Explanation
Using a fee-on-transfer token configured for a `MultiDepositorVault` pool, every `deposit`/`mint`/async-deposit-solve mints vault units backed by fewer tokens than accounted for. This inflates the vault's unit-to-token backing, diluting existing unit holders and eventually causing insufficient tokens to honor `exit`/`redeem` calls for tokens legitimately owed — a funds-accounting/insolvency issue affecting all depositors of that pool, matching Immunefi's "incorrect accounting" / "funds loss" categories.

### Likelihood Explanation
This requires the vault/token pair to be configured with a fee-on-transfer ERC20 as a deposit token via `setTokenDetails`, which is an admin/governance action, not attacker-controlled. Whether this is exploitable in practice therefore hinges entirely on whether Aera's whitelisted/supported deposit tokens for deployed, bounty-in-scope vaults include any fee-on-transfer token. I was not able to fully verify from the indexed code/docs whether v3 explicitly documents an assumption that only standard (non-fee-on-transfer) ERC20s are ever configured as deposit tokens, nor could I confirm the current live token whitelist for deployed vaults. Given the codebase's own v1 precedent of explicitly guarding for fee-on-transfer tokens, this is a plausible unaddressed regression, but I cannot confirm with certainty that a fee-on-transfer token is actually deployed/whitelisted for any live, bounty-eligible `MultiDepositorVault`/`ProvisionerV2` pool. This is a material uncertainty that should be resolved before treating this as a confirmed live exploit rather than a design-assumption gap.

### Recommendation
In `MultiDepositorVault.enter` (and correspondingly in `exit`), measure `token.balanceOf(address(this))` immediately before and after `safeTransferFrom`, and use the delta as the amount considered actually received, matching the recommended fix in the referenced report and the existing `AeraVaultV1.depositToken` pattern. Alternatively/additionally, `ProvisionerV2` should explicitly reject or flag tokens with transfer fees at `setTokenDetails` time if fee-on-transfer support is not intended.

### Proof of Concept
1. Deploy a `TransferFeeToken`-style ERC20 with e.g. a 1% transfer fee.
2. Deploy `MultiDepositorVault` + `ProvisionerV2`, register the fee-on-transfer token via `setTokenDetails` with sync deposits enabled.
3. As an ordinary user, call `approve` then `ProvisionerV2.deposit(token, 1e18, minUnitsOut, receiver)`.
4. Assert `token.balanceOf(MULTI_DEPOSITOR_VAULT) < unitsOut`-implied token backing, i.e. compare `unitsOut` minted (via `IERC20(MULTI_DEPOSITOR_VAULT).balanceOf(receiver)`) against the actual `token.balanceOf(MULTI_DEPOSITOR_VAULT)` — the minted units correspond to `1e18` tokens while the vault only received `0.99e18`, demonstrating the accounting mismatch.

### Citations

**File:** v3/src/core/ProvisionerV2.sol (L992-1015)
```text
    /// @notice Handles a synchronous deposit, records the deposit hash, and enters the vault
    /// @dev Reverts if the deposit hash already exists. Sets the refundable period for the user
    /// @param token The ERC20 token to deposit
    /// @param tokenAmount The amount of tokens to deposit
    /// @param unitAmount The amount of vault units to mint for the user
    /// @param receiver The address receiving the minted units
    function _syncDeposit(IERC20 token, uint256 tokenAmount, uint256 unitAmount, address receiver) internal {
        uint256 refundableUntil = block.timestamp + depositRefundTimeout;
        bytes32 depositHash = _getDepositHash(msg.sender, receiver, token, tokenAmount, unitAmount, refundableUntil);

        // Requirements: deposit hash is not set
        require(!syncDepositHashes[depositHash], Aera__HashCollision());
        // Effects: set hash as used
        syncDepositHashes[depositHash] = true;

        // Effects: set receiver's refundable until timestamp
        userUnitsRefundableUntil[receiver] = refundableUntil;

        // Interactions: enter vault
        IMultiDepositorVault(MULTI_DEPOSITOR_VAULT).enter(msg.sender, token, tokenAmount, unitAmount, receiver);

        // Log deposit event
        emit Deposited(msg.sender, receiver, token, tokenAmount, unitAmount, depositHash);
    }
```

**File:** v3/src/core/ProvisionerV2.sol (L1093-1104)
```text
            // Interactions: apply premium and convert tokens in to units out
            uint256 unitsOut = _tokensToUnitsFloorIfActive(token, tokensAfterTip, depositMultiplier);
            // Requirements: units out meets min units out
            if (_guardAmountBound(unitsOut, request.units, index)) return 0;
            // Requirements + interactions: convert new total units to numeraire and check against deposit cap
            if (_guardDepositCapExceeded(unitsOut, index)) return 0;

            // Effects: unset hash as used
            asyncRequestHashes[depositHash] = false;
            // Interactions: enter vault and route units to receiver
            IMultiDepositorVault(MULTI_DEPOSITOR_VAULT)
                .enter(address(this), token, tokensAfterTip, unitsOut, request.receiver);
```

**File:** v3/src/core/ProvisionerV2.sol (L1150-1161)
```text
            // Interactions: convert units to tokens applying premium
            uint256 tokensNeeded = _unitsToTokensCeilIfActive(token, request.units, depositMultiplier);
            // Requirements: tokens needed is less than or equal to max tokens in
            if (_guardAmountBound(request.tokens, tokensNeeded, index)) return 0;
            // Requirements + interactions: convert new total units to numeraire and check against deposit cap
            if (_guardDepositCapExceeded(request.units, index)) return 0;

            // Effects: unset hash as used
            asyncRequestHashes[depositHash] = false;
            // Interactions: enter vault and route units to receiver
            IMultiDepositorVault(MULTI_DEPOSITOR_VAULT)
                .enter(address(this), token, tokensNeeded, request.units, request.receiver);
```

**File:** v3/src/core/MultiDepositorVault.sol (L60-74)
```text
    /// @inheritdoc IMultiDepositorVault
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

**File:** v1/AeraVaultV1.sol (L1268-1286)
```text
    function depositToken(IERC20 token, uint256 amount)
        internal
        returns (uint256)
    {
        // slither-disable-next-line calls-loop
        uint256 balance = token.balanceOf(address(this));
        token.safeTransferFrom(owner(), address(this), amount);
        // slither-disable-next-line calls-loop
        balance = token.balanceOf(address(this)) - balance;

        // slither-disable-next-line calls-loop
        uint256 allowance = token.allowance(address(this), address(bVault));
        if (allowance > 0) {
            token.safeDecreaseAllowance(address(bVault), allowance);
        }
        token.safeIncreaseAllowance(address(bVault), balance);

        return balance;
    }
```
