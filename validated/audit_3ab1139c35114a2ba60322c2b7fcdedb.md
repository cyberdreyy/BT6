### Title
Malicious `maker` can permanently DoS `EscrowDst.withdraw`/`publicWithdraw` when the destination asset is native ETH, freezing the resolver's escrowed funds - (File: `contracts/EscrowDst.sol`)

### Summary
`EscrowDst._withdraw` unconditionally pushes the destination `amount` to `immutables.maker` via a low-level ETH `call` and reverts the whole transaction (including the safety-deposit payout to the caller) if that call fails. Since `immutables.maker` is a value freely supplied by the swap-initiating (unprivileged) party and never validated to be able to accept ETH, that same maker can deploy a contract as its own receiving address whose `receive()`/`fallback()` deterministically reverts, permanently blocking every `withdraw` and `publicWithdraw` call on the destination escrow for as long as the withdrawal windows last.

### Finding Description
`EscrowDst._withdraw` sends fee amounts and then the net `amount` to `immutables.maker.get()` through `_uniTransfer` → `_ethTransfer` when the destination token is the native asset (`token == address(0)`): [1](#0-0) 

`_ethTransfer` reverts the entire call if the recipient rejects the ETH: [2](#0-1) 

Both `withdraw` (caller-restricted to `taker`) and `publicWithdraw` (open to any access-token holder) funnel into this same `_withdraw` function, so there is no alternate successful path to complete a withdrawal while `maker` keeps rejecting ETH: [3](#0-2) 

Crucially, `immutables.maker` for the destination escrow is attacker-influenceable data supplied directly to `createDstEscrow` calldata (and mirrored from `order.receiver`/`order.maker` off-chain, with no on-chain enforcement of "must accept ETH"): [4](#0-3) [5](#0-4) 

This is the exact analog of the reported bug class: an unprivileged party (`lien.borrower` in the original report, `maker` here) controls a contract address that conditionally/permanently reverts on `receive()`, and the protocol's payout function (`auctionBuyNft`/`withdrawEthWithInterest` in the original, `withdraw`/`publicWithdraw` here) unconditionally attempts a push-transfer to that address, causing the whole state-mutating transaction (including unrelated safety-deposit payout to the caller) to revert.

The repository's own test suite already demonstrates this exact failure mode, confirming the code path is reachable and behaves as described: [6](#0-5) 

### Impact Explanation
While `maker` refuses ETH, neither the `taker` (private withdrawal) nor any access-token holder (public withdrawal) can withdraw the destination escrow. This freezes the resolver's escrowed native ETH (and the safety deposit) for the entire withdrawal + public-withdrawal window of the swap lifecycle. Recovery is only possible once the `DstCancellation` timelock elapses and `taker` calls `cancel()`, which returns the funds to `taker` instead of `maker` and does not call into `maker` at all. This matches the "temporary freezing of funds during the live swap lifecycle" High-severity impact defined in the bounty scope: the resolver's capital is locked and unusable for the full duration of the withdrawal window, purely due to an unprivileged party's choice of a reverting `maker` address, with no cost to that party.

### Likelihood Explanation
Likelihood is high: the attacker only needs to set their own receiving address (as `maker`/`receiver`) to a trivial contract that always reverts on receiving ETH, and only needs the destination token of the swap to be native ETH — a normal, supported configuration (`token.get() == address(0)` is explicitly handled). No privileged role, timing race, or cooperation from the resolver is required; the "attack" is simply choosing an adversarial `maker` contract before agreeing to the swap.

### Recommendation
Adopt the mitigation pattern from the original report: never let an untrusted recipient's ability to revert block critical state transitions or unrelated payouts (like the safety deposit to the withdrawal caller). Specifically:
- Decouple the safety-deposit payout to `msg.sender` from the maker payout, e.g. perform the maker's ETH transfer last, or via a pull-based mechanism (credit `maker` a claimable balance instead of push-transferring), so a reverting `maker` cannot block `withdraw`/`publicWithdraw` or the caller's own safety-deposit incentive.
- Alternatively, wrap the maker-facing native transfer in a bounded-gas, non-reverting call (e.g. record failed transfers into a withdrawable balance) rather than reverting the whole function on failure.

### Proof of Concept
1. Maker (an unprivileged user) deploys `NativeTokenRejector`, a contract whose `receive()` always reverts, and uses its address as `maker`/`order.receiver` when constructing the swap order.
2. Resolver (taker) fills the order on the source chain and calls `createDstEscrow` on the destination chain with `dstImmutables.token == address(0)` and `dstImmutables.maker == address(NativeTokenRejector)`, funding the escrow with `amount + safetyDeposit` in native ETH (`contracts/BaseEscrowFactory.sol` lines 165-185).
3. Once the withdrawal window opens, taker calls `EscrowDst.withdraw(secret, immutables)` (or any access-token holder calls `publicWithdraw`). The internal `_uniTransfer`/`_ethTransfer` call to `maker` reverts, causing `NativeTokenSendingFailure` and reverting the whole transaction — exactly reproduced by the existing test `test_NoFailedNativeTokenTransferWithdrawalDstNative` (`test/unit/Escrow.t.sol` lines 762-789).
4. This repeats for every withdrawal attempt during both the private and public withdrawal periods, freezing the resolver's escrowed ETH and safety deposit until the `DstCancellation` timelock is reached and `cancel()` can be called instead.

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

**File:** contracts/BaseEscrow.sol (L92-98)
```text
    /**
     * @dev Transfers native tokens to the recipient.
     */
    function _ethTransfer(address to, uint256 amount) internal {
        (bool success,) = to.call{ value: amount }("");
        if (!success) revert NativeTokenSendingFailure();
    }
```

**File:** contracts/BaseEscrowFactory.sol (L139-151)
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

**File:** test/unit/Escrow.t.sol (L762-789)
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
        uint256 balanceEscrow = address(dstClone).balance;

        // withdraw
        vm.warp(block.timestamp + dstTimelocks.withdrawal + 10);
        vm.expectRevert(IBaseEscrow.NativeTokenSendingFailure.selector);
        dstClone.withdraw(SECRET, immutables);
        assertEq(bob.addr.balance, balanceBob);
        assertEq(address(dstClone).balance, balanceEscrow);
    }
```
