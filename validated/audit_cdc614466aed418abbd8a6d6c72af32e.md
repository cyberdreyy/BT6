### Title
Missing fee-sum validation lets anyone create a `EscrowDst` whose stated protocol/integrator fees exceed the escrowed amount, causing `withdraw()` to permanently revert (destination funds locked/DoS) - (File: `contracts/BaseEscrowFactory.sol`, `contracts/EscrowDst.sol`)

### Summary
`BaseEscrowFactory.createDstEscrow` lets **any unprivileged caller** supply arbitrary `Immutables.parameters` (which encode `protocolFeeAmount`, `integratorFeeAmount`, and their recipients) with **no validation** that `protocolFeeAmount + integratorFeeAmount < amount`. [1](#0-0)  Compare this to the source-side path, `_postInteraction`, which explicitly guards against this exact condition: `if (integratorFeeAmount + protocolFeeAmount >= takingAmount) revert InvalidFeeAmounts();`. [2](#0-1)  No equivalent check exists on the destination path, either in `createDstEscrow` or in `EscrowDst._withdraw`, which performs `uint256 amount = immutables.amount - integratorFeeAmount - protocolFeeAmount;` [3](#0-2)  with unchecked arithmetic (Solidity 0.8 checked math, so it reverts on underflow) and no cap comparable to `InvalidFeeAmounts`.

### Finding Description
- `createDstEscrow` accepts `dstImmutables` fully as calldata from `msg.sender`, with only two checks: that `msg.value` equals the expected native amount, and that the destination cancellation timelock doesn't exceed `srcCancellationTimestamp`. [1](#0-0) 
- The `parameters` field (fee amounts/recipients) inside `Immutables` is opaque to the factory — it is never parsed, summed, or cross-checked against `immutables.amount`. It is only decoded later, inside `EscrowDst._withdraw`, via `ImmutablesLib`. [4](#0-3) 
- `_withdraw` computes `amount = immutables.amount - integratorFeeAmount - protocolFeeAmount` and only afterward attempts `_uniTransfer(..., maker, amount)`. [5](#0-4)  If `integratorFeeAmount + protocolFeeAmount > immutables.amount`, this subtraction underflows and reverts unconditionally for every call path that reaches `_withdraw` (`withdraw` and `publicWithdraw`), because `immutables` must match the create-time hash via `onlyValidImmutables`, so the bad fee values are permanently baked into the deployed escrow's identity. [6](#0-5) 
- Since anyone can call `createDstEscrow` (no whitelist/access-token gate on this function, unlike the source-side fill flow which is gated through `FeeTaker`/whitelist checks), an unprivileged actor can deploy a destination escrow at the deterministic address expected for a given hashlock/order with deliberately malformed `parameters`, funding it correctly (so balances look fine) but with fee amounts that sum to more than `amount`.

### Impact Explanation
Once such a malformed `EscrowDst` clone is deployed and funded, `withdraw`/`publicWithdraw` will always revert due to the underflow — the maker can never receive the destination-chain payment, and the resolver/taker's safety deposit and funds become stuck in that escrow (only reachable via `cancel`, which returns the whole `amount` back to `taker`, not `maker`). This breaks the required balance/operational invariant of the destination-side escrow using only public parameters (no privileged access needed), matching "Medium: smart contract unable to operate because required token/native balances can be broken by an unprivileged actor" from the bounty scope. It also blocks the swap lifecycle mid-flight (temporary/permanent freezing of the taker's own escrowed funds and the maker's expected payment) during the live swap.

### Likelihood Explanation
Likelihood is high for the DoS variant: it requires only calling a fully public function (`createDstEscrow`) with hand-crafted `Immutables.parameters`, and there's no on-chain cross-check between the fee values chosen here and any signed/committed value from the source-chain order (contrast with the source-side check present in `_postInteraction`). The asymmetry between the source-side guard (`InvalidFeeAmounts`) and the complete absence of an equivalent guard on the destination side is a clear, reachable gap in the production contracts, not a theoretical concern.

### Recommendation
Add a check in `createDstEscrow` (or in `EscrowDst._withdraw`) enforcing `integratorFeeAmount + protocolFeeAmount < immutables.amount`, mirroring the `InvalidFeeAmounts` guard already used in `_postInteraction`, before allowing the escrow to be created/deployed with attacker-supplied `parameters`.

### Proof of Concept
1. Attacker computes a valid `hashlock` (or reuses a public one from an in-flight order) and constructs `Immutables` with `amount = A`, and `parameters = abi.encode(protocolFeeAmount = A, integratorFeeAmount = A, protocolFeeRecipient, integratorFeeRecipient)` — i.e., fees summing to `2A > A`.
2. Attacker calls `createDstEscrow(immutables, srcCancellationTimestamp)` with `msg.value` and ERC20 approval matching `A`, so the checks in `createDstEscrow` pass and the clone is deployed and funded normally. [1](#0-0) 
3. When `withdraw`/`publicWithdraw` is later called with the exact same immutables (required by `onlyValidImmutables`), `_withdraw` executes `immutables.amount - integratorFeeAmount - protocolFeeAmount` which underflows and reverts, permanently DoS-ing withdrawal for that escrow. [5](#0-4)

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

**File:** contracts/libraries/ImmutablesLib.sol (L24-43)
```text
    function protocolFeeAmount(IBaseEscrow.Immutables memory immutables) internal pure returns (uint256 ret) {
        bytes memory parameters = immutables.parameters;
        if (parameters.length < 0x20) revert IndexOutOfRange();
        assembly ("memory-safe") {
            ret := mload(add(parameters, 0x20))
        }
    }

    /**
     * @notice Returns the integrator fee amount from the immutables.
     * @param immutables The immutables to extract the fee from.
     * @return ret The integrator fee amount.
     */
    function integratorFeeAmount(IBaseEscrow.Immutables memory immutables) internal pure returns (uint256 ret) {
        bytes memory parameters = immutables.parameters;
        if (parameters.length < 0x40) revert IndexOutOfRange();
        assembly ("memory-safe") {
            ret := mload(add(parameters, 0x40))
        }
    }
```
