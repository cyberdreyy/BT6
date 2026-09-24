### Title
Any user can grief-lock another user's vault units via unauthenticated receiver parameter in `ProvisionerV2.deposit`/`mint` - ([File: v3/src/core/ProvisionerV2.sol])

### Summary
`ProvisionerV2.deposit()` and `ProvisionerV2.mint()` accept an arbitrary `receiver` address and never verify that `msg.sender == receiver` or that the receiver approved the caller, despite the interface NatSpec explicitly stating "Caller must be the receiver or approved by the receiver via `{setDepositReceiverApproval}`" [1](#0-0) . This missing binding lets any ordinary attacker deposit a trivial amount specifying a victim as `receiver`, which unconditionally overwrites the victim's shared `userUnitsRefundableUntil` lock timestamp and re-locks the victim's pre-existing, already-unlocked vault units for a fresh `depositRefundTimeout` window.

### Finding Description
`_syncDeposit` is invoked from both `deposit()` and `mint()` and unconditionally sets:
```
userUnitsRefundableUntil[receiver] = refundableUntil;
``` [2](#0-1) 

`userUnitsRefundableUntil` is a single mapping keyed only by `address user`, shared across *all* of a user's deposits, not scoped per-deposit-hash [3](#0-2) . `MultiDepositorVault._update` consults this via `areUserUnitsLocked(from)` and blocks any transfer/redeem out of a locked account:
```
require(
    from == address(0) || to == address(0) || !IProvisionerV2(provisioner).areUserUnitsLocked(from),
    Aera__UnitsLocked()
);
``` [4](#0-3) 

`deposit()` and `mint()` only validate that `receiver` is a valid, non-zero address (`_requireValidReceiver`), and gate on `nonReentrant`/`anyoneButVault`/`solvingNotPaused` — there is no check that `msg.sender == receiver` or any receiver-approval lookup, despite the interface doc's stated invariant:
```
function deposit(IERC20 token, uint256 tokensIn, uint256 minUnitsOut, address receiver)
    external
    nonReentrant
    anyoneButVault
    solvingNotPaused(token)
    returns (uint256 unitsOut)
{
    _requireValidReceiver(receiver);
    _validateNonZeroAmounts(minUnitsOut, tokensIn);
    ...
    _syncDeposit(token, tokensIn, unitsOut, receiver);
    ...
}
``` [5](#0-4) [6](#0-5) 

This is structurally the same root-cause pattern as the WebAuthn advisory: an operation that is supposed to be scoped to "the authenticated/authorized identity" (the WebAuthn assertion's resolved user must equal `current_user`; here, the deposit's `receiver` must equal `msg.sender` or an approved delegator) instead accepts any caller-supplied target identity because the binding check was never implemented on this path, even though the sibling invariant is documented/expected. The attacker pays only the cost of `depositCap`-bounded minimal tokens (can be the smallest unit that rounds to non-zero `unitsOut`), and the victim's already-owned, previously freely transferable/redeemable units become locked (`Aera__UnitsLocked`) for a fresh `depositRefundTimeout` window, repeatable indefinitely to keep the victim's funds continuously frozen.

### Impact Explanation
This is a fund-freezing bug: it forces continuous, attacker-triggerable freezing of a victim's already-owned MultiDepositorVault units, blocking transfers and redemptions (`Aera__UnitsLocked`) for the `depositRefundTimeout` duration on every attacker-initiated micro-deposit. Since the mapping is global per-user (not per-deposit-hash) and overwritten unconditionally, a griefer can perpetually re-lock the victim by repeating the trivial deposit before each expiry, denying the victim access to their own capital — matching the "temporary freezing of user funds" Medium-severity Immunefi impact category on `ProvisionerV2`/`MultiDepositorVault`, both in-scope, deployed, bounty-listed contracts.

### Likelihood Explanation
Trivially reachable by any address with no privileges: `deposit()`/`mint()` are public, require only holding/approving a minimal amount of an accepted token, and `receiver` is fully attacker-controlled with no approval check enforced in the implementation (despite being documented as required). No solver, guardian, oracle, or admin role is needed — an ordinary user transaction suffices.

### Recommendation
Enforce the documented invariant in `ProvisionerV2.deposit()` and `mint()`: require `msg.sender == receiver` unless the receiver has explicitly approved the caller (implement and check the referenced `setDepositReceiverApproval`/approval mapping before allowing a third-party `receiver`). Additionally, consider scoping `userUnitsRefundableUntil` per-deposit-hash rather than as a single overwritable per-user timestamp, so unrelated deposits cannot extend/reset another (or even the same) deposit's lock window.

### Proof of Concept
Foundry/local-fork steps:
1. Deploy `MultiDepositorVault` + `ProvisionerV2` per repo test harness; enable sync deposits for a test token with a nonzero `depositRefundTimeout`.
2. As Victim, call `deposit(token, tokensIn, minUnitsOut, victim)` to acquire units; fast-forward past `depositRefundTimeout` so `areUserUnitsLocked(victim) == false`. Assert Victim can `transfer`/`redeem` freely.
3. As Attacker (unrelated address, no approval from Victim), call `deposit(token, 1 wei-equivalent tokensIn, 0, victim)` (or `mint` with `unitsOut` rounding to smallest valid amount) specifying `receiver = victim`.
4. Assert the call succeeds (no revert) and `userUnitsRefundableUntil(victim)` is now `block.timestamp + depositRefundTimeout`.
5. As Victim, attempt `MultiDepositorVault.transfer(...)` of the units acquired in step 2; assert it reverts with `Aera__UnitsLocked()` even though those units are unrelated to the attacker's deposit and were previously freely transferable.
6. Repeat step 3 before each expiry to show the freeze can be perpetuated indefinitely by the attacker at negligible cost.

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

**File:** v3/src/core/ProvisionerV2.sol (L86-87)
```text
    /// @notice Mapping of user address to timestamp until which their units are locked
    mapping(address user => uint256 unitsLockedUntil) public userUnitsRefundableUntil;
```

**File:** v3/src/core/ProvisionerV2.sol (L170-198)
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
```

**File:** v3/src/core/ProvisionerV2.sol (L200-229)
```text
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

**File:** v3/src/core/MultiDepositorVault.sol (L121-127)
```text
        // Requirements: check that the from address does not have its units locked
        // from == address(0) is to allow minting further units for user with locked units
        // to == address(0) is to allow burning units in refundDeposit
        require(
            from == address(0) || to == address(0) || !IProvisionerV2(provisioner).areUserUnitsLocked(from),
            Aera__UnitsLocked()
        );
```
