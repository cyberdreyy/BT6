Based on the code, this is a legitimate vulnerability. Here's the analysis:

`createDstEscrow` accepts `dstImmutables` (including the `parameters` field) entirely from `msg.sender` with **no validation whatsoever** on its length or content [1](#0-0) . Meanwhile, `EscrowDst._withdraw` unconditionally decodes `integratorFeeAmountCd()` (requires `parameters.length >= 0x40`) and `protocolFeeAmountCd()` (requires `>= 0x20`), then *conditionally* decodes the corresponding recipient (`protocolFeeRecipientCd()` requires `>= 0x60`, `integratorFeeRecipientCd()` requires `>= 0x80`) only if the matching fee amount is non-zero [2](#0-1) . The length gates are defined in `ImmutablesLib` [3](#0-2) .

With a 95-byte `parameters` blob and a non-zero `protocolFeeAmount`, the length is enough to pass `protocolFeeAmountCd`/`integratorFeeAmountCd` (need ≥64) but insufficient for `protocolFeeRecipientCd` (needs ≥96), so `IndexOutOfRange()` reverts every single call into `_withdraw` — both `withdraw` (taker-only) [4](#0-3)  and `publicWithdraw` (open to any access-token holder) [5](#0-4) , since both route through the same `_withdraw` internal function and both are constrained to use the exact same immutables (validated via `onlyValidImmutables(immutables.hash())`). Since `parameters` is fixed at deployment (part of the CREATE2 salt/hash), there's no way to "fix" it after the fact — the malformed length permanently blocks both withdrawal entry points for that escrow instance.

The only remaining paths are `cancel()` (taker-only, after `DstCancellation`) [6](#0-5)  and `rescueFunds` (taker-only, after the rescue delay) [7](#0-6)  — both of which return the escrowed token/native amount to the **taker**, not the maker. Since `createDstEscrow` can be called by anyone funding it themselves, a malicious taker/resolver can deliberately create a destination escrow with this malformed `parameters` blob for a real order, guaranteeing the maker can never actually receive the destination payout (neither privately nor via the public-access-token path) while the taker eventually reclaims their own deposited collateral via `cancel()`. This fits the bounty's "temporary/permanent freezing of funds" impact class since it originates from a missing on-chain validation in `createDstEscrow`/`ImmutablesLib`, not from privileged/admin misbehavior — the taker/resolver is an explicitly untrusted role in this protocol's threat model.

### Title
Missing validation of `parameters` length/consistency in `createDstEscrow` allows an unprivileged taker to permanently brick both `withdraw` and `publicWithdraw` on `EscrowDst` - (File: `contracts/BaseEscrowFactory.sol`, `contracts/EscrowDst.sol`, `contracts/libraries/ImmutablesLib.sol`)

### Summary
`createDstEscrow` deploys `EscrowDst` clones using attacker-controlled `Immutables.parameters` bytes with no length or content validation. `ImmutablesLib`'s fee-amount getters are always called unconditionally in `_withdraw`, but the fee-recipient getters are only called when the corresponding fee amount is non-zero and require a longer `parameters` buffer. By choosing a `parameters` length that satisfies the fee-amount decode but is too short for the fee-recipient decode (e.g., 95 bytes with `protocolFeeAmount > 0`), an unprivileged escrow creator (the taker) can make both `withdraw` and `publicWithdraw` always revert with `IndexOutOfRange`, for the lifetime of that escrow.

### Finding Description
- `createDstEscrow` copies `dstImmutables` (attacker-supplied calldata) verbatim into the deployed clone's salt/state, including `parameters`, with zero checks on its length or on fee-amount/recipient consistency [1](#0-0) .
- `EscrowDst._withdraw` always calls `integratorFeeAmountCd()` and `protocolFeeAmountCd()`, and conditionally calls `integratorFeeRecipientCd()`/`protocolFeeRecipientCd()` only if the amount is non-zero [8](#0-7) .
- The `ImmutablesLib` getters enforce different minimum lengths: 0x20 for `protocolFeeAmountCd`, 0x40 for `integratorFeeAmountCd`, 0x60 for `protocolFeeRecipientCd`, 0x80 for `integratorFeeRecipientCd` [3](#0-2) .
- A `parameters` blob of exactly 95 bytes (0x5F) passes the 0x40 check for `integratorFeeAmountCd` and the 0x20 check for `protocolFeeAmountCd`, but fails the 0x60 check inside `protocolFeeRecipientCd` whenever `protocolFeeAmount > 0`, reverting with `IndexOutOfRange`.
- Both `withdraw` (private, taker-only) and `publicWithdraw` (public, access-token gated) call the same `_withdraw` internal function with the identical, hash-committed `immutables` [9](#0-8) , so both entrypoints revert unconditionally and permanently for this escrow instance — the secret being correct is irrelevant.
- The only functioning exits are `cancel()` and `rescueFunds()`, both restricted to `onlyCaller(immutables.taker.get())` and both returning the locked amount to the taker, not the maker [6](#0-5) [7](#0-6) .

### Impact Explanation
A malicious/unprivileged taker who deploys the destination escrow for a real cross-chain swap can guarantee that the maker's destination payout is never claimable through either withdrawal path, while the taker alone recovers the locked funds after the cancellation timelock via `cancel()`. This breaks the intended atomicity guarantee that the escrow contracts exist to enforce against dishonest resolvers, and fits the "temporary freezing of funds during the live swap lifecycle" bounty tier at minimum (the maker's expected payout is never delivered even though the taker later reclaims their own capital).

### Likelihood Explanation
The precondition only requires an unprivileged actor to call the public `createDstEscrow` function with a crafted `parameters` field and fund the escrow themselves — no admin, governance, or privileged-resolver rights are needed, matching the in-scope attacker model (taker/destination-escrow creator).

### Recommendation
Validate `parameters` at creation time in `createDstEscrow` (e.g., require an exact/expected length such as 128 bytes, or require `protocolFeeAmount == 0` whenever the buffer is too short for the recipient, and vice versa for the integrator fee), or make the recipient-getter gating consistent with the amount-getter gating so a short `parameters` buffer cannot combine with a non-zero fee amount to always revert.

### Proof of Concept
1. Attacker (as taker) calls `createDstEscrow` with `dstImmutables.parameters` set to a 95-byte blob encoding `protocolFeeAmount > 0` in the first word, funding the destination escrow themselves.
2. Warp to `DstWithdrawal`; call `withdraw(secret, immutables)` as the taker → reverts with `IndexOutOfRange` inside `protocolFeeRecipientCd()`.
3. Warp to `DstPublicWithdrawal`; any access-token holder calls `publicWithdraw(secret, immutables)` → same revert, even with the correct secret.
4. Warp to `DstCancellation`; the taker calls `cancel()` and reclaims the full `amount` + `safetyDeposit`, while the maker never receives any destination funds.

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
