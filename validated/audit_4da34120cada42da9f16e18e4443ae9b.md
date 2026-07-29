### Title
Missing fee-amount validation in `createDstEscrow` causes permanent underflow revert in `EscrowDst._withdraw` - (File: `contracts/BaseEscrowFactory.sol`, `contracts/EscrowDst.sol`)

### Summary
`BaseEscrowFactory.createDstEscrow` accepts an arbitrary, caller-supplied `Immutables.parameters` field (which encodes `protocolFeeAmount` and `integratorFeeAmount`) without validating that these fees are smaller than `immutables.amount`. Unlike the source-side path (`_postInteraction`), which explicitly checks `integratorFeeAmount + protocolFeeAmount >= takingAmount` and reverts, the destination-side path has no equivalent guard. Because `EscrowDst._withdraw` subtracts these fee amounts from `immutables.amount` using unchecked-by-default Solidity 0.8 arithmetic, any unprivileged caller can craft a `dstImmutables.parameters` blob where `protocolFeeAmount + integratorFeeAmount >= amount`, permanently bricking `withdraw`/`publicWithdraw` on the resulting escrow clone via arithmetic underflow revert. This mirrors exactly the reported `OracleFeeDistributor` bug class: an unchecked fee-split parameter that can push a subtraction below zero and permanently revert the intended fund-release function.

### Finding Description
In `contracts/BaseEscrowFactory.sol`, the source path enforces a fee sanity check: [1](#0-0) 

But `createDstEscrow` performs no analogous validation on the caller-supplied `dstImmutables`: [2](#0-1) 

The `dstImmutables.parameters` bytes blob (which encodes `protocolFeeAmount` and `integratorFeeAmount`, read via `ImmutablesLib`) is fully attacker-controlled calldata — it is not derived from any on-chain fee computation for the destination escrow path, and `createDstEscrow` never checks it against `dstImmutables.amount`: [3](#0-2) 

These `parameters` are hashed as part of `immutables.hash()`, which is used both as the CREATE2 salt and as the value checked by `onlyValidImmutables` on every subsequent call, so the corrupted fee values are permanently baked into the deployed escrow instance and can never be corrected.

`EscrowDst._withdraw` then unconditionally subtracts both fee amounts from `immutables.amount`: [4](#0-3) 

If `integratorFeeAmount + protocolFeeAmount >= immutables.amount`, the line `uint256 amount = immutables.amount - integratorFeeAmount - protocolFeeAmount;` underflows, and Solidity 0.8's built-in checked arithmetic causes the entire transaction to revert. This affects both `withdraw` (`onlyCaller(taker)`) and `publicWithdraw` (`onlyAccessTokenHolder`), since both funnel into `_withdraw`: [5](#0-4) 

Because the escrow's address, hash, and stored parameters are all immutable once deployed, there is no recovery path for the intended fund-release flow — the only remaining path is `cancel()` after the `DstCancellation` timelock, which returns the full `amount` to the `taker` (not the intended maker) and bypasses fee logic entirely.

### Impact Explanation
This matches the Medium bounty category: "smart contract unable to operate because required token/native balances can be broken by an unprivileged actor." Any unprivileged address calling `createDstEscrow` (funding it with their own native/ERC20 balance per `nativeAmount`/`safeTransferFrom`) can encode fee parameters that make `withdraw`/`publicWithdraw` permanently unusable on that escrow instance, forcing the destination-chain leg of the swap into the `cancel` path instead of the fee-aware `withdraw` path — breaking the contract's designed fee-distribution and payout logic for that swap. This is the direct on-chain analog of the `OracleFeeDistributor` underflow: a fee-split value that isn't range-checked before being subtracted from a balance.

### Likelihood Explanation
`createDstEscrow` is a public, unauthenticated function reachable by any unprivileged EOA/contract in the normal destination-escrow-creation flow; no privileged role, governance action, or malicious infrastructure assumption is required — only supplying a crafted `parameters` field, which is trivial calldata construction.

### Recommendation
Add the same guard used in `_postInteraction` to `createDstEscrow`:
```solidity
if (dstImmutables.protocolFeeAmount() + dstImmutables.integratorFeeAmount() >= dstImmutables.amount) revert InvalidFeeAmounts();
```
This should be checked before deploying the clone, mirroring the check at `contracts/BaseEscrowFactory.sol:92`.

### Proof of Concept
1. Caller computes `dstImmutables` with `amount = X`, and `parameters = abi.encode(protocolFeeAmount = X, integratorFeeAmount = X, protocolFeeRecipient, integratorFeeRecipient)` (i.e., fees sum to `>= X`).
2. Caller invokes `BaseEscrowFactory.createDstEscrow{value: ...}(dstImmutables, srcCancellationTimestamp)` — succeeds, since no fee-sum check exists there (`contracts/BaseEscrowFactory.sol:165-185`).
3. Once the withdrawal window opens, any call to `EscrowDst.withdraw(secret, dstImmutables)` or `publicWithdraw` reaches `_withdraw`, where `immutables.amount - integratorFeeAmount - protocolFeeAmount` underflows and reverts (`contracts/EscrowDst.sol:92`).
4. The escrow can never be withdrawn from; the only usable path is `cancel()` after `DstCancellation`, returning funds to `taker` only.

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
