## Analysis

This is a real bug in `ImmutablesLib`/`EscrowDst`. The root cause is that `BaseEscrowFactory.createDstEscrow()` is a permissionless entrypoint that never validates the shape of `dstImmutables.parameters`: [1](#0-0) 

`ImmutablesLib` decodes the four fee fields from `parameters` using four independent, increasing length thresholds (`0x20`, `0x40`, `0x60`, `0x80`) instead of requiring the whole 128-byte block up front: [2](#0-1) 

`EscrowDst._withdraw()` reads `integratorFeeAmountCd()` (needs ≥ `0x40` = 64 bytes) and, only if the value is non-zero, reads `integratorFeeRecipientCd()` (needs ≥ `0x60` = 96 bytes): [3](#0-2) 

A 65-byte `parameters` blob satisfies the 64-byte check (so `integratorFeeAmountCd()` returns whatever attacker-chosen 32-byte word occupies offset 32, e.g. "roughly half of `amount`") but fails the 96-byte check for the recipient, so `_withdraw` always reverts with `IndexOutOfRange`. Because `withdraw()` and `publicWithdraw()` both funnel into `_withdraw`, and because the salted CREATE2 address / `onlyValidImmutables` hash check locks the parameters permanently once the clone is deployed, this makes *both* the private-window and public-window withdrawal paths permanently unusable for that specific escrow: [4](#0-3) 

Notably `cancel()` never touches the fee fields, so the escrow creator (who must also be `immutables.taker`, since `withdraw`/`cancel` are gated by `onlyCaller(immutables.taker.get())`) can still reclaim the full deposited amount via `cancel()` after the cancellation timelock: [5](#0-4) 

This is meaningful beyond "a malicious taker could just refuse to withdraw," because the protocol's designed defense against an unresponsive/malicious taker is `publicWithdraw`, callable by any access-token holder during the public window to force payment to the maker. The malformed-parameters trick defeats that safety net too — every caller, not just the taker, hits the same revert — so resolution is only possible via `cancel()`, which returns funds to the taker instead of paying the intended maker. This directly breaks the stated invariant that "every funded destination escrow should remain withdrawable with the exact parameters used to create it."

### Caveats worth flagging
- The escrow's `taker`/creator is the same party who funds it and who benefits from `cancel()`, so in isolation this doesn't let a third party steal fresh funds — it lets the escrow creator guarantee non-payment to the specified maker while recovering their own deposit, something a naive griefing taker could partially achieve anyway by just not calling `withdraw`. The distinguishing factor is that this bug additionally disables `publicWithdraw`'s decentralization fallback, which is the part that would normally force payment through despite an unwilling taker.
- Whether the maker actually loses value depends on the (off-chain/cross-chain) assumption that they already gave up equivalent value on the source chain before this dst escrow becomes unwithdrawable — this linkage is not itself enforced or verifiable purely within `EscrowDst`/`ImmutablesLib`.

### Title
Malformed short `parameters` blob permanently bricks `EscrowDst.withdraw`/`publicWithdraw`, defeating the public-withdrawal safety net - (File: `contracts/EscrowDst.sol`, `contracts/libraries/ImmutablesLib.sol`)

### Summary
`createDstEscrow` accepts an attacker-supplied `Immutables.parameters` blob with no length/shape validation. A carefully sized blob (e.g. 65 bytes, with a non-zero value at the integrator-fee-amount slot) passes the `0x40`-byte length gate in `ImmutablesLib.integratorFeeAmountCd` but fails the `0x60`-byte gate in `integratorFeeRecipientCd`, so every call into `EscrowDst._withdraw` (used by both `withdraw` and `publicWithdraw`) reverts with `IndexOutOfRange` for the lifetime of that escrow.

### Finding Description
`ImmutablesLib` (`contracts/libraries/ImmutablesLib.sol:76-121`) gates decoding of the four fee-related fields with four separate, increasing length thresholds rather than requiring the entire fixed-size block atomically. `EscrowDst._withdraw` (`contracts/EscrowDst.sol:79-96`) conditionally reads the recipient only if the corresponding amount is non-zero. A `parameters` value whose length sits strictly between one threshold and the next (e.g., between `0x40` and `0x60`) lets the amount decode succeed while making the recipient decode always fail, so `_withdraw` can never complete successfully. Since the CREATE2 salt / `onlyValidImmutables` hash check binds the escrow to these exact immutables forever, there is no way to "fix" the parameters after deployment.

### Impact Explanation
Funds sent to the destination clone (both the ERC20/native `amount` and the native `safetyDeposit`) cannot be released via `withdraw` or `publicWithdraw` — not even by a good-faith third-party access-token holder acting as the decentralization fallback. Resolution is only possible via `cancel()` after the cancellation timelock, which pays the full amount back to `immutables.taker` rather than the intended maker, breaking the intended payout guarantee for a live swap. This fits "temporary freezing of funds during the live swap lifecycle."

### Likelihood Explanation
The entrypoint `createDstEscrow` is fully permissionless and takes `dstImmutables` as raw calldata with no shape validation, so any unprivileged caller acting as `taker` can trigger this deterministically and cheaply (a single crafted call).

### Recommendation
Enforce that `parameters` is either empty or exactly the full fixed-size fee-block length (`0x80` bytes) in `createDstEscrow` (or in a shared validation helper used before deployment), instead of relying on `ImmutablesLib`'s per-field length checks alone.

### Proof of Concept
1. Call `createDstEscrow` with `dstImmutables.parameters` set to a 65-byte blob whose bytes `[32:64]` encode a non-zero `integratorFeeAmount` and fund the required `msg.value`/token balance.
2. Wait for the withdrawal window to open.
3. Call `EscrowDst.withdraw(secret, immutables)` (or `publicWithdraw`) — it reverts inside `ImmutablesLib.integratorFeeRecipientCd` with `IndexOutOfRange`.
4. Confirm the same revert occurs for any caller, then confirm `cancel()` (post-cancellation timelock) succeeds and returns funds to `taker`, not the maker.

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
