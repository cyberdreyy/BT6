### Title
Global per-user refund lock in `ProvisionerV2`/`MultiDepositorVault` retroactively re-locks a user's entire (already-unlocked) unit balance on any new sync deposit - ([File: v3/src/core/ProvisionerV2.sol], [File: v3/src/core/MultiDepositorVault.sol])

### Summary
`ProvisionerV2` tracks refundability of sync deposits with a single scalar timestamp per user, `userUnitsRefundableUntil`, instead of tracking a lock expiry per individual deposit/issuance. `MultiDepositorVault._update` enforces this scalar lock against *any* outgoing transfer of the user's vault units. Any new sync `deposit`/`mint` — even a trivially small one — overwrites this timestamp and retroactively re-locks the user's entire, previously fully-vested unit balance for a fresh `depositRefundTimeout` period, exactly mirroring the reported pattern of applying a "current" lock parameter to historical issuances instead of the lock that applied when each unit was actually minted.

### Finding Description
`_syncDeposit` unconditionally overwrites the user's lock timestamp on every sync deposit: [1](#0-0) 

This value is a single mapping entry per address, not per-deposit: [2](#0-1) 

The lock check `areUserUnitsLocked` compares `block.timestamp` against this single timestamp for the whole balance: [3](#0-2) 

`redeem` and `withdraw` explicitly gate on this same scalar for the caller's full balance: [4](#0-3) [5](#0-4) 

More importantly, `MultiDepositorVault._update` (invoked on every ERC20 transfer/transferFrom of vault units, including standard transfers and the `safeTransferFrom` performed by `requestRedeem`) enforces the same scalar lock against the `from` address unconditionally: [6](#0-5) 

`requestRedeem` (the async escape hatch) performs exactly such a `safeTransferFrom(msg.sender, ...)`, so it is blocked too, once the user's `userUnitsRefundableUntil` is refreshed: [7](#0-6) 

Root cause: the refund lock is scoped to the *account*, not the *deposit*. There is no per-deposit tracking of when each tranche of units was minted and when its individual refund window elapses — analogous to the report's missing per-issuance lock storage. As a result, a brand-new, tiny sync deposit resets the lock timestamp for the entire historical balance, not just the newly minted units, freezing funds that had already legitimately cleared their refund window.

### Impact Explanation
Any ordinary user who has previously deposited units (fully vested, past `depositRefundTimeout`) and then makes another sync `deposit`/`mint` — for any reason, even a dust amount — will find their entire unit balance (including all long-held, previously transferable units) frozen and untransferable via `redeem`, `withdraw`, plain ERC20 `transfer`, and even the async `requestRedeem` path, for the full `depositRefundTimeout` duration again. This is a temporary freeze of user funds/liquidity triggered purely by an ordinary-user transaction sequence, matching an Immunefi "temporary freezing of funds" Medium-severity impact.

### Likelihood Explanation
No privileged role, oracle, or third-party token is required. Any user holding vault units who calls `deposit`/`mint` a second time (a completely ordinary, expected action) triggers the bug against themselves. Because `deposit`/`mint` allow depositing on behalf of an approved receiver (`setDepositReceiverApproval`), it can also occur unintentionally when routine top-up deposits are made for active accounts (e.g., automated recurring deposits), each time re-freezing the account's full balance. This is easily and reliably reproducible with two `deposit` calls separated by time and one subsequent transfer attempt.

### Recommendation
Track the refund lock per deposit tranche instead of a single account-wide scalar — e.g., store `refundableUntil` alongside each `syncDepositHash`/mint record, and have `areUserUnitsLocked`/`_update` only restrict the specific units minted in the still-refundable window (or maintain a running "locked amount" per user that decays instead of a monolithic future timestamp), rather than gating transfer of the user's *entire* balance on the *latest* deposit's timestamp.

### Proof of Concept
Foundry steps (local fork):
1. Deploy/fork with `ProvisionerV2` + `MultiDepositorVault`, `depositRefundTimeout = T`.
2. `alice` calls `provisioner.deposit(token, amount1, minUnitsOut1, alice)` at `t0`. Assert `userUnitsRefundableUntil(alice) == t0 + T`.
3. Warp to `t0 + T + 1`. Assert `areUserUnitsLocked(alice) == false`; confirm `vault.transfer(bob, someUnits)` succeeds (units are freely transferable).
4. `alice` calls `provisioner.deposit(token, 1, 1, alice)` (dust deposit) at `t1 = t0 + T + 2`. Assert `userUnitsRefundableUntil(alice) == t1 + T`.
5. Attempt `vault.transfer(bob, sameOldUnitsFromStep2)` — reverts `Aera__UnitsLocked()`.
6. Attempt `provisioner.redeem(...)` and `provisioner.requestRedeem(...)` for the old, pre-existing units — both revert with `Aera__UnitsLocked()`, proving the entire historical, previously-unlocked balance is frozen again solely due to the new dust deposit.

### Citations

**File:** v3/src/core/ProvisionerV2.sol (L86-87)
```text
    /// @notice Mapping of user address to timestamp until which their units are locked
    mapping(address user => uint256 unitsLockedUntil) public userUnitsRefundableUntil;
```

**File:** v3/src/core/ProvisionerV2.sol (L605-606)
```text
        // Requirements: check that the caller does not have its units locked
        require(userUnitsRefundableUntil[msg.sender] < block.timestamp, Aera__UnitsLocked());
```

**File:** v3/src/core/ProvisionerV2.sol (L639-640)
```text
        // Requirements: check that the caller does not have its units locked
        require(userUnitsRefundableUntil[msg.sender] < block.timestamp, Aera__UnitsLocked());
```

**File:** v3/src/core/ProvisionerV2.sol (L737-739)
```text
    function areUserUnitsLocked(address user) external view returns (bool) {
        return userUnitsRefundableUntil[user] >= block.timestamp;
    }
```

**File:** v3/src/core/ProvisionerV2.sol (L811-832)
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
