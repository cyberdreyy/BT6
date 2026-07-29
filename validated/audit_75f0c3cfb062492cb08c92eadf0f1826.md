Confirmed: the fee payout only exists in `EscrowDst._withdraw`; `EscrowSrc` never sends funds to fee recipients (only to `target`/`maker` and `msg.sender`), so the analog is isolated to `EscrowDst.sol`.

### Title
Malicious integrator/protocol fee recipient can permanently DOS native-token destination withdrawals - (File: `contracts/EscrowDst.sol`)

### Summary
`EscrowDst._withdraw` pushes the integrator fee and protocol fee directly to `integratorFeeRecipientCd()` and `protocolFeeRecipientCd()` before paying the maker, exactly mirroring the M-6 pattern (push-payment to untrusted fee recipients blocking the whole flow). When the destination asset is native ETH, this push uses a reverting low-level `call`, so any fee recipient that is a contract designed to reject ETH will permanently block withdrawal, cancel, and public-withdraw for that escrow.

### Finding Description
In `EscrowDst._withdraw` [1](#0-0) , the integrator and protocol fee amounts are transferred to `immutables.integratorFeeRecipientCd().get()` and `immutables.protocolFeeRecipientCd().get()` via `_uniTransfer` before the remaining amount goes to the maker. `_uniTransfer` routes native-token transfers through `_ethTransfer`, which reverts the entire call if the low-level `call` to the recipient fails: [2](#0-1) .

The integrator fee recipient and protocol fee recipient addresses are taken verbatim from `extraData` supplied at order-fill/postInteraction time, with no restriction that they be a trusted, non-reverting address: [3](#0-2) . These addresses are then baked into the `DstImmutablesComplement.parameters` and ultimately into `EscrowDst`'s immutables used at withdrawal time [4](#0-3) .

Because `withdraw`, `publicWithdraw`, and `_withdraw` all funnel through the same `_withdraw` internal function that unconditionally pays the fee recipients first [5](#0-4) , a fee recipient contract that reverts on `receive()` blocks every withdrawal path for that specific escrow (both the private `taker`-only path and the public, access-token-gated path), for as long as the timelocked withdrawal window is open. The existing test suite already demonstrates that a reverting recipient of a native transfer causes `NativeTokenSendingFailure` to bubble up and revert the whole `withdraw`/`publicWithdraw` call [6](#0-5) , confirming the exact mechanics needed for this DOS, only the target here is the fee recipient rather than the safety-deposit caller.

This is a direct analog to the referenced Sherlock M-6 finding: an unprivileged, semi-trusted party ("integrator") supplies an address that is paid via push-transfer mid-flow, and that party can grief the entire settlement, harming the innocent maker (and the resolver/caller who cannot complete withdrawal) rather than just themselves.

### Impact Explanation
While the destination cancellation path (`EscrowDst.cancel`) does not touch fee recipients and remains callable after `DstCancellation`, refunding `immutables.amount` to the `taker` and the safety deposit to the canceller [7](#0-6) , this means the maker's expected destination payout is withheld for the entire withdrawal window and then redirected back to the taker/resolver instead of the maker once cancellation triggers. This is a temporary freezing of the maker's swap proceeds during the live swap lifecycle, and functionally causes the maker to never receive the funds they were owed for revealing the secret — the same "innocent party" fund-flow disruption cited in the M-6 report, falling under the bounty's High-severity bucket ("temporary freezing of funds during the live swap lifecycle").

### Likelihood Explanation
Any actor able to influence the `integratorFeeRecipient`/`protocolFeeRecipient` bytes embedded in the order's `postInteraction` extraData (an unprivileged integrator role, not owner/governance) can trivially deploy a reverting fallback contract and set it as the fee recipient for native-asset (`token == address(0)`) destination swaps. No special privileges, race conditions, or timing constraints are needed — the contract only needs to reject `receive()`/fallback calls, and every subsequent withdrawal attempt on that escrow will revert deterministically.

### Recommendation
Adopt a pull-payment pattern for `integratorFeeRecipient` and `protocolFeeRecipient` on `EscrowDst`: credit fee balances internally and let recipients claim them separately, or wrap each fee push in a try/catch (or a low-gas-limited "best effort" send with fallback to an internally tracked claimable balance) so a reverting recipient cannot block the maker's principal payout and the safety-deposit refund to the withdrawing caller.

### Proof of Concept
1. Order is created/filled with `extraData` (or the order's extension) setting `integratorFeeRecipient` to a deployed `Reverter` contract whose `receive()`/`fallback()` always reverts, and `dstToken == address(0)` (native ETH), following `BaseEscrowFactory._postInteraction` [8](#0-7) .
2. Resolver calls `EscrowFactory.createDstEscrow` and funds the `EscrowDst` clone with native ETH covering `amount + safetyDeposit`.
3. After `DstWithdrawal` timelock, taker calls `EscrowDst.withdraw(secret, immutables)`; `_withdraw` attempts `_uniTransfer(address(0), Reverter, integratorFeeAmount)` → `_ethTransfer` → `call` fails → `NativeTokenSendingFailure()` reverts the whole transaction, matching the guard pattern already exercised in `test_NoFailedNativeTokenTransferWithdrawalDstNative` [9](#0-8)  but with the reverting party being the fee recipient instead of the maker.
4. `publicWithdraw` fails identically once the public window opens, since it calls the same `_withdraw` internal function.
5. Only `cancel()` after `DstCancellation` succeeds, returning the destination funds to the `taker` instead of the `maker`, and the maker permanently loses the destination-side proceeds of the swap.

### Citations

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

**File:** contracts/BaseEscrow.sol (L84-98)
```text
    function _uniTransfer(address token, address to, uint256 amount) internal {
        if (token == address(0)) {
            _ethTransfer(to, amount);
        } else {
            IERC20(token).safeTransfer(to, amount);
        }
    }

    /**
     * @dev Transfers native tokens to the recipient.
     */
    function _ethTransfer(address to, uint256 amount) internal {
        (bool success,) = to.call{ value: amount }("");
        if (!success) revert NativeTokenSendingFailure();
    }
```

**File:** contracts/BaseEscrowFactory.sol (L67-150)
```text
    function _postInteraction(
        IOrderMixin.Order calldata order,
        bytes calldata extension,
        bytes32 orderHash,
        address taker,
        uint256 makingAmount,
        uint256 takingAmount,
        uint256 remainingMakingAmount,
        bytes calldata extraData
    ) internal override(FeeTaker) {
        address integratorFeeRecipient = address(bytes20(extraData[:20]));
        address protocolFeeRecipient = address(bytes20(extraData[20:40]));

        extraData = extraData[40:];

        uint256 superArgsLength = extraData.length - SRC_IMMUTABLES_LENGTH;

        (uint256 integratorFeeAmount, uint256 protocolFeeAmount, bytes calldata tail) = FeeTaker._getFeeAmounts(
            order,
            taker,
            takingAmount,
            makingAmount,
            extraData[:superArgsLength]
        );

        if (integratorFeeAmount + protocolFeeAmount >= takingAmount) revert InvalidFeeAmounts();

        if (tail.length > 19) {
            IPostInteraction(address(bytes20(tail))).postInteraction(
                order,
                extension,
                orderHash,
                taker,
                makingAmount,
                takingAmount,
                remainingMakingAmount,
                tail[20:]
            );
        }

        ExtraDataArgs calldata extraDataArgs;
        assembly ("memory-safe") {
            extraDataArgs := add(extraData.offset, superArgsLength)
        }

        bytes32 hashlock;

        if (MakerTraitsLib.allowMultipleFills(order.makerTraits)) {
            uint256 partsAmount = uint256(extraDataArgs.hashlockInfo) >> 240;
            if (partsAmount < 2) revert InvalidSecretsAmount();
            bytes32 key = keccak256(abi.encodePacked(orderHash, uint240(uint256(extraDataArgs.hashlockInfo))));
            ValidationData memory validated = lastValidated[key];
            hashlock = validated.leaf;
            if (!_isValidPartialFill(makingAmount, remainingMakingAmount, order.makingAmount, partsAmount, validated.index)) {
                revert InvalidPartialFill();
            }
        } else {
            hashlock = extraDataArgs.hashlockInfo;
        }

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
```

**File:** test/unit/Escrow.t.sol (L747-760)
```text
    function test_NoFailedNativeTokenTransferWithdrawalDst() public {
        (IBaseEscrow.Immutables memory immutables, uint256 srcCancellationTimestamp, IEscrowDst dstClone) = _prepareDataDst();

        // deploy escrow
        vm.prank(bob.addr);
        escrowFactory.createDstEscrow{ value: DST_SAFETY_DEPOSIT }(immutables, srcCancellationTimestamp);

        // withdraw
        vm.warp(block.timestamp + dstTimelocks.publicWithdrawal + 10);
        accessToken.mint(address(nativeTokenRejector), 1);
        vm.prank(address(nativeTokenRejector));
        vm.expectRevert(IBaseEscrow.NativeTokenSendingFailure.selector);
        dstClone.publicWithdraw(SECRET, immutables);
    }
```

**File:** test/unit/Escrow.t.sol (L762-780)
```text
    function test_NoFailedNativeTokenTransferWithdrawalDstNative() public {
        (IBaseEscrow.Immutables memory immutables, uint256 srcCancellationTimestamp, IEscrowDst dstClone) = _prepareDataDstCustom(
            HASHED_SECRET,
            TAKING_AMOUNT,
            address(nativeTokenRejector),
            bob.addr, address(0x00),
            DST_SAFETY_DEPOSIT,
            PROTOCOL_FEE,
            INTEGRATOR_FEE,
            INTEGRATOR_SHARES,
            WHITELIST_PROTOCOL_FEE_DISCOUNT,
            true
        );

        // deploy escrow
        vm.startPrank(bob.addr);
        escrowFactory.createDstEscrow{ value: DST_SAFETY_DEPOSIT + TAKING_AMOUNT }(immutables, srcCancellationTimestamp);

        uint256 balanceBob = bob.addr.balance;
```
