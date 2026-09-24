### Title
Anyone can grief another user's vault-unit transferability by resetting `userUnitsRefundableUntil` via `ProvisionerV2.deposit`/`mint` for an arbitrary `receiver` - ([File: v3/src/core/ProvisionerV2.sol])

### Summary
`ProvisionerV2.deposit()` and `ProvisionerV2.mint()` are callable by any address (`anyoneButVault` modifier only excludes the vault itself) and let the caller specify an arbitrary `receiver` for the minted units. Every sync deposit unconditionally overwrites `userUnitsRefundableUntil[receiver]` with a new value derived from the *current* `block.timestamp`, regardless of who the depositor is. This is analogous to the Ajna `PositionManager`/`LenderActions.transferLPs` bug, where an unrelated user's action (memorializing to the same bucket) silently overwrote another lender's `depositTime`, extending their lock/penalty window. Here, an unrelated depositor can silently extend a victim's unit-lock (`Aera__UnitsLocked`) enforced in `MultiDepositorVault._update`, freezing the victim's ability to transfer their existing vault units.

### Finding Description
`deposit()`/`mint()` in [1](#0-0)  only validate that `receiver` is non-zero/non-vault via `_requireValidReceiver`, and are open to any caller (`anyoneButVault`). They call `_syncDeposit`, which does: [2](#0-1) 

`userUnitsRefundableUntil[receiver] = block.timestamp + depositRefundTimeout;` is a plain assignment (no max/merge logic protecting a prior, unrelated depositor's own lock schedule), keyed only by `receiver` address — exactly like Ajna's `LenderActions.transferLPs`, which centralizes/overwrites `depositTime` for a `lender` bucket shared across independent users memorializing to `PositionManager`. Because `block.timestamp` strictly increases, any subsequent deposit to the same `receiver`—triggered by anyone, for any (even tiny) amount—always pushes the lock further into the future than whatever the receiver's own most recent deposit had set.

This lock is enforced on ordinary ERC20 transfers of vault units in `MultiDepositorVault._update`: [3](#0-2) 
`areUserUnitsLocked` checks `userUnitsRefundableUntil[user] >= block.timestamp` [4](#0-3) . The check is skipped only for burns (`to == address(0)`) and mints (`from == address(0)`), i.e. it specifically blocks ordinary transfers between users.

Failed guard: there is no check that `msg.sender == receiver`, no per-depositor/per-hash tracking of the lock (unlike `syncDepositHashes`, which is keyed by the full deposit params including `msg.sender` and `receiver`), and no floor/merge logic protecting the receiver's existing lock expectations from being extended by a third party's unrelated deposit.

### Impact Explanation
An attacker can repeatedly (at low, self-funded cost, since minted units go to the victim, not the attacker) call `deposit()`/`mint()` with the victim as `receiver` and a minimal `tokensIn`, resetting `userUnitsRefundableUntil[victim]` to `block.timestamp + depositRefundTimeout` on each call. As long as the attacker repeats this before the existing lock expires, the victim's vault units remain permanently non-transferable via ordinary ERC20 `transfer`/`transferFrom`, i.e. a sustained, attacker-controlled temporary freeze of the victim's transfer rights on their vault-unit holdings. This matches the Immunefi "Medium: temporary freezing of user funds" impact category, since the victim's units cannot be transferred (though they may still be exited/redeemed through the Provisioner's redeem paths since burns bypass the lock check).

### Likelihood Explanation
Requires: (1) the target vault's `MultiDepositorVault` has a `beforeTransferHook`/lock semantics relying on `areUserUnitsLocked`, (2) sync deposits enabled for some token (`asyncDepositEnabled`/sync deposit config via `_requireSyncDepositsEnabled`), (3) attacker has enough tokens to make a minimal deposit that produces `unitsOut >= minUnitsOut > 0`. All of these are ordinary, permissionless conditions reachable by any user with no privileged role, since `deposit`/`mint` are public entry points guarded only by `anyoneButVault`. The attack is cheap to repeat (attacker's tokens are consumed into the vault as legitimate deposits credited to the victim, not lost, aside from any deposit/premium spread).

### Recommendation
Restrict who can set/extend a `receiver`'s refund-lock window: either (a) require `msg.sender == receiver` for sync `deposit`/`mint`, or (b) track `userUnitsRefundableUntil` per deposit hash/depositor rather than as a single overwritable value per receiver, or (c) only extend (never shorten) the lock legitimately tied to the depositor's own units, and exclude third-party-initiated deposits from resetting another address's lock window entirely.

### Proof of Concept
1. Deploy `MultiDepositorVault` + `ProvisionerV2` with sync deposits enabled for token `T`, `depositRefundTimeout = 1 days`, and a `beforeTransferHook`/no hook (lock check is native in `_update`).
2. Victim calls `provisioner.deposit(T, 100e18, minUnitsOut, victim)`; `userUnitsRefundableUntil[victim] = t0 + 1 days`. Advance time to `t0 + 1 day - 1` (still locked).
3. Attacker (unrelated address, holding minimal `T`) calls `provisioner.deposit(T, 1, 0 /*minUnitsOut allowed if >0 unit produced*/, victim)` at `t1 = t0 + 1 day - 1`; this sets `userUnitsRefundableUntil[victim] = t1 + 1 day`, extending the victim's lock by nearly a full day beyond what the victim expected.
4. Assert `provisioner.areUserUnitsLocked(victim) == true` at `t0 + 1 day` (when it should have unlocked per the victim's own deposit).
5. Assert victim's `vault.transfer(otherAddress, victimUnits)` reverts with `Aera__UnitsLocked` at `t0 + 1 day`, demonstrating the griefing-induced freeze caused solely by the attacker's unrelated deposit to the victim's address.

### Citations

**File:** v3/src/core/ProvisionerV2.sol (L169-198)
```text
    /// @inheritdoc IProvisionerV2
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

**File:** v3/src/core/MultiDepositorVault.sol (L109-131)
```text
    /// @notice Internal function to update token balances with transfer hook checks
    /// @param from The address tokens are transferred from
    /// @param to The address tokens are transferred to
    /// @param amount The amount of tokens to transfer
    /// @inheritdoc ERC20
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
