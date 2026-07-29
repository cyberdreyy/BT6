### Title
Missing fee-vs-amount validation in `createDstEscrow` allows an unprivileged taker to permanently block destination withdrawals and reclaim funds via `cancel()` - ([File: contracts/BaseEscrowFactory.sol])

### Summary
`EscrowDst._withdraw` computes `amount = immutables.amount - integratorFeeAmount - protocolFeeAmount` [1](#0-0) , and this subtraction reverts on underflow if the combined fees are not strictly less than the escrow's `amount`. On the source-side entry point, `_postInteraction` explicitly guards against this: `if (integratorFeeAmount + protocolFeeAmount >= takingAmount) revert InvalidFeeAmounts();` [2](#0-1) . The destination-side entry point, `createDstEscrow`, has no equivalent check on the `parameters` field (which encodes `protocolFeeAmount`/`integratorFeeAmount`) against `dstImmutables.amount` [3](#0-2) .

### Finding Description
`createDstEscrow` is called directly by the taker/resolver (an unprivileged actor per the bounty's attacker model — "destination escrow creation" is an explicitly allowed entry point). The taker supplies the entire `IBaseEscrow.Immutables` struct, including the `parameters` bytes that `ImmutablesLib`/`EscrowDst` decode into `protocolFeeAmountCd()`/`integratorFeeAmountCd()` [4](#0-3) . The factory only validates `msg.value` against `safetyDeposit` (+`amount` for native token) [5](#0-4) ; it never checks `integratorFeeAmount + protocolFeeAmount < immutables.amount`.

If a taker sets `integratorFeeAmount + protocolFeeAmount >= amount` when calling `createDstEscrow`, every subsequent call to `EscrowDst.withdraw` or `publicWithdraw` will revert in `_withdraw` due to arithmetic underflow (Solidity 0.8 checked math) at `immutables.amount - integratorFeeAmount - protocolFeeAmount` [6](#0-5) . This makes it impossible for the maker to ever receive their destination-side funds, whether via the taker's private `withdraw` window or the permissionless `publicWithdraw` window. Only the same taker can subsequently call `cancel()` after the `DstCancellation` timelock, which returns the entire `amount` (with no fee deduction) plus the safety deposit back to the taker themselves [7](#0-6) .

This asymmetry lets a malicious taker: create a valid `EscrowSrc` (normal order fill), fund a `EscrowDst` with corrupted fee parameters via `createDstEscrow`, obtain the maker's secret (as is required in the normal off-chain protocol flow) and use it to withdraw the maker's tokens from `EscrowSrc`, while ensuring the destination side can never pay the maker (it always reverts) and eventually reclaiming their own destination-side deposit intact via `cancel()`.

### Impact Explanation
This directly matches the sDAI report's root cause class: a fee value that can exceed the value it's subtracted from, causing broken accounting that either underpays users or (here) makes payout arithmetic revert entirely. Unlike the sDAI shared-pool case, this repo's escrows are per-order isolated, so the analogous manifestation is a subtraction-underflow DoS rather than a silent shortfall — but it is caused by the identical missing-validation pattern, and the fix that exists on the source path (`InvalidFeeAmounts` check) is conspicuously absent on the destination path. The impact is at minimum "temporary freezing of funds during the live swap lifecycle" (High) since the destination escrow's payout to the maker is permanently blocked for the life of that escrow instance, and in the composed scenario across both escrows it enables theft of the maker's source-side funds with no destination-side compensation (Critical: "direct theft of user funds at rest or in motion").

### Likelihood Explanation
The path is trivially reachable: `createDstEscrow` is a public, unprivileged entry point, and the taker fully controls the `parameters` bytes passed as part of `dstImmutables` (as demonstrated by the fact that test/deployment scripts construct these fee amounts off-chain via `FeeCalcLib` and pass them straight into the immutables struct, e.g., in `script/txn_example/DeployEscrowDst.s.sol`) [8](#0-7) . Exploitation requires no special privileges, timing races, or governance access — only a taker willing to set a bad fee configuration when funding the destination escrow. The mitigating factor is that this relies on the off-chain protocol convention where the maker/relayer is expected to verify the deployed `EscrowDst`'s immutables (including its fee parameters) against the `SrcEscrowCreated` event's `immutablesComplement.parameters` before releasing the secret; if that off-chain check is performed rigorously, the corrupted escrow would be detected before the secret is disclosed. However, the on-chain contracts themselves provide no defense-in-depth here, unlike the equivalent `_postInteraction` guard.

### Recommendation
Add the same guard used in `_postInteraction` to `createDstEscrow`: revert if `integratorFeeAmount + protocolFeeAmount >= dstImmutables.amount` (decoded from `dstImmutables.parameters`), mirroring the existing `InvalidFeeAmounts()` check in `BaseEscrowFactory._postInteraction` [9](#0-8) .

### Proof of Concept
1. Taker builds `dstImmutables` with `amount = X` and `parameters = abi.encode(protocolFeeAmount, integratorFeeAmount, protocolFeeRecipient, integratorFeeRecipient)` where `protocolFeeAmount + integratorFeeAmount >= X` (analogous construction shown in `test/utils/BaseSetup.sol` `_prepareDataDstCustom` [10](#0-9) , but without the factory's `getFeeAmounts` guardrails a raw caller can set arbitrary values).
2. Taker calls `escrowFactory.createDstEscrow{value: ...}(dstImmutables, srcCancellationTimestamp)` — succeeds because there is no fee-vs-amount check [3](#0-2) .
3. After `DstWithdrawal` timelock, taker or any access-token holder calls `withdraw`/`publicWithdraw` with the correct secret — reverts on underflow inside `_withdraw` [11](#0-10) , for the entire withdrawal window.
4. After `DstCancellation` timelock, taker calls `cancel()` and receives the full `amount` + `safetyDeposit` back [7](#0-6) , while having already used the secret (obtained per the normal off-chain protocol handoff) to withdraw the maker's funds from `EscrowSrc`.

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

**File:** contracts/libraries/ImmutablesLib.sol (L19-44)
```text
    /**
     * @notice Returns the protocol fee amount from the immutables.
     * @param immutables The immutables to extract the fee from.
     * @return ret The protocol fee amount.
     */
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

**File:** script/txn_example/DeployEscrowDst.s.sol (L31-56)
```text
        uint256 dstAmount = 1; // ETH
        uint256 safetyDeposit = 1;
        bytes32 secret = keccak256(abi.encodePacked("secret"));
        bytes32 hashlock = keccak256(abi.encode(secret));

        (uint256 integratorFeeAmount, uint256 protocolFeeAmount) = FeeCalcLib.getFeeAmounts(
            dstAmount,
            protocolFee,
            integratorFee,
            integratorShare
        );

        IBaseEscrow.Immutables memory escrowImmutables = CrossChainTestLib.buildDstEscrowImmutables(
            orderHash,
            hashlock,
            dstAmount,
            maker,
            address(resolver),
            dstToken,
            safetyDeposit,
            timelocks,
            protocolFeeRecipient,
            integratorFeeRecipient,
            protocolFeeAmount,
            integratorFeeAmount
        );
```

**File:** test/utils/BaseSetup.sol (L279-319)
```text
    function _prepareDataDstCustom(
        bytes32 hashlock,
        uint256 amount,
        address maker,
        address taker,
        address token,
        uint256 safetyDeposit,
        uint256 protocolFee,
        uint256 integratorFee,
        uint256 integratorShares,
        uint256 whitelistDiscount,
        bool isWhitelisted
    ) internal view returns (IBaseEscrow.Immutables memory, uint256, EscrowDst) {
        protocolFee = isWhitelisted ? protocolFee * whitelistDiscount / BASE_1E2 : protocolFee;

        (uint256 integratorFeeAmount, uint256 protocolFeeAmount) = FeeCalcLib.getFeeAmounts(
            amount,
            protocolFee,
            integratorFee,
            integratorShares
        );

        bytes32 orderHash = bytes32(block.timestamp); // fake order hash
        uint256 srcCancellationTimestamp = block.timestamp + srcTimelocks.cancellation;
        IBaseEscrow.Immutables memory escrowImmutables = CrossChainTestLib.buildDstEscrowImmutables(
            orderHash,
            hashlock,
            amount,
            maker,
            taker,
            token,
            safetyDeposit,
            timelocksDst,
            protocolFeeReceiver,
            integratorFeeReceiver,
            protocolFeeAmount,
            integratorFeeAmount
        );

        return (escrowImmutables, srcCancellationTimestamp, EscrowDst(escrowFactory.addressOfEscrowDst(escrowImmutables)));
    }
```
