## Finding Confirmed

The question identifies a real bug rooted in an inconsistency between `ImmutablesLib`'s amount-decoding length checks and its recipient-decoding length checks, combined with the fact that `createDstEscrow` never validates `parameters` for internal consistency.

### Title
Inconsistent `parameters` length checks in `ImmutablesLib` permanently brick both private and public `EscrowDst` withdrawal, leaking the secret and enabling src-side theft — (`contracts/libraries/ImmutablesLib.sol`, `contracts/EscrowDst.sol`)

### Summary
`createDstEscrow` lets the caller (the taker/resolver who funds the escrow) supply an arbitrary `parameters` byte blob with no validation of its length or internal consistency: [1](#0-0) 
The `Immutables.hash()`/`hashMem()` used both as the CREATE2 salt and for the `onlyValidImmutables` check simply hash whatever `parameters` bytes are supplied — there is no on-chain requirement that `parameters` be either empty or the full 128-byte fee tuple.

### Finding Description
`ImmutablesLib` decodes fee fields from `parameters` using per-field minimum-length guards that are inconsistent with each other:
- `protocolFeeAmountCd`/`integratorFeeAmountCd` only require `parameters.length >= 0x20` / `>= 0x40`.
- `protocolFeeRecipientCd`/`integratorFeeRecipientCd` require `parameters.length >= 0x60` / `>= 0x80`. [2](#0-1) 

`EscrowDst._withdraw` first reads the two fee *amounts*, and only if an amount is non-zero does it then read the corresponding *recipient*: [3](#0-2) 

If a destination-escrow creator supplies a `parameters` blob of exactly 64 bytes (`0x40`) whose first or second 32-byte word decodes to a non-zero value, both `protocolFeeAmountCd`/`integratorFeeAmountCd` succeed (64 ≥ 32 and 64 ≥ 64), but the subsequent `protocolFeeRecipientCd`/`integratorFeeRecipientCd` call reverts with `IndexOutOfRange` because 64 < 96/128. This revert path is reached identically from `withdraw` (private, `onlyCaller(taker)`) and `publicWithdraw` (public, `onlyAccessTokenHolder`), since both call the same internal `_withdraw`: [4](#0-3) 

Because the immutables (including `parameters`) are committed at deployment time via the CREATE2 salt and re-validated on every call via `onlyValidImmutables(immutables.hash())`, no alternate/corrected `parameters` value can be substituted later — the escrow is permanently unable to complete `withdraw`/`publicWithdraw` with these immutables.

Beyond the freeze itself, since Ethereum records calldata for reverted transactions on-chain, a maker who submits `withdraw(secret, immutables)`/`publicWithdraw` (believing the escrow is correctly configured) will have `secret` permanently exposed on the destination chain even though the destination-side call reverts. Because `hashlock = keccak256(secret)` is shared between the source and destination escrows for the same swap, this leaked secret lets the taker (or any observer) immediately claim the maker's already-deposited funds from the corresponding `EscrowSrc` on the source chain, while the destination funds intended for the maker remain stuck in `EscrowDst` until the taker (only the taker, per `cancel`'s `onlyCaller(taker)`) reclaims them back via `cancel()` after the cancellation timelock: [5](#0-4) 

### Impact Explanation
This lets a resolver/taker who creates the destination escrow (an unprivileged action reachable via the normal `createDstEscrow` fill path — no admin/governance rights required) construct a malformed `parameters` blob that:
1. Guarantees both private and public withdrawal paths always revert (permanent freeze of the maker's destination payout), and
2. Causes the maker's secret to be exposed on-chain the moment they attempt to withdraw, which the taker can use to steal the maker's source-side funds while separately recovering their own destination-side deposit via `cancel()`.

This satisfies the Critical bar (permanent freezing of funds / theft of user funds) since the fee-decode revert is unconditional and unrecoverable for the given immutables, and it enables theft of the maker's source-chain funds via the leaked secret.

### Likelihood Explanation
Any actor who is allowed to call `createDstEscrow` (any funded taker/resolver) can trigger this without needing special access, since `parameters` is fully attacker-supplied and unchecked for length/consistency by the factory.

### Recommendation
`ImmutablesLib` should enforce that `parameters` is either exactly `0` bytes (no fees) or exactly `0x80` (128) bytes (full fee tuple), and reject any other length up front (e.g., in a single length check performed once, rather than four independent minimum-length checks). Alternatively, `createDstEscrow` should validate `parameters.length` is one of the two allowed values before deploying the escrow, so malformed fee metadata can never be committed into the CREATE2 salt/immutables hash in the first place.

### Proof of Concept
1. Attacker (taker) calls `createDstEscrow` with `dstImmutables.parameters = abi.encode(uint256(X), uint256(Y))` (64 bytes total) where `X` or `Y` is non-zero, funding it with the required token amount + safety deposit.
2. Maker, once the escrow is confirmed, submits `publicWithdraw(secret, immutables)` (or the taker submits `withdraw`) after the relevant timelock.
3. `_withdraw` reads `protocolFeeAmountCd`/`integratorFeeAmountCd` successfully (64 ≥ 32/64), computes a non-zero fee, then calls `protocolFeeRecipientCd`/`integratorFeeRecipientCd`, which reverts with `IndexOutOfRange` (64 < 96/128) — confirmed by tracing `contracts/libraries/ImmutablesLib.sol` lines 76-121 against `contracts/EscrowDst.sol` lines 79-96.
4. The maker's `secret` is now public in the reverted transaction's calldata; the taker uses it to claim the maker's funds from `EscrowSrc`, and after the `DstCancellation` timelock, calls `cancel()` on the (still fee-broken) `EscrowDst` to reclaim the full escrowed destination amount.

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
