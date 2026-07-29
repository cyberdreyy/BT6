## Answer

This is a valid finding. The root cause is that `ImmutablesLib`'s length checks for fee data are performed independently per-field rather than atomically, and `createDstEscrow` never validates `dstImmutables.parameters` before funding a deterministic clone.

### Title
Malformed short `parameters` blob in `createDstEscrow` permanently blocks `EscrowDst.withdraw`/`publicWithdraw`, enabling the taker to reclaim destination funds via `cancel` after already claiming the maker's source funds — (File: `contracts/EscrowDst.sol`, `contracts/libraries/ImmutablesLib.sol`)

### Summary
`BaseEscrowFactory.createDstEscrow` accepts an attacker/taker-controlled `dstImmutables.parameters` blob with no length or schema validation, and deploys+funds the clone using it as-is. [1](#0-0)  `EscrowDst._withdraw` then decodes fee amounts and fee recipients from that same blob via `ImmutablesLib`, but each accessor checks only its own minimum length independently. [2](#0-1) 

### Finding Description
`ImmutablesLib.protocolFeeAmountCd`/`integratorFeeAmountCd` only require `parameters.length >= 0x20`/`0x40`, while the corresponding recipient getters `protocolFeeRecipientCd`/`integratorFeeRecipientCd` require `>= 0x60`/`0x80`. [3](#0-2)  A `parameters` blob of exactly 64 bytes (`0x40`) passes both amount checks, but as soon as either fee amount word is non-zero, `_withdraw` unconditionally calls the matching recipient getter, which requires 96/128 bytes and reverts with `IndexOutOfRange`. [4](#0-3) 

Because the CREATE2 salt is `immutables.hashMem()`, computed over the exact same `parameters` bytes used at funding time, [5](#0-4)  the clone deploys and gets funded successfully with the malformed blob, but every future call that must reproduce the identical immutables (`withdraw` and `publicWithdraw`) will deterministically hit the same revert — including the public/anti-griefing path, since `publicWithdraw` shares the same `_withdraw` internal logic and is open to any access-token holder, not just the taker. [6](#0-5)  The only remaining exit is `cancel()`, which bypasses `ImmutablesLib` fee decoding entirely and returns the full `immutables.amount` straight to `immutables.taker` (the malicious caller of `createDstEscrow`), not to the maker. [7](#0-6) 

This lets a malicious taker/resolver: fill the source order and lock the maker's funds in `EscrowSrc`, then call `createDstEscrow` with a legitimately-funded but 64-byte-`parameters` clone (passing any naive off-chain balance checks the relayer performs). Once the maker's secret is used/obtained to unlock `EscrowSrc.withdraw()` (claiming the maker's source funds), the destination payout can never be completed by anyone — private or public — and after the `DstCancellation` timelock the same malicious taker calls `cancel()` to reclaim the destination escrow's funds for themselves.

### Impact Explanation
The maker's source-chain funds are taken by the taker while the corresponding destination payout is durably blocked and ultimately refunded to the taker instead of delivered to the maker — this is direct theft of user funds / permanent loss for the maker, and at minimum a temporary/permanent freeze of the destination payout, both within the active bounty's Critical/High categories.

### Likelihood Explanation
`createDstEscrow` is a fully permissionless, unprivileged entrypoint with no validation of `parameters` length/content, and constructing a 64-byte blob with one non-zero fee word is trivial for any caller who funds their own clone; no special privileges, governance, or third-party trust are required to trigger the revert path.

### Recommendation
Validate `dstImmutables.parameters.length` (e.g., require exactly `0x80`/128 bytes, matching the full fee-tuple encoding) in `createDstEscrow` before deployment/funding, and/or make `ImmutablesLib`'s fee-amount and fee-recipient checks atomic (single length check covering the whole fee tuple) so a short blob is rejected consistently rather than allowing a state where amounts decode but recipients cannot.

### Proof of Concept
1. Attacker (as taker) fills a source order normally, locking maker funds in `EscrowSrc`.
2. Attacker calls `createDstEscrow(dstImmutables, srcCancellationTimestamp)` with `dstImmutables.parameters = abi.encode(uint256(0), uint256(1))` (64 bytes, protocol fee 0, integrator fee 1 wei), funding the correct `amount`/`safetyDeposit`.
3. Clone deploys and is funded successfully (hash/salt matches the supplied parameters). [8](#0-7) 
4. Once the secret is available, attacker calls `EscrowSrc.withdraw` to claim the maker's source funds.
5. Any call to `EscrowDst.withdraw`/`publicWithdraw` with the exact same immutables reverts with `ImmutablesLib.IndexOutOfRange` at `integratorFeeRecipientCd()`. [9](#0-8) 
6. After `DstCancellation` timelock, attacker calls `EscrowDst.cancel()`, reclaiming `immutables.amount` for themselves. [7](#0-6)

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

**File:** contracts/EscrowDst.sol (L50-57)
```text
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
