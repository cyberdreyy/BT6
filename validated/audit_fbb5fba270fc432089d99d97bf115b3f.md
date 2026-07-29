### Title
Unvalidated `parameters` field in `createDstEscrow` lets any caller zero out protocol/integrator fees - (File: `contracts/BaseEscrowFactory.sol`)

### Summary
`BaseEscrowFactory.createDstEscrow` accepts a fully caller-supplied `IBaseEscrow.Immutables` struct, including the `parameters` bytes field that encodes `protocolFeeAmount`, `integratorFeeAmount`, `protocolFeeRecipient`, and `integratorFeeRecipient` (as read by `ImmutablesLib.protocolFeeAmountCd`/`integratorFeeAmountCd`). Unlike the source-chain path (`_postInteraction`), which computes these fee amounts from the order/extension data and enforces `integratorFeeAmount + protocolFeeAmount < takingAmount`, the destination path performs no computation and no validation at all on these values. Any unprivileged address can call `createDstEscrow` with `parameters` set to zero fee amounts, permanently denying the protocol and integrator their fee for that swap while the escrow still functions normally for the maker/taker.

### Finding Description
On the source chain, fee amounts are derived on-chain and checked: [1](#0-0) 

These values are embedded only in the `DstImmutablesComplement.parameters` emitted in the `SrcEscrowCreated` event, for off-chain reconstruction: [2](#0-1) 

However, `createDstEscrow` is a public, unauthenticated function that takes an entirely caller-supplied `dstImmutables` struct and never recomputes or cross-checks `parameters` against anything the protocol computed on-chain: [3](#0-2) 

The `parameters` bytes are opaque to the factory - it never decodes or validates them; it only requires `msg.value`/token transfer to match `dstImmutables.amount` and `dstImmutables.safetyDeposit`. The fee amounts are read directly from this attacker-supplied blob only later, during `EscrowDst._withdraw`: [4](#0-3) [5](#0-4) 

`ImmutablesLib.protocolFeeAmountCd`/`integratorFeeAmountCd` simply decode whatever bytes were stored in `immutables.parameters` at deployment time - there is no invariant tying these numbers to the fee amounts that were actually computed and emitted on the source chain: [6](#0-5) 

Because `hash()`/`hashMem()` only guarantee that whatever `parameters` was supplied at `createDstEscrow` time is the same one used at `withdraw`/`cancel` time (self-consistency), and never validate it against the source-chain-committed `immutablesComplement.parameters`, any address (not just a legitimate resolver) can call `createDstEscrow` for a real order with `parameters` fee fields set to `0` and legitimate-looking `protocolFeeRecipient`/`integratorFeeRecipient` addresses. The escrow deploys successfully, the maker's off-chain secret-release logic typically validates `amount`, `token`, `hashlock`, `timelocks`, and `safetyDeposit` (the fields the README emphasizes must "match"), but the `parameters` fee-split field is a newer addition that is not part of the well-known match-checklist and can be zeroed without visibly breaking the swap for the maker (the maker actually receives more, since no fee is deducted). Once the maker reveals the secret and the escrow is withdrawn, the protocol/integrator fee for that swap is permanently unpaid — the value simply reverts to the maker.

### Impact Explanation
This is a permanent loss of unclaimed fee-like value: the protocol and integrator fee recipients configured off-chain for a given order never receive their fee for a specific dst-side settlement, and this cannot be recovered after `_withdraw` executes. This matches the bounty's High-severity category: "theft or permanent loss of unclaimed fee-like value." The attack requires no privileged role — `createDstEscrow` has no access control, matching the required unprivileged-user attacker model (destination escrow creation path from the Smart Audit Pivots).

### Likelihood Explanation
Likelihood is high for any actor willing to fund the destination escrow themselves (which a resolver/taker naturally does as part of the normal swap flow) since the only "control" preventing fee-stripping is off-chain diligence checking `dstImmutables.parameters` against the `SrcEscrowCreated` event — a check that is easy to omit because `parameters` is not part of the "obviously must-match" fields (`amount`, `token`, `hashlock`, `safetyDeposit`, `timelocks`) called out in the project's own documentation.

### Recommendation
Do not allow arbitrary caller-supplied `parameters` for fee accounting in `createDstEscrow`. Either:
1. Require the caller to also supply `orderHash` plus a signed/committed fee record from the src-chain `SrcEscrowCreated` event, and validate `dstImmutables.parameters` against it before deploying the dst clone, or
2. Recompute/validate protocol and integrator fee amounts on-chain in `createDstEscrow` (e.g., bound them to a known max determined by the order/extension configuration) instead of trusting the raw `parameters` bytes supplied by `msg.sender`.

### Proof of Concept
1. A maker signs an order on the source chain; the resolver fills it via the Limit Order Protocol, triggering `_postInteraction`, which computes and emits `protocolFeeAmount`/`integratorFeeAmount` in `SrcEscrowCreated`'s `immutablesComplement.parameters`.
2. Any address (does not need to be the resolver who filled the source order) observes the order details (hashlock, amount, token, taker, timelocks - all public in the event) and calls `EscrowFactory.createDstEscrow` supplying the correct `amount`, `token`, `hashlock`, `safetyDeposit`, and `timelocks`, but sets `parameters = abi.encode(0, 0, protocolFeeRecipient, integratorFeeRecipient)` instead of the real fee amounts.
3. The factory funds/deploys the escrow with no check on `parameters`; the escrow deploys successfully, its balance requirements are satisfied by `dstImmutables.amount`.
4. Once the maker's secret is revealed and `EscrowDst.withdraw` is called, `_withdraw` reads `integratorFeeAmountCd()`/`protocolFeeAmountCd()` as `0`, skips both fee transfers, and sends the entire `immutables.amount` to the maker — the protocol/integrator fee for this swap is permanently forfeited.

### Citations

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
