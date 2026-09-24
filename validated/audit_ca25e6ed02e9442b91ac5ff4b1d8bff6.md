### Title
Missing receiver-authorization check in `ProvisionerV2.deposit`/`mint` allows any user to force-lock another user's vault units - ([File: v3/src/core/ProvisionerV2.sol])

### Summary
`IProvisionerV2.deposit`/`mint` are documented as requiring "Caller must be the receiver or approved by the receiver via `{setDepositReceiverApproval}`" [1](#0-0) , but the actual `ProvisionerV2.deposit`/`mint` implementations never check `depositReceiverApprovals[receiver][msg.sender]` or `msg.sender == receiver` — they only call `_requireValidReceiver(receiver)` and proceed to `_syncDeposit` for an arbitrary attacker-chosen `receiver` [2](#0-1) . Because `_syncDeposit` unconditionally overwrites `userUnitsRefundableUntil[receiver]` with a fresh future timestamp for any caller-supplied `receiver` [3](#0-2) , and `MultiDepositorVault._update` blocks transfers/exits from any address whose units are "locked" per that mapping [4](#0-3) , any unprivileged, ordinary user can repeatedly and indefinitely freeze another user's already-owned vault units without the victim's consent — the same "privilege not checked → unauthorized user can add/edit data on someone else's behalf" root cause as CVE-2025-46823.

### Finding Description
`deposit(token, tokensIn, minUnitsOut, receiver)` and `mint(token, unitsOut, maxTokensIn, receiver)` accept an arbitrary `receiver` address supplied by `msg.sender`, guarded only by `anyoneButVault`, `solvingNotPaused`, `_requireValidReceiver`, `_validateNonZeroAmounts`, and `_requireSyncDepositsEnabled` — none of which check that `msg.sender` is `receiver` or has been approved by `receiver` via `depositReceiverApprovals` [2](#0-1) . This directly contradicts the interface's documented invariant that "Caller must be the receiver or approved by the receiver" [5](#0-4) [6](#0-5) .

`_syncDeposit`, invoked from both `deposit` and `mint`, unconditionally sets `userUnitsRefundableUntil[receiver] = block.timestamp + depositRefundTimeout` for the caller-chosen `receiver`, and calls `MultiDepositorVault.enter` with `receiver` as recipient [3](#0-2) . `MultiDepositorVault._update` enforces `!IProvisionerV2(provisioner).areUserUnitsLocked(from)` for every transfer/exit where `from != address(0)` [7](#0-6) , and `areUserUnitsLocked` simply checks `userUnitsRefundableUntil[user] >= block.timestamp` [8](#0-7) .

Because `receiver` in `deposit`/`mint` is entirely attacker-controlled and there is no approval check, an attacker can pick any victim address as `receiver`, make a trivially small deposit (`tokensIn`/`unitsOut` can be minimized subject to `minUnitsOut`/`maxTokensIn` and deposit-cap constraints), and this refreshes the victim's `userUnitsRefundableUntil` to a new future timestamp — locking the victim's entire pre-existing unit balance (not just the newly minted dust) from being transferred or exited until the lock expires. Repeating this call before the previous lock expires keeps the victim's units perpetually locked. `refundDeposit`, which could otherwise reverse a malicious deposit, is `requiresAuth`-gated [9](#0-8) , so the victim (an ordinary, non-privileged user) has no permissionless way to unlock their own funds early.

### Impact Explanation
This causes concrete freezing of a victim's already-owned vault units (`MultiDepositorVault` shares), preventing transfers, redemptions, or exits for the duration of `depositRefundTimeout`, and the freeze can be perpetually renewed by the attacker at low cost. This is a temporary but attacker-controllable and repeatable freeze of user funds, matching Immunefi's "temporary freezing of funds" impact category.

### Likelihood Explanation
No privileged role is required. Any address can call `deposit`/`mint` on `ProvisionerV2` with an arbitrary `receiver`, subject only to `solvingNotPaused`, `asyncDepositEnabled`/sync-deposit-enabled flags, and deposit cap — all standard, non-privileged states for a live, in-scope vault. The attacker needs only enough of the deposit token to satisfy `minUnitsOut`/dust amounts, making the attack cheap and repeatable indefinitely.

### Recommendation
Enforce the documented invariant in `deposit` and `mint`: require `msg.sender == receiver || depositReceiverApprovals[receiver][msg.sender]` before proceeding, mirroring the interface's stated preconditions [5](#0-4) .

### Proof of Concept
1. Deploy/fork the live `MultiDepositorVault` + `ProvisionerV2` pair with sync deposits enabled for a test token and non-zero `depositRefundTimeout`.
2. Victim performs a legitimate `deposit()` to receive units and passes the initial refund window.
3. Attacker (unrelated EOA, no approval granted via `setDepositReceiverApproval`) calls `ProvisionerV2.deposit(token, tinyTokensIn, tinyMinUnitsOut, victim)` repeatedly (or `mint`), each call refreshing `userUnitsRefundableUntil[victim]`.
4. Assert: victim attempts `MultiDepositorVault.transfer`/`ProvisionerV2` redeem and it reverts with `Aera__UnitsLocked()` despite having taken no freezing action themselves, confirming forced fund lock by an unauthorized third party.
5. Assert: victim cannot call `refundDeposit` themselves (function is `requiresAuth`), so they have no permissionless self-help remedy.

### Citations

**File:** v3/src/core/interfaces/IProvisionerV2.sol (L302-322)
```text
    /// @notice Deposit tokens directly into the vault
    /// @param token The token to deposit
    /// @param tokensIn The amount of tokens to deposit
    /// @param minUnitsOut The minimum amount of units expected
    /// @param receiver The address that receives units
    /// @return unitsOut The amount of shares minted to the receiver
    /// @dev Caller must be the receiver or approved by the receiver via {setDepositReceiverApproval}
    function deposit(IERC20 token, uint256 tokensIn, uint256 minUnitsOut, address receiver)
        external
        returns (uint256 unitsOut);

    /// @notice Mint exact amount of units by depositing required tokens
    /// @param token The token to deposit
    /// @param unitsOut The exact amount of units to mint
    /// @param maxTokensIn Maximum amount of tokens willing to deposit
    /// @param receiver The address that receives units
    /// @return tokensIn The amount of tokens used to mint the requested shares
    /// @dev Caller must be the receiver or approved by the receiver via {setDepositReceiverApproval}
    function mint(IERC20 token, uint256 unitsOut, uint256 maxTokensIn, address receiver)
        external
        returns (uint256 tokensIn);
```

**File:** v3/src/core/ProvisionerV2.sol (L170-229)
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

    /// @inheritdoc IProvisionerV2
    function mint(IERC20 token, uint256 unitsOut, uint256 maxTokensIn, address receiver)
        external
        nonReentrant
        anyoneButVault
        solvingNotPaused(token)
        returns (uint256 tokensIn)
    {
        // Requirements: receiver is valid
        _requireValidReceiver(receiver);

        // Requirements: tokens and units amount are positive
        _validateNonZeroAmounts(unitsOut, maxTokensIn);

        // Requirements: sync deposits are enabled
        TokenDetailsV2 storage tokenDetails = _requireSyncDepositsEnabled(token);

        // Requirements + interactions: convert new total units to numeraire and check against deposit cap
        _requireDepositCapNotExceeded(unitsOut);
        // Interactions: convert units to tokens
        tokensIn = _unitsToTokensCeilIfActive(token, unitsOut, tokenDetails.syncDepositMultiplier);
        // Requirements: token in is less than or equal to max tokens in
        require(tokensIn <= maxTokensIn, Aera__MaxTokensInExceeded());

        // Effects + interactions: sync deposit
        _syncDeposit(token, tokensIn, unitsOut, receiver);

        // Interactions: push funds to yield source if configured (swallows failures)
        _pushFundsIfConfigured(tokenDetails, tokensIn);
    }
```

**File:** v3/src/core/ProvisionerV2.sol (L231-239)
```text
    /// @inheritdoc IProvisionerV2
    function refundDeposit(
        address sender,
        address receiver,
        IERC20 token,
        uint256 tokenAmount,
        uint256 unitsAmount,
        uint256 refundableUntil
    ) external requiresAuth {
```

**File:** v3/src/core/ProvisionerV2.sol (L736-739)
```text
    /// @inheritdoc IProvisionerV2
    function areUserUnitsLocked(address user) external view returns (bool) {
        return userUnitsRefundableUntil[user] >= block.timestamp;
    }
```

**File:** v3/src/core/ProvisionerV2.sol (L998-1015)
```text
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

**File:** v3/src/core/MultiDepositorVault.sol (L114-131)
```text
    function _update(address from, address to, uint256 amount) internal override {
        IBeforeTransferHook hook = beforeTransferHook;
        if (address(hook) != address(0)) {
            // Requirements: perform before transfer checks
            hook.beforeTransfer(from, to, provisioner);
        }

        // Requirements: check that the from address does not have its units locked
        // from == address(0) is to allow minting further units for user with locked units
        // to == address(0) is to allow burning units in refundDeposit
        require(
            from == address(0) || to == address(0) || !IProvisionerV2(provisioner).areUserUnitsLocked(from),
            Aera__UnitsLocked()
        );

        // Effects: transfer the tokens
        return super._update(from, to, amount);
    }
```
