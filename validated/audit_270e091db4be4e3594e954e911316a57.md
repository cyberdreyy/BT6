This is a genuine, reproducible bug. `createDstEscrow` in `BaseEscrowFactory.sol` performs no length/content validation on the `dstImmutables.parameters` blob — the caller funding the destination escrow controls it entirely, and it is only later consumed by `ImmutablesLib`'s calldata-fee decoders inside `EscrowDst._withdraw`.

### Title
Unvalidated `parameters` length lets an escrow creator permanently brick both `withdraw` and `publicWithdraw` on `EscrowDst` - (File: `contracts/EscrowDst.sol`, `contracts/libraries/ImmutablesLib.sol`)

### Summary
`ImmutablesLib` decodes fee fields from `immutables.parameters` at fixed calldata offsets with independent length checks per field: `protocolFeeAmountCd` requires `>= 0x20`, `integratorFeeAmountCd` requires `>= 0x40`, `protocolFeeRecipientCd` requires `>= 0x60`, and `integratorFeeRecipientCd` requires `>= 0x80` [1](#0-0) . `EscrowDst._withdraw` first reads both fee *amounts* and, only if the decoded `integratorFeeAmount > 0`, calls `integratorFeeRecipientCd()` [2](#0-1) . `createDstEscrow` accepts `dstImmutables` directly from `msg.sender` with zero validation of the `parameters` bytes content or length — it only checks `msg.value`/token transfer and the cancellation-timing bound [3](#0-2) .

### Finding Description
Any account calling `createDstEscrow` (which is permissionless and just requires the caller to fund the escrow with the correct native/token amount) can encode `parameters` as exactly 95 bytes: a zero `protocolFeeAmount` in the first word and a nonzero (near-full) `integratorFeeAmount` in the second word. This satisfies the `>= 0x40` check needed to read both fee amounts, but is short of the `>= 0x80` (128-byte) requirement for `integratorFeeRecipientCd()`. Since `protocolFeeAmount == 0` skips the protocol-recipient lookup, but `integratorFeeAmount > 0` unconditionally triggers the integrator-recipient lookup, `_withdraw` will always revert with `IndexOutOfRange`, regardless of caller. This affects both `withdraw` (private, taker-only) and `publicWithdraw` (open to any access-token holder) [4](#0-3)  because both funnel into the same `_withdraw` internal function.

Because the immutables (including `parameters`) are hashed into the CREATE2 salt and re-validated via `onlyValidImmutables` on every call [5](#0-4) , the attacker who created the escrow knows the exact malformed bytes and there is no way for the maker, taker, or any public caller to supply different immutables to route around the broken parameters — the withdrawal is permanently unusable until `cancel()` becomes available at `DstCancellation`.

### Impact Explanation
This breaks the stated invariant: "if a destination escrow was funded and the secret is known, the public withdrawal path should still be able to finalize." Since `cancel()` returns the escrowed token amount and safety deposit to the **taker**, not the maker [6](#0-5) , the maker's expected destination-chain payout never arrives — it is frozen through the entire withdrawal window and then effectively lost to the maker when the taker reclaims it via cancellation. This matches the "Temporary freezing of funds during the live swap lifecycle" (High) bucket in the bounty scope, and given cross-chain atomicity assumptions (the taker may already have withdrawn the maker's source-chain funds using the same revealed secret), it can escalate to a real loss for the maker on the destination leg.

### Likelihood Explanation
High likelihood: the entry point (`createDstEscrow`) is public/unprivileged, requires no special role, and the malformed `parameters` blob is trivial to construct (a specific short byte length with a zero protocol fee word and nonzero integrator fee word). No existing guard in `BaseEscrowFactory.createDstEscrow` or `ImmutablesLib` validates that `parameters` has a consistent/complete length before it's persisted into the escrow's immutable hash.

### Recommendation
Validate `parameters.length` in `createDstEscrow` (and/or in `ImmutablesLib`) to enforce an exact expected length (e.g., require `parameters.length == 0` or `== 0x80`) rather than independently-thresholded field checks, or require that if `integratorFeeAmount > 0`/`protocolFeeAmount > 0` the corresponding recipient fields exist by validating the full 0x80-byte length whenever fees are present, at escrow-creation time rather than at withdrawal time.

### Proof of Concept
1. Attacker (any account) calls `createDstEscrow` with `dstImmutables.parameters = abi.encodePacked(uint256(0), uint256(<large_integratorFee>), <29 extra zero bytes>)` totaling exactly 95 bytes, funding the escrow with the correct `amount + safetyDeposit`.
2. Time passes to `DstWithdrawal`; taker calls `withdraw(secret, immutables)` → `_withdraw` reads `protocolFeeAmountCd()` (0, skip), `integratorFeeAmountCd()` (large, nonzero) → calls `integratorFeeRecipientCd()` which requires `parameters.length >= 0x80` (128) but only has 95 → reverts `IndexOutOfRange`.
3. Time passes to `DstPublicWithdrawal`; any access-token holder calls `publicWithdraw(secret, immutables)` → same revert occurs.
4. Only `cancel()` after `DstCancellation` succeeds, returning the amount and safety deposit to the taker, leaving the maker without the destination payout. [2](#0-1) [7](#0-6) [3](#0-2)

### Citations

**File:** contracts/libraries/ImmutablesLib.sol (L76-121)
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

    /**
     * @notice Returns the protocol fee recipient from the immutables (calldata version).
     * @param immutables The immutables to extract the recipient from.
     * @return ret The protocol fee recipient.
     */
    function protocolFeeRecipientCd(IBaseEscrow.Immutables calldata immutables) external pure returns (Address ret) {
        bytes calldata parameters = immutables.parameters;
        if (parameters.length < 0x60) revert IndexOutOfRange();
        assembly ("memory-safe") {
            ret := calldataload(add(parameters.offset, 0x40))
        }
    }

    /**
     * @notice Returns the integrator fee recipient from the immutables (calldata version).
     * @param immutables The immutables to extract the recipient from.
     * @return ret The integrator fee recipient.
     */
    function integratorFeeRecipientCd(IBaseEscrow.Immutables calldata immutables) external pure returns (Address ret) {
        bytes calldata parameters = immutables.parameters;
        if (parameters.length < 0x80) revert IndexOutOfRange();
        assembly ("memory-safe") {
            ret := calldataload(add(parameters.offset, 0x60))
        }
    }
```

**File:** contracts/libraries/ImmutablesLib.sol (L128-143)
```text
    function hash(IBaseEscrow.Immutables calldata immutables) internal pure returns(bytes32 ret) {
        // Compute the EIP-712 hash for the immutables struct
        bytes calldata parameters = immutables.parameters;
        assembly ("memory-safe") {
            let ptr := mload(0x40)

            // Copy immutables.parameters to memory and compute its hash
            calldatacopy(ptr, parameters.offset, parameters.length)
            let parametersHash := keccak256(ptr, parameters.length)

            // Copy the immutables struct to memory, patch `parameters` and compute its hash
            calldatacopy(ptr, immutables, IMMUTABLES_SIZE)
            mstore(add(ptr, IMMUTABLES_LAST_WORD), parametersHash)
            ret := keccak256(ptr, IMMUTABLES_SIZE)
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

**File:** contracts/EscrowDst.sol (L79-96)
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
        _uniTransfer(immutables.token.get(), immutables.maker.get(), amount);
        _ethTransfer(msg.sender, immutables.safetyDeposit);
        emit EscrowWithdrawal(secret);
    }
```

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
