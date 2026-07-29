Confirmed: no length validation exists anywhere in the deployment path (`createDstEscrow`, `_deployEscrow`, `Escrow._validateImmutables`) — `_validateImmutables` only checks the CREATE2 address matches, not the `parameters` byte-length or content.

## Analysis

The claim is confirmed by the code, though the "fees sum exactly to `immutables.amount`" detail in the question is a red herring — the revert is unconditional and purely length-based.

- `createDstEscrow` performs no validation on `dstImmutables.parameters` length or encoding before deploying and funding the clone: [1](#0-0) 
- `EscrowDst._withdraw` (used by both `withdraw` and `publicWithdraw`) unconditionally calls `integratorFeeAmountCd()` and `protocolFeeAmountCd()`: [2](#0-1) 
- `integratorFeeAmountCd` reverts with `IndexOutOfRange` whenever `parameters.length < 0x40` (64 bytes); `protocolFeeAmountCd` requires `>= 0x20` (32 bytes): [3](#0-2) 

A 33-byte `parameters` blob satisfies the `protocolFeeAmountCd` check (33 ≥ 32) but fails `integratorFeeAmountCd` (33 < 64), so **every** call to `_withdraw` — private or public — reverts with `IndexOutOfRange`, regardless of the actual fee values encoded. `EscrowDst.cancel` does not decode any fee fields, so it always works: [4](#0-3) 

This means the public-withdrawal safety mechanism — whose entire purpose is to let any access-token holder finalize a stuck escrow on the taker's behalf — can itself be permanently defeated by the same malformed `parameters` that breaks the private path, since both funnel through the identical `_withdraw` and identical fee-decoding calls. The only recovery path is `cancel()` (returns escrowed tokens to the taker, not the maker) or `rescueFunds` after the rescue delay, matching the question's own framing.

Since `createDstEscrow` is a fully unprivileged, self-funded entrypoint (only `msg.value`/token balance checks, no allow-list or resolver-only restriction) and permits arbitrary `parameters` bytes, an unprivileged actor deploying/funding the destination escrow can trap that escrow so the maker can never receive the destination payout through either withdrawal path, while `cancel()` after `DstCancellation` returns the locked tokens back to the depositor instead. This matches the "Temporary freezing of funds" bucket in the bounty scope, and is reachable purely through the scoped `createDstEscrow` → `publicWithdraw` path with no admin/governance assumption.

### Title
Missing `parameters` length validation in `createDstEscrow` lets a malformed 33-byte blob permanently break both `withdraw` and `publicWithdraw` on `EscrowDst` - (File: contracts/BaseEscrowFactory.sol / contracts/EscrowDst.sol)

### Summary
`createDstEscrow` accepts an arbitrary-length `parameters` byte blob with no validation. `EscrowDst._withdraw`, shared by both the private `withdraw` and the permissionless `publicWithdraw`, unconditionally decodes fee fields from `parameters` via `ImmutablesLib.integratorFeeAmountCd`/`protocolFeeAmountCd`, which revert with `IndexOutOfRange` if the blob is shorter than 64/32 bytes respectively. A 33-byte blob triggers this revert deterministically on every withdrawal attempt.

### Finding Description
`createDstEscrow` only validates `msg.value`/token transfer amounts and timelock ordering, never the shape of `dstImmutables.parameters`: [1](#0-0) . `Escrow._validateImmutables` (invoked via `onlyValidImmutables`) only re-derives the CREATE2 address from the immutables hash, so it accepts any `parameters` content/length as long as the same bytes are supplied consistently: [5](#0-4) . Both `withdraw` and `publicWithdraw` route through `_withdraw`, which reads `integratorFeeAmountCd()`/`protocolFeeAmountCd()` before any transfer occurs: [6](#0-5) . These calldata decoders enforce minimum lengths of 0x20/0x40/0x60/0x80 bytes: [3](#0-2) . A 33-byte `parameters` field is long enough to pass the protocol-fee check but too short for the integrator-fee check, so it always reverts, blocking both withdrawal entrypoints.

### Impact Explanation
Both withdrawal entrypoints on a funded `EscrowDst` become permanently unusable, so the maker can never receive their destination-chain payout through `withdraw` or `publicWithdraw`. The only path forward is `cancel()` (private, taker-only, after `DstCancellation`) or `rescueFunds` (taker-only, after the rescue delay), both of which return the escrowed value to the taker rather than paying the maker. This fits "Temporary freezing of funds" during the live swap lifecycle.

### Likelihood Explanation
`createDstEscrow` is fully permissionless and self-funded, requiring no allow-listing or special role, so any address controlling the destination-escrow creation transaction can set this trap intentionally.

### Recommendation
Enforce a strict length check on `dstImmutables.parameters` (e.g., require exactly the expected ABI-encoded size, `2 * uint256 + 2 * address`) inside `createDstEscrow` before deployment/funding, so malformed blobs are rejected at creation time rather than surfacing as an unrecoverable revert during withdrawal.

### Proof of Concept
1. Attacker calls `createDstEscrow` with a valid `hashlock`/`amount`/`timelocks` but `parameters = abi.encodePacked(bytes33(0))` (33 bytes), funding it with the correct `msg.value`/token amount.
2. Warp past `DstWithdrawal`; call `withdraw(secret, immutables)` from the taker — reverts with `IndexOutOfRange` from `integratorFeeAmountCd`.
3. Warp past `DstPublicWithdrawal`; call `publicWithdraw(secret, immutables)` from any access-token holder — reverts identically, since it shares `_withdraw`.
4. Only `cancel()` after `DstCancellation` succeeds, returning funds to the taker instead of paying the maker.

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

**File:** contracts/EscrowDst.sol (L50-92)
```text
    function publicWithdraw(bytes32 secret, Immutables calldata immutables)
        external
        onlyAccessTokenHolder()
        onlyAfter(immutables.timelocks.get(TimelocksLib.Stage.DstPublicWithdrawal))
        onlyBefore(immutables.timelocks.get(TimelocksLib.Stage.DstCancellation))
    {
        _withdraw(secret, immutables);
    }

    /**
     * @notice See {IBaseEscrow-cancel}.
     * @dev The function works on the time interval highlighted with capital letters:
     * ---- contract deployed --/-- finality --/-- private withdrawal --/-- public withdrawal --/-- PRIVATE CANCELLATION ----
     */
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

    /**
     * @dev Transfers ERC20 (or native) tokens to the maker and native tokens to the caller.
     * @param immutables The immutable values used to deploy the clone contract.
     */
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

**File:** contracts/Escrow.sol (L24-28)
```text
    function _validateImmutables(bytes32 immutablesHash) internal view virtual override {
        if (Create2.computeAddress(immutablesHash, PROXY_BYTECODE_HASH, FACTORY) != address(this)) {
            revert InvalidImmutables();
        }
    }
```
