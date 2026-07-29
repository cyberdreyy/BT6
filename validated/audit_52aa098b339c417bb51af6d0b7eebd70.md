## Analysis

This is a valid, in-scope finding. The root cause is that `createDstEscrow` never validates the `parameters` field of `dstImmutables`, and `EscrowDst._withdraw` (used by both `withdraw` and `publicWithdraw`) unconditionally decodes fee data from that field with no length checks before decoding.

**Entry point — no validation of `parameters`:**
`createDstEscrow` is callable by anyone, checks only the native-value/token transfer amounts, and stores whatever `dstImmutables` (including an arbitrary-length `parameters` blob) the caller supplies as the CREATE2 salt/immutables hash. [1](#0-0) 

**Both withdrawal paths funnel through the same fee-decoding logic:**
`withdraw` and `publicWithdraw` both call the internal `_withdraw`, which unconditionally calls `integratorFeeAmountCd()`/`protocolFeeAmountCd()` before any transfer happens. [2](#0-1) 

**Fee decoders revert on malformed/empty `parameters`:**
`ImmutablesLib.protocolFeeAmountCd`/`integratorFeeAmountCd` (and the recipient variants) require `parameters.length` to be at least `0x20`/`0x40`/`0x60`/`0x80` bytes respectively, reverting with `IndexOutOfRange()` otherwise. [3](#0-2) 

`cancel()`, by contrast, performs a plain transfer of the full `amount` back to the `taker` and never touches the fee fields, so it remains callable regardless of how `parameters` is encoded. [4](#0-3) 

**Consequence:** an unprivileged actor calling `createDstEscrow` with `parameters: ""` (or any blob shorter than `0x40` bytes) funds a valid, correctly-hashed `EscrowDst` for which **both** the private `withdraw` (taker-only) and the `publicWithdraw` (access-token holder fallback) permanently revert on `IndexOutOfRange`, even with a correct secret. The only way out of this escrow is `cancel()` after `DstCancellation`, which returns the deposited destination tokens to the `taker` who created the escrow — not to the `maker` who was supposed to receive them. Since the whole purpose of `publicWithdraw` is to guarantee liveness/settlement to the maker even when the private taker is unresponsive, this defect nullifies that guarantee: the taker (who fully controls `createDstEscrow`'s calldata and has no privileged role requirement) can grief/route funds back to itself while the maker's expected destination payout is uncollectable, meeting the "temporary freezing of funds during the live swap lifecycle" bounty class (and edges toward fund misappropriation, since the reclaimed tokens go to the taker rather than the intended maker).

### Title
Unvalidated `parameters` length in `createDstEscrow` lets an unprivileged taker permanently break both `withdraw` and `publicWithdraw` on `EscrowDst`, freezing the maker's destination payout - (File: `contracts/EscrowDst.sol`, `contracts/BaseEscrowFactory.sol`, `contracts/libraries/ImmutablesLib.sol`)

### Summary
`createDstEscrow` accepts a caller-supplied `Immutables.parameters` blob with no length/shape validation. `EscrowDst._withdraw`, used by both the private `withdraw` and the public-fallback `publicWithdraw`, unconditionally decodes fee amounts/recipients from that blob via `ImmutablesLib`, which reverts with `IndexOutOfRange()` if the blob is shorter than 0x40 bytes. An unprivileged escrow creator can therefore deploy a fully funded, hash-valid `EscrowDst` whose `parameters` is empty or truncated, guaranteeing that neither the private nor the public withdrawal path can ever succeed — only `cancel()` (which returns funds to the `taker`, not the `maker`) works.

### Finding Description
See analysis above: `createDstEscrow` has no guard on `dstImmutables.parameters` [1](#0-0) , and `_withdraw` always calls the strict-length fee decoders [5](#0-4) [6](#0-5) , which revert for any `parameters` shorter than expected. Both `withdraw` and `publicWithdraw` invoke `_withdraw`, so both revert identically [7](#0-6) .

### Impact Explanation
The invariant that a funded escrow with a known secret can always be finalized via the public path is broken. The maker's intended destination payout becomes permanently unreachable through `withdraw`/`publicWithdraw`; the only recovery path (`cancel`) redirects the escrowed destination tokens to the `taker` who deliberately crafted the malformed immutables, not to the `maker`. This fits the "temporary freezing of funds during the live swap lifecycle" bounty tier at minimum, and results in the maker never collecting the swap counter-value it was owed.

### Likelihood Explanation
High — `createDstEscrow` is a fully public, unprivileged entry point; crafting `parameters` as an empty (or too-short) `bytes` value requires no special access and costs nothing extra beyond normal escrow funding.

### Recommendation
Validate `dstImmutables.parameters` length (and ideally the encoded fee amounts against `amount`) inside `createDstEscrow` before deployment, rejecting malformed fee metadata up front so a malformed escrow can never be funded.

### Proof of Concept
1. Call `createDstEscrow{value: safetyDeposit (+amount if native)}(dstImmutables, srcCancellationTimestamp)` with `dstImmutables.parameters = ""`.
2. Warp past `DstWithdrawal` and call `withdraw(secret, immutables)` from the `taker` — reverts with `IndexOutOfRange`.
3. Warp past `DstPublicWithdrawal` and call `publicWithdraw(secret, immutables)` from any access-token holder — reverts with `IndexOutOfRange`.
4. Warp past `DstCancellation` and call `cancel(immutables)` — succeeds, returning the destination tokens to the `taker` (the creator), while the `maker` never receives any payout.

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

**File:** contracts/EscrowDst.sol (L36-96)
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
