### Title
Missing fee-amount validation in `createDstEscrow` allows permanent underflow-revert lock of `EscrowDst.withdraw` - (File: contracts/BaseEscrowFactory.sol)

### Summary
This is the same root-cause class as Sherlock M-2 (BunniPrice/BunniSupply): a value that is validated on one code path (`_postInteraction`) is never re-validated on the parallel code path that actually consumes it (`createDstEscrow`/`EscrowDst._withdraw`), producing a state where the "checked" invariant and the "used" invariant diverge.

### Finding Description
On the source-chain path, `BaseEscrowFactory._postInteraction` explicitly guards against fee amounts exceeding the swap amount: [1](#0-0) 

However, `integratorFeeAmount`/`protocolFeeAmount` computed here are only emitted informationally inside `DstImmutablesComplement.parameters` via `SrcEscrowCreated`; they are never persisted on-chain or cross-checked against the immutables that are actually used to deploy the destination escrow.

`createDstEscrow` is a fully permissionless, unprivileged entry point (no whitelist/access-token check, unlike `_postInteraction`) that accepts an arbitrary caller-supplied `dstImmutables` struct, including its `parameters` field encoding `protocolFeeAmount`/`integratorFeeAmount`: [2](#0-1) 

There is no equivalent `if (integratorFeeAmount + protocolFeeAmount >= amount) revert ...` check here. The fee amounts flow unchecked into the deployed `EscrowDst` clone via `Immutables.parameters` and are later consumed in `_withdraw`: [3](#0-2) 

Line `uint256 amount = immutables.amount - integratorFeeAmount - protocolFeeAmount;` performs unchecked-by-default Solidity 0.8 checked subtraction. If `integratorFeeAmount + protocolFeeAmount > immutables.amount`, this reverts with an arithmetic underflow on every call — `withdraw`, `publicWithdraw`, and any secret-based path — because `immutables` is fixed (verified via `onlyValidImmutables(immutables.hash())`) and cannot be corrected after deployment.

### Impact Explanation
Any unprivileged caller (no whitelist requirement, unlike the src side) can deploy an `EscrowDst` clone whose `parameters` encode `protocolFeeAmount + integratorFeeAmount >= amount`. For that specific escrow instance, `_withdraw` (and therefore `withdraw`/`publicWithdraw`) becomes permanently unusable — the escrow cannot fulfil its core function of releasing funds to the maker once a valid secret is revealed. This matches the bounty's Medium category: "smart contract unable to operate because required token/native balances can be broken by an unprivileged actor." Recovery is possible only via `cancel()` after the `DstCancellation` timelock, which returns the deposited `amount` to `immutables.taker` rather than the maker — meaning the intended recipient of the swap (`immutables.maker`) can never receive their funds through the withdraw path, and the swap can only unwind, not settle, for the affected instance.

### Likelihood Explanation
`createDstEscrow` has no access control, so triggering the broken state requires only crafting `dstImmutables.parameters` with `integratorFeeAmount + protocolFeeAmount >= amount` and funding the escrow — a straightforward, low-cost, single-transaction action. The factory performs no cross-check against the fee amounts computed and validated in `_postInteraction`/`SrcEscrowCreated`, so the divergence between "checked" (src side) and "used" (dst side) fee invariants is trivially reachable.

### Recommendation
Mirror the `InvalidFeeAmounts` guard from `_postInteraction` inside `createDstEscrow` (or inside `BaseEscrow`/`EscrowDst` construction), rejecting any `dstImmutables` where `protocolFeeAmount + integratorFeeAmount >= immutables.amount`, e.g.:
```solidity
if (immutables.protocolFeeAmount() + immutables.integratorFeeAmount() >= immutables.amount) revert InvalidFeeAmounts();
```
This aligns the destination-side validation with the source-side validation, closing the same class of "checked vs. used" mismatch identified in the BunniPrice/BunniSupply report.

### Proof of Concept
1. An unprivileged address calls `createDstEscrow` with `dstImmutables.amount = 100`, `token = someERC20`, and `parameters = abi.encode(protocolFeeAmount = 60, integratorFeeAmount = 50, protocolFeeRecipient, integratorFeeRecipient)` (sum 110 > 100), funding the escrow with 100 tokens + safety deposit as required by `createDstEscrow`'s balance checks. [2](#0-1) 
2. After `DstWithdrawal` stage begins, anyone possessing the secret calls `withdraw(secret, immutables)`.
3. Inside `_withdraw`, `uint256 amount = immutables.amount - integratorFeeAmount - protocolFeeAmount;` evaluates `100 - 60 - 50`, underflows, and reverts for every subsequent call. [4](#0-3) 
4. The maker can never receive the destination funds through `withdraw`/`publicWithdraw`; the only recovery is `cancel()` after `DstCancellation`, which refunds `immutables.taker`, not the maker — demonstrating the broken/unusable contract state caused entirely by an unprivileged actor’s unchecked input.

### Citations

**File:** contracts/BaseEscrowFactory.sol (L84-93)
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
