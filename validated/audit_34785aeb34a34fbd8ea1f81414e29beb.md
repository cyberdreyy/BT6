## Analysis

The external report's root cause is: **an untrusted, attacker-controlled fee-recipient address can revert during a mandatory token transfer inside a shared operation, permanently blocking that operation for everyone**. Searching this repository, the closest analog is in `EscrowDst._withdraw()`, where the `integratorFeeRecipient` and `protocolFeeRecipient` addresses — both fully attacker-controlled data supplied at order-creation time — receive unconditional, non-catchable token transfers *before* the maker receives their swap proceeds.

### Where the analog lives

`EscrowDst._withdraw()` unconditionally transfers to the integrator/protocol fee recipients before paying out the maker: [1](#0-0) 

Both transfers go through `BaseEscrow._uniTransfer`, which for native token uses a raw `call` that reverts the whole transaction on failure, and for ERC20 uses `safeTransfer` (no try/catch): [2](#0-1) 

These recipient addresses originate from `extraData` decoded inside `BaseEscrowFactory._postInteraction`, which is part of the order's `extension` — data that is hashed into `order.salt` and thus signed off-chain by the **maker**, an unprivileged, permissionless party: [3](#0-2) [4](#0-3) 

The test helper confirms these fee-recipient fields are simply packed by whoever constructs the order (i.e., the maker/order-creator), with no on-chain validation that they are EOAs or safe recipients: [5](#0-4) 

### Why this is bounded, not critical

Unlike the pool-keeper analog (which blocks *all* pools' upkeep indefinitely), here `EscrowDst.cancel()` bypasses the fee-transfer path entirely and lets the resolver/taker reclaim their deposited destination tokens and safety deposit once `DstCancellation` is reached: [6](#0-5) 

So a malicious maker who sets a reverting `integratorFeeRecipient`/`protocolFeeRecipient` can make `withdraw()` and `publicWithdraw()` permanently revert (both private and public paths call the same `_withdraw`), but the resolver's escrowed funds are only **frozen until the cancellation timelock**, at which point they are recoverable via `cancel()`. The maker never receives their swap output in this scenario — it is a self-defeating griefing move for the maker, but it does impose a forced, unavoidable temporary freeze on the resolver's capital for the duration of the swap lifecycle, matching the bounty's Medium-tier language on "temporary freezing of funds during the live swap lifecycle."

### Title
Attacker-Controlled Fee Recipient Can Permanently Revert `EscrowDst.withdraw()`, Forcing Cancellation and Temporarily Freezing Resolver Funds — (File: `contracts/EscrowDst.sol`)

### Summary
`EscrowDst._withdraw()` performs unconditional, non-catchable transfers to `integratorFeeRecipient` and `protocolFeeRecipient` before paying the maker. These two addresses are embedded in order `extraData`/`extension` fully controlled and signed by the maker at order-creation time — an unprivileged, permissionless action. If either address is a contract that unconditionally reverts on receiving the fee token/ETH (e.g., a reverting fallback, or an ERC20 with a transfer-hook that reverts), every call to `withdraw()` and `publicWithdraw()` will revert forever, since the fee transfer happens unconditionally whenever the corresponding fee amount is non-zero.

### Finding Description
`_withdraw` in `EscrowDst.sol` calls `_uniTransfer` for `integratorFeeAmount` and `protocolFeeAmount` (when non-zero) before transferring the remaining amount to the maker. `_uniTransfer` in `BaseEscrow.sol` uses `safeTransfer` for ERC20 or a raw `.call{value:}` for native token, neither of which tolerates a reverting recipient — the whole transaction, including the maker payout and safety-deposit refund to the caller, reverts. Because these recipient addresses and non-zero fee percentages are supplied in the order's `extension`/`extraData`, which is hashed into `order.salt` and thus signed entirely by the maker (`BaseEscrowFactory._postInteraction`), a malicious maker can pre-select a fee recipient contract engineered to always revert. Both the private `withdraw()` (caller-gated to taker) and the public `publicWithdraw()` (open to any access-token holder) call the same reverting `_withdraw` internal function, so no caller can ever complete a normal withdrawal for that specific escrow instance.

### Impact Explanation
This does not enable direct theft, but it forces every withdrawal attempt on the affected `EscrowDst` instance to fail unconditionally, regardless of who calls it or when (private or public window). The resolver's deposited destination-chain tokens and safety deposit remain locked in the escrow until the `DstCancellation` timelock elapses, at which point `cancel()` (which does not touch the fee-recipient path) allows recovery. This matches the "temporary freezing of funds during the live swap lifecycle" Medium-tier impact: an unprivileged actor (the maker) can force a resolver's capital to sit frozen for the duration of the timelock window on every swap they orchestrate this way, without any on-chain safeguard preventing it.

### Likelihood Explanation
Likelihood is limited by rational-actor incentives: the maker who poisons their own order's fee recipients also forfeits their own swap proceeds (they never receive the destination amount either), so this is a pure griefing move with no direct profit for the attacker beyond denying resolver liquidity/time. It requires no privileged role, only the ability to construct and sign a normal order — fully within the unprivileged attacker model.

### Recommendation
Do not let a reverting fee-recipient transfer block payout of the primary maker funds. Options: (1) wrap `_uniTransfer` calls to the fee recipients in a try/catch (or low-level call check) that does not propagate failure, redirecting failed fee amounts to a pull-based claim mechanism; (2) perform the maker's principal transfer before attempting fee transfers, and isolate fee-transfer failures so they cannot revert the overall withdrawal; (3) validate that fee-amount transfers cannot brick the entire `_withdraw` flow, consistent with the original report's recommendation to avoid unconditional "push" transfers to externally-controlled addresses in favor of a separate claim step.

### Proof of Concept
1. Maker constructs an order whose `extension`/postInteraction `extraData` sets `integratorFeeRecipient` (or `protocolFeeRecipient`) to the address of a contract with a `receive()`/fallback that always reverts (for native destination token) or a token that reverts on `transfer` to that address, and sets a non-zero `integratorFee`/`protocolFee` percentage.
2. Maker signs and publishes the order; a resolver fills it, triggering `createDstEscrow`, funding `EscrowDst` with the destination amount and safety deposit as usual (see `test/integration/ResolverMock.t.sol` `test_MockWithdrawDst` flow for the equivalent happy path at [7](#0-6) ).
3. Once the secret is revealed and `dstTimelocks.withdrawal` has passed, any caller invokes `withdraw()`/`publicWithdraw()`; the transaction reverts inside `_uniTransfer` at the fee-recipient call in `EscrowDst._withdraw` (contracts/EscrowDst.sol:86-91), for every subsequent attempt by any caller during both private and public withdrawal windows.
4. The resolver's escrowed destination tokens and safety deposit remain frozen until `dstTimelocks.cancellation`, when the resolver must call `cancel()` to recover them, and the maker never receives the swap output.

### Citations

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

**File:** contracts/BaseEscrowFactory.sol (L67-90)
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
```

**File:** contracts/BaseEscrowFactory.sol (L139-150)
```text
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

**File:** test/utils/libraries/CrossChainTestLib.sol (L361-372)
```text
            bytes memory postInteractionData = abi.encodePacked(
                factory,
                bytes20(address(orderDetails.integratorFeeRecipient)), // integrator fee recipient
                bytes20(address(orderDetails.protocolFeeRecipient)), // protocol fee recipient
                bytes2(orderDetails.integratorFee),  // integrator fee percentage (in 1e5)
                bytes1(orderDetails.integratorShare), // integrator rev share percentage (in 1e2)
                bytes2(orderDetails.protocolFee), // resolver fee percentage (in 1e5)
                bytes1(orderDetails.whitelistDiscountNumerator), // whitelist discount numerator (in 1e2)
                whitelist,  // struct (4 bytes | 1 byte | (bytes12)[N] )
                orderDetails.customDataForPostInteraction,
                swapData.extraData
            );
```

**File:** test/integration/ResolverMock.t.sol (L305-324)
```text
    function test_MockWithdrawDst() public {
        (IBaseEscrow.Immutables memory immutables,
        uint256 srcCancellationTimestamp,
        IEscrowDst dstClone
        ) = _prepareDataDst();

        address[] memory targets = new address[](1);
        bytes[] memory arguments = new bytes[](1);
        targets[0] = address(dai);
        arguments[0] = abi.encodePacked(dai.approve.selector, abi.encode(address(escrowFactory), type(uint256).max));

        assertEq(dai.balanceOf(address(dstClone)), 0);
        assertEq(address(dstClone).balance, 0);

        // Approve DAI to escrowFactory
        IResolverExample(resolverMock).arbitraryCalls(targets, arguments);
        IResolverExample(resolverMock).deployDst{ value: DST_SAFETY_DEPOSIT }(immutables, srcCancellationTimestamp);

        assertEq(dai.balanceOf(address(dstClone)), TAKING_AMOUNT);
        assertEq(address(dstClone).balance, DST_SAFETY_DEPOSIT);
```
