Confirmed: `_validateImmutables` (contracts/Escrow.sol:24-28) only checks that the CREATE2 address derived from the immutables hash matches the clone address — it never validates the internal structure or length of `parameters`. That closes the loop: nothing in the deployment or withdrawal path enforces that `parameters` is 128 bytes.

### Title
Malformed `parameters` blob in `createDstEscrow` permanently blocks `withdraw`/`publicWithdraw`, letting the taker recover the maker's destination payout via `cancel()` - (File: contracts/BaseEscrowFactory.sol, contracts/EscrowDst.sol, contracts/libraries/ImmutablesLib.sol)

### Summary
`BaseEscrowFactory.createDstEscrow` accepts caller-supplied `dstImmutables.parameters` with no length or structural validation before funding and deploying the destination clone. `EscrowDst._withdraw` (called by both private `withdraw` and public `publicWithdraw`) decodes fee fields from that blob via `ImmutablesLib`'s calldata accessors, which `revert IndexOutOfRange()` if the blob is too short for the field being read. Because the read is unconditional whenever the corresponding fee amount is nonzero, an escrow creator can craft a 96-byte `parameters` blob that always causes `_withdraw` to revert for every caller — defeating the `publicWithdraw` safety valve that exists specifically to stop a malicious taker from griefing the maker.

### Finding Description
`createDstEscrow` builds `immutables` directly from calldata and only checks native-value/timelock consistency before deploying and funding the clone: [1](#0-0) 

`ImmutablesLib`'s calldata accessors gate on `parameters.length`: [2](#0-1) 

`EscrowDst._withdraw` unconditionally reads `integratorFeeAmountCd()` (needs ≥0x40) and `protocolFeeAmountCd()` (needs ≥0x20), then — only if the amount is nonzero — reads the corresponding recipient (`integratorFeeRecipientCd()` needs ≥0x80, `protocolFeeRecipientCd()` needs ≥0x60): [3](#0-2) 

Both `withdraw` and `publicWithdraw` funnel into `_withdraw` with no other divergence: [4](#0-3) 

`cancel()`, however, never touches `parameters` at all — it just transfers `immutables.amount` back to the taker: [5](#0-4) 

And immutables validation only checks that the CREATE2 address matches — it never validates `parameters` content/length: [6](#0-5) 

An escrow creator (the "taker" role, unrestricted — `createDstEscrow` has no access control) can set `parameters = abi.encode(protocolFeeAmount, integratorFeeAmount, protocolFeeRecipient)` (96 bytes) with a nonzero `integratorFeeAmount` close to the full `amount`. This satisfies `integratorFeeAmountCd()` (needs 64 bytes) and `protocolFeeAmountCd()` (needs 32 bytes), so both succeed, but then `_withdraw` calls `integratorFeeRecipientCd()`, which requires 128 bytes and reverts `IndexOutOfRange()` — for every caller, always, regardless of `msg.sender` or timing.

### Impact Explanation
Because the revert condition depends only on the immutables (which are fixed once the clone is deployed and hash-validated), it is not caller- or time-dependent: neither the taker's private `withdraw` nor the permissionless `publicWithdraw` (meant to prevent exactly this kind of resolver griefing) can ever succeed for that escrow. The only remaining path is `cancel()`, callable solely by the taker after `DstCancellation`, which is unaffected by `parameters` and always returns the full deposited `amount` to the taker. The practical consequence: the taker can fund a destination escrow whose payout to the maker is guaranteed to be permanently blocked, and after the timelock reclaim their own deposit in full via `cancel()` — while the maker never receives their destination-side settlement, even though the secret may already have been consumed to release the corresponding source-side funds. This fits "temporary freezing of funds during the live swap lifecycle" (funds are locked and unspendable for the maker throughout the entire withdrawal/public-withdrawal window) and functionally results in the maker's expected payout never being delivered.

### Likelihood Explanation
Any address can call `createDstEscrow` (no access control), and constructing a short `parameters` blob with a nonzero integrator (or protocol) fee amount requires no special privilege — only that the caller (who is also the escrow's `taker`/funder) intentionally deviates from the canonical 128-byte fee-tuple format that `_postInteraction` and honest tooling always produce. This is a business-logic gap rather than a theoretical edge case: nothing in `createDstEscrow` or `_validateImmutables` enforces the expected `parameters` shape.

### Recommendation
In `BaseEscrowFactory.createDstEscrow`, validate that `dstImmutables.parameters.length` equals the canonical fixed size (128 bytes: two `uint256` fee amounts + two `Address` recipients) before deploying/funding the clone, reverting otherwise. Alternatively, have `ImmutablesLib`/`EscrowDst._withdraw` treat a too-short `parameters` as "fee amount = 0 / no recipient" rather than reverting, or perform a single upfront length check in `_withdraw` that fails safely without corrupting the payout guarantee.

### Proof of Concept
1. Attacker (as `taker`) calls `createDstEscrow` with `dstImmutables.parameters = abi.encode(uint256(0), uint256(amount - 1), address(protocolFeeRecipient))` (96 bytes; nonzero `integratorFeeAmount`), funding the clone with `amount + safetyDeposit`.
2. Once `DstWithdrawal` opens, the taker (or, once `DstPublicWithdrawal` opens, anyone) calls `withdraw`/`publicWithdraw` with the correct secret; `_withdraw` reads `integratorFeeAmountCd()` (succeeds, nonzero), then calls `integratorFeeRecipientCd()`, which requires `parameters.length >= 0x80` (128) but only 96 bytes exist — reverts `IndexOutOfRange()`.
3. This revert is deterministic and caller-independent, so it repeats for every subsequent attempt through the entire withdrawal/public-withdrawal window.
4. After `DstCancellation` timelock, the taker calls `cancel()` (unaffected by `parameters`), reclaiming the full `amount` plus getting the safety deposit refunded to the caller — the maker receives nothing from this escrow despite it having been "successfully funded."

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

**File:** contracts/Escrow.sol (L24-28)
```text
    function _validateImmutables(bytes32 immutablesHash) internal view virtual override {
        if (Create2.computeAddress(immutablesHash, PROXY_BYTECODE_HASH, FACTORY) != address(this)) {
            revert InvalidImmutables();
        }
    }
```
