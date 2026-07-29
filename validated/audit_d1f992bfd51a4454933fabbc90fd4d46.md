The vulnerability is confirmed. `EscrowDst._withdraw` unconditionally calls the calldata fee-decoding helpers `integratorFeeAmountCd()` and `protocolFeeAmountCd()` regardless of whether fees are actually zero, and both `withdraw()` (private path) and `publicWithdraw()` (public path) route through this same internal function, so a short `parameters` blob bricks both paths identically.

### Title
Attacker-controlled short `parameters` in `createDstEscrow` permanently reverts both private and public destination withdrawal, letting the taker grief the maker and reclaim funds via `cancel()` - (File: contracts/EscrowDst.sol)

### Summary
`createDstEscrow` in `BaseEscrowFactory.sol` accepts `dstImmutables` (including the `parameters` field) directly from `msg.sender` with no length or content validation against the `DstImmutablesComplement.parameters` announced by `_postInteraction` on the source chain. [1](#0-0) 
The CREATE2 salt is `immutables.hashMem()`, which folds in the raw `parameters` bytes, so whatever `parameters` the taker supplies at creation time becomes permanently bound to the escrow and must be reproduced byte-for-byte on every subsequent `withdraw`/`publicWithdraw`/`cancel` call via `onlyValidImmutables`. [2](#0-1) 

### Finding Description
`EscrowDst._withdraw` always calls `immutables.integratorFeeAmountCd()` and `immutables.protocolFeeAmountCd()` before checking whether the resulting fee amounts are nonzero: [3](#0-2) 

Both accessors unconditionally revert with `IndexOutOfRange()` if `parameters.length` is smaller than `0x20`/`0x40` bytes respectively: [4](#0-3) 

Both `withdraw()` (private, `onlyCaller(taker)`) and `publicWithdraw()` (public, `onlyAccessTokenHolder`) call the exact same `_withdraw` internal function: [5](#0-4) 

Since `createDstEscrow` lets an unprivileged caller (the taker/resolver creating the destination escrow) supply an arbitrary `parameters` blob — e.g., 1 byte — with no minimum-length check, and this blob is baked into the CREATE2 salt/immutables hash, there is no way for the maker, the taker, or a public caller to later "fix" the parameters to make withdrawal succeed. Every future `withdraw`/`publicWithdraw` call reverts with `IndexOutOfRange()` regardless of who calls it or whether fees are actually zero.

`cancel()`, by contrast, never touches `parameters` and sends the escrowed `amount` back to `taker` (not `maker`): [6](#0-5) 

This means the taker who deliberately deploys the malformed escrow can wait out the timelocks and reclaim the destination funds themselves via `cancel()`, while the maker — who would have revealed the secret expecting to be paid — never receives their destination-side funds. The revealed secret (visible in the reverted transaction's calldata) can still be reused by the taker to withdraw on the source chain, effectively letting the taker collect both sides of the swap.

### Impact Explanation
This matches the "Temporary freezing of funds" / "theft of unclaimed value" categories: the maker's destination payout is unconditionally blocked from the moment of escrow creation through `DstCancellation`, and the taker can convert this freeze into an outright fund diversion by cancelling and reclaiming the escrowed amount for themselves after the timelock, while still being able to use the leaked secret to withdraw on the source side.

### Likelihood Explanation
High likelihood: `createDstEscrow` is a fully public, unprivileged entrypoint that takes `dstImmutables` directly from calldata with no minimum-length enforcement on `parameters`. Any taker/resolver funding the destination escrow (a normal, permissionless step in the protocol) can trivially pass a 1-byte `parameters` value while still satisfying the balance/safety-deposit checks, since those only look at `amount`/`safetyDeposit`, not `parameters`.

### Recommendation
Enforce a minimum/expected `parameters` length (at least `0x80` bytes to cover both fee amounts and both fee recipients) in `createDstEscrow` before deploying the clone, and/or have `ImmutablesLib`'s Cd accessors treat "too short" as "fee is zero" only when the entire tail is provably absent by construction rather than allowing an attacker-supplied truncated blob to pass the balance checks and still deploy.

### Proof of Concept
1. Attacker (taker) calls `createDstEscrow(dstImmutables, srcCancellationTimestamp)` with `dstImmutables.parameters` set to a single byte (e.g., `hex"00"`), funding `amount` and `safetyDeposit` normally. [1](#0-0) 
2. Maker (or anyone with the access token) waits for `DstWithdrawal`/`DstPublicWithdrawal` and calls `withdraw(secret, immutables)` / `publicWithdraw(secret, immutables)` with the correct secret and the exact same `immutables` (so `onlyValidImmutables` passes).
3. `_withdraw` calls `immutables.integratorFeeAmountCd()`, which reverts `IndexOutOfRange()` because `parameters.length (1) < 0x40`, so no version of withdrawal ever succeeds. [7](#0-6) 
4. After `DstCancellation`, the taker calls `cancel(immutables)`, which does not reference `parameters` at all and sends the full `amount` back to `taker`, and the safety deposit to whoever calls it. [6](#0-5)

### Citations

**File:** contracts/BaseEscrowFactory.sol (L165-185)
```text
    function createDstEscrow(IBaseEscrow.Immutables calldata dstImmutables, uint256 srcCancellationTimestamp) external payable {
        address token = dstImmutables.token.get();
        uint256 nativeAmount = dstImmutables.safetyDeposit;
        if (token == address(0)) {
            nativeAmount += dstImmutables.amount;
        }
        if (msg.value != nativeAmount) revert InsufficientEscrowBalance();

        IBaseEscrow.Immutables memory immutables = dstImmutables;
        immutables.timelocks = immutables.timelocks.setDeployedAt(block.timestamp);
        // Check that the escrow cancellation will start not later than the cancellation time on the source chain.
        if (immutables.timelocks.get(TimelocksLib.Stage.DstCancellation) > srcCancellationTimestamp) revert InvalidCreationTime();

        bytes32 salt = immutables.hashMem();
        address escrow = _deployEscrow(salt, msg.value, ESCROW_DST_IMPLEMENTATION);
        if (token != address(0)) {
            IERC20(token).safeTransferFrom(msg.sender, escrow, immutables.amount);
        }

        emit DstEscrowCreated(escrow, immutables.hashlock, immutables.taker);
    }
```

**File:** contracts/libraries/ImmutablesLib.sol (L76-95)
```text
    function protocolFeeAmountCd(IBaseEscrow.Immutables calldata immutables) external pure returns (uint256 ret) {
        bytes calldata parameters = immutables.parameters;
        if (parameters.length < 0x20) revert IndexOutOfRange();
        assembly ("memory-safe") {
            ret := calldataload(parameters.offset)
        }
    }

    /**
     * @notice Returns the integrator fee amount from the immutables (calldata version).
     * @param immutables The immutables to extract the fee from.
     * @return ret The integrator fee amount.
     */
    function integratorFeeAmountCd(IBaseEscrow.Immutables calldata immutables) external pure returns (uint256 ret) {
        bytes calldata parameters = immutables.parameters;
        if (parameters.length < 0x40) revert IndexOutOfRange();
        assembly ("memory-safe") {
            ret := calldataload(add(parameters.offset, 0x20))
        }
    }
```

**File:** contracts/libraries/ImmutablesLib.sol (L150-165)
```text
    function hashMem(IBaseEscrow.Immutables memory immutables) internal pure returns(bytes32 ret) {
        // Compute the EIP-712 hash for the immutables struct
        // Patch the last word (bytes parameters) in the struct with the hash of it
        bytes memory parameters = immutables.parameters;
        assembly ("memory-safe") {
            let parametersHash := keccak256(add(parameters, 0x20), mload(parameters))
            let patchLocation := sub(add(immutables, IMMUTABLES_SIZE), 0x20)
            let backup := mload(patchLocation)

            // Patch the last word with the hash of parameters to compute the EIP-712 hash
            mstore(patchLocation, parametersHash)
            ret := keccak256(immutables, IMMUTABLES_SIZE)

            mstore(patchLocation, backup) // Restore the original value
        }
    }
```

**File:** contracts/EscrowDst.sol (L36-57)
```text
    function withdraw(bytes32 secret, Immutables calldata immutables)
        external
        onlyCaller(immutables.taker.get())
        onlyAfter(immutables.timelocks.get(TimelocksLib.Stage.DstWithdrawal))
        onlyBefore(immutables.timelocks.get(TimelocksLib.Stage.DstCancellation))
    {
        _withdraw(secret, immutables);
    }

    /**
     * @notice See {IBaseEscrow-publicWithdraw}.
     * @dev The function works on the time intervals highlighted with capital letters:
     * ---- contract deployed --/-- finality --/-- private withdrawal --/-- PUBLIC WITHDRAWAL --/-- private cancellation ----
     */
    function publicWithdraw(bytes32 secret, Immutables calldata immutables)
        external
        onlyAccessTokenHolder()
        onlyAfter(immutables.timelocks.get(TimelocksLib.Stage.DstPublicWithdrawal))
        onlyBefore(immutables.timelocks.get(TimelocksLib.Stage.DstCancellation))
    {
        _withdraw(secret, immutables);
    }
```

**File:** contracts/EscrowDst.sol (L64-73)
```text
    function cancel(Immutables calldata immutables)
        external
        onlyCaller(immutables.taker.get())
        onlyValidImmutables(immutables.hash())
        onlyAfter(immutables.timelocks.get(TimelocksLib.Stage.DstCancellation))
    {
        _uniTransfer(immutables.token.get(), immutables.taker.get(), immutables.amount);
        _ethTransfer(msg.sender, immutables.safetyDeposit);
        emit EscrowCancelled();
    }
```

**File:** contracts/EscrowDst.sol (L79-92)
```text
    function _withdraw(bytes32 secret, Immutables calldata immutables)
        internal
        onlyValidImmutables(immutables.hash())
        onlyValidSecret(secret, immutables.hashlock)
    {
        uint256 integratorFeeAmount = immutables.integratorFeeAmountCd();
        uint256 protocolFeeAmount = immutables.protocolFeeAmountCd();
        if (integratorFeeAmount > 0) {
            _uniTransfer(immutables.token.get(), immutables.integratorFeeRecipientCd().get(), integratorFeeAmount);
        }
        if (protocolFeeAmount > 0) {
            _uniTransfer(immutables.token.get(), immutables.protocolFeeRecipientCd().get(), protocolFeeAmount);
        }
        uint256 amount = immutables.amount - integratorFeeAmount - protocolFeeAmount;
```
