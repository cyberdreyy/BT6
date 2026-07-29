### Title
Missing fee-amount validation in `createDstEscrow` permanently bricks destination-escrow withdrawal via arithmetic underflow - (File: contracts/BaseEscrowFactory.sol)

### Summary
`BaseEscrowFactory.createDstEscrow` accepts caller-supplied `Immutables` (including the packed `parameters` field that encodes `protocolFeeAmount` and `integratorFeeAmount`) without validating that `protocolFeeAmount + integratorFeeAmount < amount`. The equivalent LOP-driven path (`_postInteraction`) explicitly guards against this with `if (integratorFeeAmount + protocolFeeAmount >= takingAmount) revert InvalidFeeAmounts();`, but `createDstEscrow` has no such check. Because `EscrowDst._withdraw` computes `immutables.amount - integratorFeeAmount - protocolFeeAmount` unchecked in Solidity 0.8 semantics (i.e., it reverts on underflow rather than wrapping), any escrow deployed through `createDstEscrow` with fee amounts ≥ `amount` can never complete a `withdraw`/`publicWithdraw` call — this is the same underflow root cause as the referenced Morpho `_calculateMaxBorrowCollateral` bug.

### Finding Description
- `BaseEscrowFactory._postInteraction` validates fees before constructing the destination-complement parameters: [1](#0-0) 
- `createDstEscrow` is a public, unprivileged entry point that takes the full `Immutables` struct (including `parameters`, which encodes the two fee amounts) directly from `msg.sender` and deploys the clone without any equivalent check: [2](#0-1) 
- The fee amounts are extracted from `immutables.parameters` via `ImmutablesLib`: [3](#0-2) 
- `EscrowDst._withdraw` subtracts both fees from `immutables.amount` without any bounds check: [4](#0-3) 

Since `parameters` is part of the immutables hash used both for the CREATE2 salt and for `onlyValidImmutables` validation, once an escrow is deployed with `protocolFeeAmount + integratorFeeAmount >= amount`, this data is permanently baked into that specific escrow instance — there is no way to "correct" it post-deployment. Any call to `withdraw` or `publicWithdraw` on that escrow will revert with a Solidity arithmetic-underflow panic, forever.

### Impact Explanation
This matches the Medium bounty category: "smart contract unable to operate because required token/native balances can be broken by an unprivileged actor." Any unprivileged caller of `createDstEscrow` (explicitly an in-scope entry point per the attacker model) can deploy a destination escrow whose core `withdraw` function is permanently non-functional due to unchecked underflow, breaking the intended operation of the contract for that instance. `cancel` remains available as a recovery path for the escrow's own funder (`taker`) after the cancellation timelock, so this does not constitute a full "permanent freezing of funds" for a third party — the funds locked are the caller's own, and the caller (as `taker`) can eventually reclaim them via `cancel`, which does not perform the fee subtraction. Because the caller who deploys the escrow is also the one funding it, this appears to be primarily a self-inflicted griefing vector rather than a route to steal or permanently freeze a third party's assets.

### Likelihood Explanation
Low-to-moderate. Triggering this requires deliberately constructing `parameters` with `protocolFeeAmount + integratorFeeAmount >= amount` and calling `createDstEscrow` directly (bypassing the LOP fee-computation/validation path). Because the caller must fund the escrow themselves and the only apparent effect is bricking their own escrow's withdraw path (recoverable via `cancel`), there is limited incentive for an attacker to exploit this against themselves, and no clear mechanism was found by which this could brick or divert a legitimate maker's/resolver's honestly-created escrow (the CREATE2 address is uniquely derived from these same `parameters`, so a maliciously-parameterized escrow occupies a different address than a correctly-parameterized one for the same order).

### Recommendation
Add the same fee-consistency check used in `_postInteraction` to `createDstEscrow`:
```solidity
if (dstImmutables.protocolFeeAmountCd() + dstImmutables.integratorFeeAmountCd() >= dstImmutables.amount) revert InvalidFeeAmounts();
```
This closes the gap between the two escrow-creation code paths and prevents any escrow (regardless of creation route) from being deployed in a state where `withdraw` is guaranteed to underflow-revert.

### Proof of Concept
1. Attacker crafts `dstImmutables.parameters = abi.encode(protocolFeeAmount, integratorFeeAmount, protocolFeeRecipient, integratorFeeRecipient)` such that `protocolFeeAmount + integratorFeeAmount >= dstImmutables.amount`.
2. Attacker calls `createDstEscrow(dstImmutables, srcCancellationTimestamp)` funding it with `msg.value`/ERC20 approval as required — no revert occurs since there is no fee-sum check.
3. When the secret is later revealed and any party (maker, taker, or public withdrawer) calls `withdraw`/`publicWithdraw` on this escrow, `EscrowDst._withdraw` executes `immutables.amount - integratorFeeAmount - protocolFeeAmount`, which underflows and reverts, permanently disabling withdrawal for this escrow instance (verified by inspecting [5](#0-4)  against the missing check in [2](#0-1) ).

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
