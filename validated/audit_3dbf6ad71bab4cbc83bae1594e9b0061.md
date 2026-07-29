### Title
Reentrancy in `EscrowDst._withdraw` via native-token fee/maker transfers allows draining escrow balance before completing payout - (File: `contracts/EscrowDst.sol`)

### Summary
`EscrowDst._withdraw` performs four sequential external value transfers (integrator fee, protocol fee, maker amount, safety deposit) with no reentrancy guard and no state flag marking the withdrawal as processed. When the swapped asset is the native token (`immutables.token == address(0)`), the fee-recipient payouts go through `_uniTransfer` → `_ethTransfer`, which is a raw low-level `.call` that hands execution control to the recipient contract. Since `integratorFeeRecipient`/`protocolFeeRecipient` are attacker-influenceable addresses embedded in `immutables.parameters` (set from `extraData` by whoever fills the order via `_postInteraction`, or embedded directly in the `dstImmutables` supplied to the permissionless `createDstEscrow`), an attacker acting as taker/fee-recipient can re-enter `withdraw`/`publicWithdraw` with the same secret and drain the escrow's native balance through repeated fee payouts before the remaining transfers to the maker/safety-deposit collector complete.

### Finding Description
`_withdraw` in `EscrowDst.sol` does the following, in order, with no reentrancy protection: [1](#0-0) 

- Both `_uniTransfer` (which dispatches to `_ethTransfer` when `token == address(0)`) and `_ethTransfer` itself use a low-level `.call` that transfers control flow to the recipient: [2](#0-1) 

- None of the guards on `withdraw`/`publicWithdraw`/`_withdraw` are stateful: `onlyValidImmutables` only recomputes the CREATE2 address, and `onlyValidSecret` only re-hashes the already-known secret — both pass identically on a reentrant call: [3](#0-2) [4](#0-3) 

- `integratorFeeRecipient`/`protocolFeeRecipient` are attacker-influenceable: on the source-chain side they are taken verbatim from the taker-supplied `extraData` of `_postInteraction` (`extraData[:20]`/`extraData[20:40]`), then packed into `DstImmutablesComplement.parameters`: [5](#0-4) 
  and `createDstEscrow` is a fully permissionless external function that accepts the caller-supplied `dstImmutables` (including `.parameters`, i.e., the fee recipients) with no on-chain check that it matches the `DstImmutablesComplement` emitted on the source chain: [6](#0-5) 

No `nonReentrant`/`ReentrancyGuard` usage exists anywhere in the escrow contracts, confirmed by searching the repo.

### Impact Explanation
When the destination asset is native token, an attacker who controls (or colludes as) the taker and sets `integratorFeeRecipient` (or `protocolFeeRecipient`) to a malicious contract can re-enter `withdraw`/`publicWithdraw` from within the fee payout's `.call`. Because the checks are purely structural/hash-based rather than state-based, the reentrant call re-executes the full fee/maker/safety-deposit payout sequence again against the same still-funded escrow balance, allowing repeated draws of the fee-sized amount until the native balance is exhausted, at the expense of the funds meant to reach the maker and the safety-deposit collector. This is a direct in-flight fund-draining/theft during the live withdrawal path of a destination escrow, matching the Critical/High "theft of user funds in motion" / "theft of fee-like value" bounty categories.

### Likelihood Explanation
The likelihood is moderate: it only triggers for **native-token** destination swaps (`immutables.token == address(0)`), and it requires the attacker to control the fee-recipient address that gets embedded into the immutables used to deploy the destination escrow — a value that is attacker-suppliable both via `_postInteraction`'s `extraData` and directly via the permissionless `createDstEscrow` call. No governance/owner/whitelisted-resolver privilege is required to set these fields or to call `withdraw`/`publicWithdraw`.

### Recommendation
Add a `nonReentrant` guard (checks-effects-interactions or an OpenZeppelin `ReentrancyGuard`) around `_withdraw`/`_cancel` in `EscrowDst`/`EscrowSrc`, or mark the escrow as "spent" (e.g., a boolean/state variable set before any external transfer) so that a reentrant call to `withdraw`/`publicWithdraw`/`cancel` reverts immediately, mirroring the reported fix pattern of "marking the hash as processed before sending any tokens."

### Proof of Concept
1. Attacker (as taker/filler) fills an order (or directly calls `createDstEscrow`) for a destination escrow with `token == address(0)`, setting `integratorFeeRecipient` (encoded into `immutables.parameters`) to their own malicious contract address, and setting themselves as `taker`.
2. After `DstWithdrawal` stage begins, attacker calls `EscrowDst.withdraw(secret, immutables)`.
3. `_withdraw` calls `_uniTransfer(token=0, integratorFeeRecipient, integratorFeeAmount)` → `_ethTransfer` → low-level `.call` to the attacker's contract.
4. The attacker's contract's `receive()`/`fallback()` re-enters `withdraw(secret, immutables)` (same secret, same immutables — both checks pass again since msg.sender still equals `taker`).
5. The nested call repeats the fee payout to the attacker again before the original call resumes and pays out the maker's share/safety deposit, allowing the attacker to repeatedly capture native-token balance intended for the maker/safety-deposit collector until the escrow's balance is exhausted. [1](#0-0)

### Citations

**File:** contracts/EscrowDst.sol (L36-43)
```text
    function withdraw(bytes32 secret, Immutables calldata immutables)
        external
        onlyCaller(immutables.taker.get())
        onlyAfter(immutables.timelocks.get(TimelocksLib.Stage.DstWithdrawal))
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

**File:** contracts/BaseEscrow.sol (L43-51)
```text
    modifier onlyValidImmutables(bytes32 immutablesHash) virtual {
        _validateImmutables(immutablesHash);
        _;
    }

    modifier onlyValidSecret(bytes32 secret, bytes32 hashlock) {
        if (_keccakBytes32(secret) != hashlock) revert InvalidSecret();
        _;
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

**File:** contracts/BaseEscrowFactory.sol (L77-151)
```text
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
