Confirmed: `rescueFunds` is also restricted to `onlyCaller(immutables.taker.get())`, meaning only the taker (the same account that funded the malformed escrow) can rescue funds after `RESCUE_DELAY` — so the maker has no independent recovery path at all, and `cancel()` also only pays out to the taker.

Based on the full trace, this is a valid, in-scope finding.

### Title
Malformed `parameters` blob in `createDstEscrow` bricks both private and public withdrawal on `EscrowDst`, freezing the maker's payout - (File: `contracts/EscrowDst.sol`, `contracts/libraries/ImmutablesLib.sol`, `contracts/BaseEscrowFactory.sol`)

### Summary
`BaseEscrowFactory.createDstEscrow` is a fully permissionless entrypoint that accepts arbitrary `dstImmutables`, including an attacker-controlled `parameters` byte blob, with no validation of its length or content against the encoded fee amounts. [1](#0-0) . Because `EscrowDst._withdraw` unconditionally calls the fee-amount getters and then conditionally calls the fee-recipient getters (which require a longer `parameters` buffer), an attacker can craft a 95-byte `parameters` blob with non-zero (even dust) fee amounts so that fee-recipient decoding always reverts, bricking both `withdraw` and `publicWithdraw`.

### Finding Description
`ImmutablesLib` enforces increasing minimum lengths for each field packed into `parameters`: `protocolFeeAmountCd`/`integratorFeeAmountCd` require `>= 0x20`/`0x40` bytes, while `protocolFeeRecipientCd`/`integratorFeeRecipientCd` require `>= 0x60`/`0x80` bytes [2](#0-1) .

`EscrowDst._withdraw` reads the fee amounts first, then, only if a fee amount is non-zero, reads the corresponding recipient address: [3](#0-2) 

With a `parameters` blob of exactly 95 bytes (`0x60 - 1`), `protocolFeeAmountCd` (needs `0x20`) and `integratorFeeAmountCd` (needs `0x40`) succeed and can be made to return any non-zero dust value the attacker encodes in the first 64 bytes. But `protocolFeeRecipientCd` (needs `0x60=96`) and `integratorFeeRecipientCd` (needs `0x80=128`) both revert with `IndexOutOfRange` since `95 < 96`. Both `withdraw()` (private, taker-restricted) and `publicWithdraw()` (open to any access-token holder) funnel through the same `_withdraw` internal function [4](#0-3) , so neither path can ever complete once the malformed `parameters` and non-zero fee amounts are baked into the deployed escrow's immutables (which are hashed and pinned via `onlyValidImmutables`, so they cannot be corrected after deployment).

`createDstEscrow` performs no cross-check of `dstImmutables.parameters` against `dstImmutables.amount` or any fee-sum bound — unlike the source-chain `_postInteraction` path, which explicitly checks `integratorFeeAmount + protocolFeeAmount >= takingAmount` before creating the source escrow [5](#0-4) . No equivalent check exists for the destination escrow's `parameters` field, and `createDstEscrow` has no whitelist/access-control restriction on who may call it — any address that supplies the required `msg.value`/ERC20 approval can deploy the escrow [1](#0-0) .

Once bricked, recovery is severely limited: `cancel()` pays the full `immutables.amount` back to `immutables.taker` (not the maker) and is restricted with `onlyCaller(immutables.taker.get())` [6](#0-5) ; `rescueFunds` is similarly `onlyCaller(immutables.taker.get())` and only usable after `RESCUE_DELAY` [7](#0-6) . There is no `publicCancel` in `EscrowDst` (unlike `EscrowSrc`). This means the maker (who is owed the destination payout and who revealed the secret to enable it) has no path at all — private, public, or self-service — to ever receive funds from this escrow. Only the taker (who deployed and funded it, and who chose the malformed parameters) can recover the locked value, and only by reclaiming it entirely for themselves via `cancel()` once the cancellation timelock passes.

### Impact Explanation
This matches the "High: temporary freezing of funds during the live swap lifecycle" bucket at minimum, and arguably crosses into fund theft: a malicious taker/resolver can deploy a destination escrow for a real order, fund it (satisfying `createDstEscrow`'s balance checks), obtain the maker's revealed secret to withdraw the maker's source-chain tokens via `EscrowSrc.withdraw`, and then simply let/force the destination-chain payout to permanently revert for the maker while reclaiming their own deposited destination tokens via `cancel()` once the `DstCancellation` timelock passes. The maker ends up paying on the source chain but never receiving the corresponding destination-chain funds through any code path.

### Likelihood Explanation
`createDstEscrow` is unprivileged and takes attacker-supplied `Immutables.parameters` verbatim with zero validation of length or consistency with the fee amounts, and the immutables hash is fixed at deployment via `onlyValidImmutables`, so the malformed blob cannot be repaired afterward. Constructing a 95-byte ABI-encoded blob with small non-zero fee values is trivial and requires no special privileges, whitelisting, or governance access — only the ability to call `createDstEscrow` with the required token/native funding.

### Recommendation
Validate the `parameters` field in `createDstEscrow` (e.g., require exact expected length `0x80`, or that decoded `protocolFeeAmount + integratorFeeAmount <= amount` and that the blob length is sufficient for whichever recipient fields are non-zero) before deploying the escrow. Additionally, consider making `EscrowDst`'s fee decoding tolerant of missing/zero-length recipient fields when the corresponding fee amount is zero, and/or add a `publicCancel`-equivalent recovery path on `EscrowDst` so that the maker (or any access-token holder) is not permanently dependent on the taker's cooperation to unlock funds after a malformed deployment.

### Proof of Concept
1. Attacker (as taker) calls `createDstEscrow` with `dstImmutables.amount = X`, valid `hashlock`, and `dstImmutables.parameters = abi.encode(dustProtocolFee, dustIntegratorFee)` truncated/crafted to be exactly 95 bytes (e.g., by hand-packing 95 bytes instead of the canonical 128-byte `abi.encode(protocolFeeAmount, integratorFeeAmount, protocolFeeRecipient, integratorFeeRecipient)`), with `dustProtocolFee > 0`.
2. Attacker funds the call with the required `msg.value`/ERC20 approval; escrow deploys successfully (no parameter-length check in `createDstEscrow`).
3. Maker reveals `secret`; attacker or anyone calls `EscrowDst.withdraw`/`publicWithdraw(secret, immutables)` during the respective windows — both revert with `ImmutablesLib.IndexOutOfRange` inside `protocolFeeRecipientCd`.
4. After `DstCancellation`, attacker (the taker) calls `cancel(immutables)` and reclaims the entire `immutables.amount` for themselves; the maker never receives destination-chain funds through any path.

### Citations

**File:** contracts/BaseEscrowFactory.sol (L91-92)
```text

        if (integratorFeeAmount + protocolFeeAmount >= takingAmount) revert InvalidFeeAmounts();
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
