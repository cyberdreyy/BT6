## Analysis

`createDstEscrow` accepts a caller-supplied `Immutables` struct — including the `parameters` bytes field — with **no length or content validation** whatsoever: [1](#0-0) 

Unlike the source-side flow, which explicitly validates `integratorFeeAmount + protocolFeeAmount >= takingAmount` before building the immutables, the destination flow simply takes `dstImmutables` as-is, checks `msg.value`, deploys the clone via CREATE2, and transfers `immutables.amount` in ERC20 (or requires the correct native value): [1](#0-0) 

`EscrowDst._withdraw` (used by both the private `withdraw` and the public `publicWithdraw` entrypoints) unconditionally decodes fee data from `immutables.parameters`: [2](#0-1) 

The decoding helpers in `ImmutablesLib` revert with `IndexOutOfRange()` if `parameters.length < 0x20` (32 bytes): [3](#0-2) 

Since a normal, well-formed `parameters` blob is always ABI-encoded as 4 × 32-byte words (0x80 bytes total — protocol fee amount, integrator fee amount, protocol fee recipient, integrator fee recipient), any `parameters` shorter than 32 bytes (e.g., 31 bytes) makes `protocolFeeAmountCd` revert on its very first bounds check — **before any fee value is even read**. This means the "fees sum to `amount+1`" detail in the question is not actually what triggers the freeze; the too-short `parameters` length alone is sufficient to permanently brick `_withdraw`, in **both** the private (`withdraw`) and public (`publicWithdraw`) paths, since they share the same internal `_withdraw` logic and neither has any fallback for missing/short fee data.

Both `cancel()` and `rescueFunds()`, by contrast, never touch `parameters` and work unaffected: [4](#0-3) [5](#0-4) 

Both are restricted to `onlyCaller(immutables.taker.get())` — i.e., only the malicious escrow creator (the "taker"/resolver) can reclaim the locked funds, and only after the `DstCancellation` timelock (`cancel`) or `RESCUE_DELAY` (`rescueFunds`). The maker has no path to recover the destination payout at all.

On the source chain, `EscrowSrc._withdrawTo`/`_cancel` never decode `parameters` and are unaffected by this corruption: [6](#0-5) 

This means a malicious taker could deploy a correctly-funded but parameter-corrupted `EscrowDst`, induce the maker to reveal the secret (trusting only the immutables-hash/balance check, which does not validate `parameters` well-formedness), then use that leaked secret to withdraw the maker's source-side funds via `EscrowSrc.withdraw`/`publicWithdraw` (unaffected by the corruption), while the destination funds remain undeliverable to the maker and are only recoverable back to the taker via `cancel`/`rescueFunds`. This breaks the stated invariant that "if a destination escrow was funded and the secret is known, the public withdrawal path should still be able to finalize."

### Title
Unvalidated `parameters` length in `createDstEscrow` permanently bricks both private and public `EscrowDst` withdrawal - (File: `contracts/BaseEscrowFactory.sol`, `contracts/EscrowDst.sol`)

### Summary
`createDstEscrow` does not validate the length/format of the caller-supplied `Immutables.parameters` field. An unprivileged destination-escrow creator (the "taker"/resolver) can fund an `EscrowDst` with a `parameters` blob shorter than 32 bytes. `EscrowDst._withdraw`, shared by both `withdraw()` and `publicWithdraw()`, unconditionally calls `ImmutablesLib.protocolFeeAmountCd`/`integratorFeeAmountCd`, which revert with `IndexOutOfRange()` for any `parameters.length < 0x20`. This blocks all withdrawal paths — private and public — for the life of the escrow, leaving the maker's payout unreachable until the taker voluntarily cancels or rescues the funds back to themselves.

### Impact Explanation
This is a temporary freeze of the maker's destination payout during the live swap lifecycle (recoverable only by the malicious taker, not the maker), and it can be chained into fund theft: if the maker reveals the secret trusting an on-chain balance/hash check (which does not validate `parameters`), the taker can still redeem the maker's source-chain funds via `EscrowSrc`, which is unaffected by the corrupted `parameters`, while the destination funds return to the taker via `cancel`/`rescueFunds`. This matches the "High: temporary freezing of funds during the live swap lifecycle" bounty tier, and under the secret-leak scenario approaches theft of the maker's source-side funds.

### Likelihood Explanation
High. `createDstEscrow` is a fully permissionless external function with no whitelist/access-control modifier and no validation on `dstImmutables.parameters`, so any address funding the correct `msg.value`/token amount can trigger this by supplying a short `parameters` byte string. No privileged role, governance action, or off-chain trust failure is required beyond the taker being the one who deploys the destination escrow, which is a normal, expected actor in the destination path.

### Recommendation
Validate `dstImmutables.parameters` in `createDstEscrow` (e.g., require it to be exactly 0x80 bytes, or empty with a defined all-zero-fee fallback) before deploying the clone, and/or make `EscrowDst._withdraw` gracefully treat missing/incorrectly sized `parameters` as zero fees instead of reverting, so that a funded escrow can never be rendered permanently unwithdrawable by malformed fee metadata.

### Proof of Concept
1. Attacker (taker) calls `createDstEscrow(dstImmutables, srcCancellationTimestamp)` with `dstImmutables.parameters` set to a 31-byte value (any content) and sends the correct `msg.value`/ERC20 approval for `amount + safetyDeposit`.
2. The clone deploys successfully and is funded — `BaseEscrowFactory.createDstEscrow` performs no check on `parameters` length: [1](#0-0) 
3. Maker obtains the secret and calls (or waits for someone to call) `dstClone.withdraw(secret, immutables)` during the private window — it reverts in `immutables.integratorFeeAmountCd()` with `IndexOutOfRange()`.
4. After `DstPublicWithdrawal`, any access-token holder calls `dstClone.publicWithdraw(secret, immutables)` — it reverts identically since it invokes the same `_withdraw` internal function: [2](#0-1) 
5. Only the taker can later call `cancel()` (after `DstCancellation`) or `rescueFunds()` (after `RESCUE_DELAY`) to reclaim the escrowed funds for themselves — the maker never receives the destination payout.

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

**File:** contracts/EscrowDst.sol (L50-96)
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
        _uniTransfer(immutables.token.get(), immutables.maker.get(), amount);
        _ethTransfer(msg.sender, immutables.safetyDeposit);
        emit EscrowWithdrawal(secret);
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

**File:** contracts/BaseEscrow.sol (L71-79)
```text
    function rescueFunds(address token, uint256 amount, Immutables calldata immutables)
        external
        onlyCaller(immutables.taker.get())
        onlyValidImmutables(immutables.hash())
        onlyAfter(immutables.timelocks.rescueStart(RESCUE_DELAY))
    {
        _uniTransfer(token, msg.sender, amount);
        emit FundsRescued(token, amount);
    }
```

**File:** contracts/EscrowSrc.sol (L111-132)
```text
    function _withdrawTo(bytes32 secret, address target, Immutables calldata immutables)
        internal
        onlyValidImmutables(immutables.hash())
        onlyValidSecret(secret, immutables.hashlock)
    {
        IERC20(immutables.token.get()).safeTransfer(target, immutables.amount);
        _ethTransfer(msg.sender, immutables.safetyDeposit);
        emit EscrowWithdrawal(secret);
    }

    /**
     * @dev Transfers ERC20 tokens to the maker and native tokens to the caller.
     * @param immutables The immutable values used to deploy the clone contract.
     */
    function _cancel(Immutables calldata immutables)
        internal
        onlyValidImmutables(immutables.hash())
    {
        IERC20(immutables.token.get()).safeTransfer(immutables.maker.get(), immutables.amount);
        _ethTransfer(msg.sender, immutables.safetyDeposit);
        emit EscrowCancelled();
    }
```
