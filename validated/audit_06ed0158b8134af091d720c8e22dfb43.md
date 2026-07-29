This confirms `rescueFunds` and `cancel()` both bypass the malformed `parameters` field entirely (only `_withdraw` decodes fees via `integratorFeeAmountCd`/`protocolFeeAmountCd`), so recovery is still possible outside the withdrawal window, meaning the impact is a temporary freeze, not a permanent one.

### Title
Unvalidated `parameters` in `createDstEscrow` lets the destination-escrow funder permanently brick both `withdraw` and `publicWithdraw` on `EscrowDst` - (File: `contracts/BaseEscrowFactory.sol`, `contracts/EscrowDst.sol`)

### Summary
`BaseEscrowFactory.createDstEscrow` accepts the full `IBaseEscrow.Immutables` struct — including the `parameters` field that encodes `protocolFeeAmount`, `integratorFeeAmount`, and the two fee recipients — directly from `msg.sender` calldata with no validation of its length or content. [1](#0-0)  Whoever calls `createDstEscrow` (not necessarily a whitelisted resolver — the caller need only fund `msg.value`/token amount) fully controls this byte blob. If it is left empty, `ImmutablesLib.protocolFeeAmountCd`/`integratorFeeAmountCd` revert with `IndexOutOfRange` because they require at least `0x20`/`0x40` bytes. [2](#0-1)  Alternatively, if `parameters` decodes to fee values whose sum exceeds `immutables.amount`, the subtraction `immutables.amount - integratorFeeAmount - protocolFeeAmount` in `_withdraw` underflows and reverts under Solidity 0.8 checked arithmetic. [3](#0-2) 

### Finding Description
`_withdraw` is the sole internal implementation shared by both `withdraw` (private, `onlyCaller(taker)`) and `publicWithdraw` (`onlyAccessTokenHolder`). [4](#0-3)  Since both entrypoints funnel into the same fee-decoding logic, a malformed `parameters` blob set at escrow-creation time breaks *both* the private and the public withdrawal path identically — there is no fallback finalize mechanism once the withdrawal windows are open. This directly contradicts the stated protocol invariant that the public window exists specifically as a backstop so that funds can still be released even if the taker is unresponsive (as documented for the analogous `EscrowSrc.publicWithdraw`). [5](#0-4) 

Unlike the source-chain path, where `parameters` is programmatically fixed to `""` in `_postInteraction` and the corresponding fee metadata is only emitted informationally in `immutablesComplement` (not consumed by `EscrowSrc`), the destination path relies on `parameters` being decoded on-chain by `EscrowDst`, yet `createDstEscrow` never re-derives or checks it — it simply copies whatever bytes the caller supplies. [6](#0-5) [7](#0-6) 

### Impact Explanation
Once such an escrow is funded and the withdrawal windows are entered, neither `withdraw` nor `publicWithdraw` can ever succeed — every call reverts on fee decoding/arithmetic. This freezes the maker's payout for the entire `DstWithdrawal` + `DstPublicWithdrawal` duration. Recovery is still possible: `cancel()` does not touch `parameters` at all and returns `immutables.amount` to the `taker` after `DstCancellation`, and `rescueFunds` (caller-gated to `taker`, after `RESCUE_DELAY`) also bypasses fee decoding. [8](#0-7) [9](#0-8)  Because recourse exists via cancellation, the impact is **temporary freezing of funds**, matching the High-severity bucket in the bounty scope rather than permanent freeze/insolvency. It is worth noting that in this failure mode funds return to the `taker`, not the `maker` — the maker who was expecting the destination payout receives nothing until the taker (who is also the party that chose the bad `parameters`) triggers cancellation.

### Likelihood Explanation
`createDstEscrow` is a fully public, unprivileged entrypoint with no whitelist gate visible in the reviewed code, and the caller who funds the escrow has complete control over the `parameters` bytes with zero on-chain validation of length or fee-sum consistency against `amount`. [1](#0-0)  This makes the malformed state trivially reachable by any actor who deploys the destination escrow (typically the resolver/taker in normal flow), requiring no special privileges beyond being the one funding the escrow — which is inherent to the intended `createDstEscrow` usage pattern.

### Recommendation
Add validation in `createDstEscrow` (or in `ImmutablesLib`) that:
1. Requires `parameters.length` to be either `0` (no fees) or exactly `0x80` bytes (fully specified fee tuple) — rejecting partial/garbage lengths.
2. Requires `protocolFeeAmount + integratorFeeAmount <= immutables.amount` at creation time, reverting with `InvalidFeeAmounts` (already defined in `BaseEscrowFactory`) if violated, so a malformed escrow can never be deployed/funded in the first place.

### Proof of Concept
1. Call `escrowFactory.createDstEscrow{value: safetyDeposit (+ amount if native)}(immutables, srcCancellationTimestamp)` with `immutables.parameters = ""` (or with `parameters` encoding `protocolFeeAmount + integratorFeeAmount > immutables.amount`).
2. Fund/observe the resulting `EscrowDst` clone holds the correct `amount` + `safetyDeposit` per the factory's balance semantics.
3. Warp past `DstWithdrawal` and call `withdraw(secret, immutables)` as the `taker` — reverts with `ImmutablesLib.IndexOutOfRange` (empty case) or a Solidity arithmetic-underflow panic (fee-overflow case).
4. Warp past `DstPublicWithdrawal`, mint/hold the access token, and call `publicWithdraw(secret, immutables)` — reverts identically, confirming the public backstop is also non-functional.
5. Only `cancel()` after `DstCancellation` succeeds, returning `amount` + `safetyDeposit` to `taker`, not to `maker`.

### Citations

**File:** contracts/BaseEscrowFactory.sol (L127-151)
```text
        IBaseEscrow.Immutables memory immutables = IBaseEscrow.Immutables({
            orderHash: orderHash,
            hashlock: hashlock,
            maker: order.maker,
            taker: Address.wrap(uint160(taker)),
            token: order.makerAsset,
            amount: makingAmount,
            safetyDeposit: extraDataArgs.deposits >> 128,
            timelocks: extraDataArgs.timelocks.setDeployedAt(block.timestamp),
            parameters: "" // Must skip params due only EscrowDst.withdraw() using it.
        });

        DstImmutablesComplement memory immutablesComplement = DstImmutablesComplement({
            maker: order.receiver.get() == address(0) ? order.maker : order.receiver,
            amount: takingAmount,
            token: extraDataArgs.dstToken,
            safetyDeposit: extraDataArgs.deposits & type(uint128).max,
            chainId: extraDataArgs.dstChainId,
            parameters: abi.encode(
                protocolFeeAmount,
                integratorFeeAmount,
                protocolFeeRecipient,
                integratorFeeRecipient
            )
        });
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

**File:** contracts/EscrowDst.sol (L84-93)
```text
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
```

**File:** README.md (L65-77)
```markdown
#### Withdraw tokens
1. `Escrow.withdraw` to withdraw tokens.
2. `Escrow.withdrawTo` to withdraw tokens to the specified address on the source chain.
3. `EscrowDst.publicWithdraw` to withdraw tokens during the public withdrawal period.


#### Cancel escrows
1. `Escrow.cancel` to cancel escrow.
2. `EscrowSrc.publicCancel` to cancel escrow during the public cancellation period.

## Security considerations
The security of protocol transactions is affected by the off-chain distribution of the user's secret. It is recommended to pay proper attention to the implementation of this process.
Resolvers are recommended to watch for the event emitted in `EscrowDst.publicWithdraw` function. If the secret hasn't been received, it can be retrieved from the mentioned event. This will allow the Resolver to withdraw tokens on the source chain before escrow is cancelled.
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
