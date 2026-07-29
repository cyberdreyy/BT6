## Finding

The bug-class analog exists in `contracts/BaseEscrowFactory.sol`'s `createDstEscrow` function, which lacks the fee-sum validation that its sibling code path (`_postInteraction`) explicitly enforces.

### Title
Missing fee-amount bound check in `createDstEscrow` causes arithmetic underflow / permanent withdraw DoS in `EscrowDst._withdraw` - (File: contracts/BaseEscrowFactory.sol)

### Summary
`EscrowDst._withdraw` computes `immutables.amount - integratorFeeAmount - protocolFeeAmount` without any bound check [1](#0-0) . The only place that validates `integratorFeeAmount + protocolFeeAmount < takingAmount` is `BaseEscrowFactory._postInteraction`, which reverts with `InvalidFeeAmounts` when the LOP-driven src-escrow flow is used [2](#0-1) . However, `createDstEscrow` is a separate, directly callable entry point that accepts arbitrary caller-supplied `dstImmutables` (including the `parameters` blob encoding `protocolFeeAmount`/`integratorFeeAmount`) and performs no equivalent check before deploying the destination escrow [3](#0-2) .

### Finding Description
Any unprivileged caller invoking `createDstEscrow` can supply `dstImmutables.parameters` such that `protocolFeeAmount + integratorFeeAmount > immutables.amount`. The factory only checks `msg.value == nativeAmount` (safety deposit + optional native amount) and the cancellation-timestamp ordering — it never validates the fee fields against `amount` [4](#0-3) . These fee values are later read back via `ImmutablesLib.integratorFeeAmountCd`/`protocolFeeAmountCd` inside `EscrowDst._withdraw`, which subtracts them from `immutables.amount` with no `require`/bound check: `uint256 amount = immutables.amount - integratorFeeAmount - protocolFeeAmount;` [1](#0-0) . Because Solidity ^0.8 reverts on underflow, every call to `withdraw`/`publicWithdraw` on that escrow permanently reverts.

This exactly mirrors the report's root cause: a value that is bounded/validated in one code path (the `updatePerformanceFee`/`_postInteraction` fee check) is not bounded in a downstream consumer (`lendingAPR`/`EscrowDst._withdraw`), producing an unconditional underflow revert.

### Impact Explanation
Once such a malformed dst escrow is deployed and funded, `withdraw`/`publicWithdraw` can never succeed (permanent revert), while `cancel` still transfers the full `immutables.amount` back to the `taker` after the cancellation timelock [5](#0-4) . This matches the Medium bounty tier: "smart contract unable to operate because required token/native balances can be broken by an unprivileged actor" — the escrow's core withdraw functionality is permanently broken by attacker-supplied constructor-time data, with no privileged action involved.

### Likelihood Explanation
`createDstEscrow` is a public, unauthenticated entry point taking fully attacker-controlled `Immutables` (including the opaque `parameters` bytes) [6](#0-5) . No cross-check exists linking these parameters back to the `DstImmutablesComplement` emitted by `_postInteraction` on the source chain [7](#0-6) , so nothing on-chain prevents a caller from directly invoking `createDstEscrow` with inconsistent fee data.

### Recommendation
Add the same `integratorFeeAmount + protocolFeeAmount < immutables.amount` (or `revert InvalidFeeAmounts()`) check inside `createDstEscrow` before deploying the escrow, mirroring the guard already present in `_postInteraction` [8](#0-7) .

### Proof of Concept
1. Attacker (any unprivileged caller) builds `dstImmutables` with `amount = 100`, and `parameters = abi.encode(protocolFeeAmount = 60, integratorFeeAmount = 60, protocolFeeRecipient, integratorFeeRecipient)` (sum 120 > 100).
2. Attacker calls `createDstEscrow(dstImmutables, srcCancellationTimestamp)` sending `msg.value = safetyDeposit` (+ `amount` if native) — the call succeeds because no fee-sum check exists [3](#0-2) .
3. After the withdrawal window opens, any call to `withdraw`/`publicWithdraw` on the deployed `EscrowDst` clone reverts on `immutables.amount - integratorFeeAmount - protocolFeeAmount` due to underflow [9](#0-8) .
4. Only `cancel()` (post-timelock, callable by `taker`) can retrieve funds, sending the full `amount` back to `taker` instead of splitting fees to recipients — the withdraw path is permanently unusable.

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

**File:** contracts/EscrowDst.sol (L79-93)
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
```

**File:** contracts/BaseEscrowFactory.sol (L84-92)
```text
        (uint256 integratorFeeAmount, uint256 protocolFeeAmount, bytes calldata tail) = FeeTaker._getFeeAmounts(
            order,
            taker,
            takingAmount,
            makingAmount,
            extraData[:superArgsLength]
        );

        if (integratorFeeAmount + protocolFeeAmount >= takingAmount) revert InvalidFeeAmounts();
```

**File:** contracts/BaseEscrowFactory.sol (L139-153)
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

        emit SrcEscrowCreated(immutables, immutablesComplement);
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
