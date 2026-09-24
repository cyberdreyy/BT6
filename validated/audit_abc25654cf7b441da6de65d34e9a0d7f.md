### Title
Sync-deposit refund lock (`userUnitsRefundableUntil`) is bypassed via burn path, allowing a locked depositor to redeem out before `refundDeposit` can claw back funds - ([File: v3/src/core/MultiDepositorVault.sol])

### Summary
`ProvisionerV2._syncDeposit` sets `userUnitsRefundableUntil[receiver]` to gate a temporary "lock" on the receiver's vault units, intended to guarantee that an authorized party can later call `refundDeposit` to reverse a sync deposit. The lock is enforced in `MultiDepositorVault._update`, but the guard is skipped whenever `to == address(0)` (i.e., any burn/exit), not just for the privileged `refundDeposit` burn it was written for. This mirrors the Prime.sol root cause: a state-gating check (`stakedAt`/lock timestamp) is enforced on some state-transition paths but not on another reachable one, letting an ordinary user bypass the intended invariant.

### Finding Description
`_syncDeposit` records the lock: [1](#0-0) . The lock is meant to stop the receiver from moving the just-minted, still-refundable units before `refundDeposit` (an authorized-only path) can burn them back: [2](#0-1) .

The lock check lives in `MultiDepositorVault._update`: [3](#0-2) 

The comment states `to == address(0)` is exempted "to allow burning units in refundDeposit," but this exemption is not scoped to `refundDeposit`'s own call — it applies to *every* burn, including `exit()` calls triggered by the locked user themselves. `exit()` in `MultiDepositorVault` performs `_burn(sender, unitsAmount)` (i.e. `to == address(0)`), unconditionally: [4](#0-3) .

`exit()` is reachable by an ordinary user through the provisioner's synchronous redeem flow (`_syncRedeem`, which calls `exit(msg.sender, token, tokensOut, unitsIn, receiver)` with `msg.sender` as burn source): [5](#0-4) . Because the burn source equals `to == address(0)` in the underlying ERC20 `_update`, `areUserUnitsLocked(from)` is never evaluated for this path — the receiver can synchronously redeem (exit) the exact units that are supposedly "locked" pending a possible `refundDeposit`.

Sequence:
1. User calls `deposit`/`mint` → `_syncDeposit` sets `userUnitsRefundableUntil[user] = now + depositRefundTimeout` and mints units to the user.
2. Before the timeout elapses, and before any authorized party calls `refundDeposit`, the user calls the synchronous redeem entrypoint, which calls `_syncRedeem` → `MultiDepositorVault.exit(user, token, tokensOut, unitsIn, user)`.
3. `exit()` burns `unitsIn` from the user (`to == address(0)`), bypassing the `!areUserUnitsLocked(from)` guard entirely, and pays out `tokensOut` to the user.
4. If the deposit is later flagged for refund (e.g., stale/manipulated price used to mint units, or any other reason `refundDeposit` exists for), the authorized `refundDeposit` call attempts `exit(receiver, token, tokenAmount, unitsAmount, receiver)` — but the receiver no longer holds `unitsAmount` units, so both the `try` and the `catch` fallback revert, and the refund cannot be executed.

The guard that is supposed to hold the receiver's units in place until an authorized refund decision is made is bypassable through a legitimate, ordinary-user-callable burn path, defeating the invariant the lock exists to protect.

### Impact Explanation
This breaks the deposit-refund safety mechanism: any depositor whose sync deposit is later deemed refundable (e.g., due to a price/oracle issue at time of deposit) can extract value and exit before the protocol can reverse the deposit, because the on-chain lock does not actually prevent redemption — only ordinary transfers. This directly undermines a fund-safety guard around user deposits/units in `ProvisionerV2`/`MultiDepositorVault`, which are in the bounty-listed deposit/redeem code paths.

### Likelihood Explanation
No privileged role, collusion, or external oracle fault is required. Any user can trigger the sequence: deposit synchronously, then immediately call the synchronous redeem path before an authorized party notices and calls `refundDeposit`. The precondition is simply that `depositRefundTimeout` window exists (feature is enabled) and a sync redeem is available for the token, both of which are ordinary configuration.

### Recommendation
Scope the burn exemption in `MultiDepositorVault._update` to the specific `refundDeposit` caller/context rather than to all burns, or alternatively enforce `areUserUnitsLocked(from)` for all `exit()`-triggered burns except when the burn is initiated by `refundDeposit` itself (e.g., pass a flag from `ProvisionerV2.refundDeposit` through `exit()` that permits bypassing the lock only in that call, while `_syncRedeem`, async solves, and cancellations remain subject to the lock for the affected user).

### Proof of Concept
Local Foundry fork steps:
1. Deploy/fork `ProvisionerV2` + `MultiDepositorVault` with sync deposit and sync redeem enabled for a token, `depositRefundTimeout` set to a non-zero value (e.g., 1 hour).
2. As `alice` (ordinary user), call `provisioner.deposit(token, tokensIn, minUnitsOut, alice)`. Assert `provisioner.userUnitsRefundableUntil(alice) > block.timestamp` and `provisioner.areUserUnitsLocked(alice) == true`.
3. Attempt `vault.transfer(bob, unitsOut)` from `alice` → assert it reverts with `Aera__UnitsLocked()` (confirms lock is active for transfers).
4. Immediately (before timeout elapses) call the provisioner's synchronous redeem entrypoint as `alice` for `unitsOut` units → assert the call **succeeds** and `alice` receives `tokensOut`, despite `areUserUnitsLocked(alice)` still being `true`.
5. As the authorized address, call `provisioner.refundDeposit(alice, alice, token, tokenAmount, unitsAmount, refundableUntil)` using the original deposit's parameters → assert it **reverts** (insufficient unit balance to burn), proving the refund guarantee was defeated.

### Citations

**File:** v3/src/core/ProvisionerV2.sol (L231-260)
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
        // Requirements: refundable timestamp is in the future
        require(refundableUntil >= block.timestamp, Aera__RefundPeriodExpired());

        bytes32 depositHash = _getDepositHash(sender, receiver, token, tokenAmount, unitsAmount, refundableUntil);
        // Requirements: hash has been set
        require(syncDepositHashes[depositHash], Aera__DepositHashNotFound());
        // Effects: unset hash as used
        syncDepositHashes[depositHash] = false;

        // Interactions: pull funds from yield source if vault idle balance is insufficient
        _pullFundsIfNeeded(token, tokenAmount);

        // Interactions: exit vault, fallback to sender
        try IMultiDepositorVault(MULTI_DEPOSITOR_VAULT).exit(receiver, token, tokenAmount, unitsAmount, receiver) { }
        catch {
            IMultiDepositorVault(MULTI_DEPOSITOR_VAULT).exit(receiver, token, tokenAmount, unitsAmount, sender);
        }

        // Log deposit refunded event
        emit DirectDepositRefunded(depositHash);
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

**File:** v3/src/core/ProvisionerV2.sol (L1017-1049)
```text
    /// @notice Executes a synchronous redeem: rolls epoch, checks epoch cap, exits vault, and emits event
    /// @param token The ERC20 token to receive
    /// @param tokensOut The amount of tokens to send to the receiver
    /// @param unitsIn The amount of vault units to burn from the caller
    /// @param receiver The address to receive the tokens
    /// @param epochRedeemNumeraire The pre-computed numeraire value of this redeem for epoch cap accounting
    /// @param priceTimestamp The PFC anchor timestamp captured in _prepareSyncRedeem (used for epoch rollover)
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

**File:** v3/src/core/MultiDepositorVault.sol (L76-90)
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
