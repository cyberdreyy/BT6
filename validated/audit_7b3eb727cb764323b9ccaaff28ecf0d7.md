## Title
`EscrowDst` fee-amount underflow in `_withdraw()` lets a malicious taker permanently block maker payout and reclaim funds via `cancel()` - (File: `contracts/EscrowDst.sol`)

### Summary
`EscrowDst._withdraw()` computes `immutables.amount - integratorFeeAmount - protocolFeeAmount` without ever checking that the combined fee does not exceed the escrowed amount. Unlike the source-chain path, `createDstEscrow()` in `BaseEscrowFactory.sol` performs no equivalent validation on the `dstImmutables.parameters` it accepts. A taker who calls `createDstEscrow()` directly (as anyone is permitted to do) can supply arbitrary `protocolFeeAmount`/`integratorFeeAmount` values that exceed `amount`, causing every future call to `withdraw()`/`publicWithdraw()` to revert with an arithmetic underflow — permanently disabling the maker's payout path — while `cancel()` (which does not subtract fees) remains callable by the taker after the cancellation timelock, letting the taker reclaim the full destination deposit.

### Finding Description
In `_postInteraction` (source-chain escrow creation), fee amounts are bounds-checked against `takingAmount`: [1](#0-0) 

This check protects the values embedded in `DstImmutablesComplement.parameters` that are emitted for off-chain consumption, but it is never enforced on-chain for the actual destination escrow. `createDstEscrow()` accepts `dstImmutables` supplied directly by the caller (the taker) and only validates `msg.value`/timing — it never re-derives or checks `protocolFeeAmount + integratorFeeAmount < amount`: [2](#0-1) 

`EscrowDst._withdraw()` then unconditionally subtracts these attacker-controlled fee fields from `immutables.amount`: [3](#0-2) 

Since `withdraw()` and `publicWithdraw()` both route through `_withdraw()`, if `integratorFeeAmount + protocolFeeAmount > immutables.amount`, line 92 underflows and reverts for every caller — including `publicWithdraw()`, the safety-valve intended to let any `accessToken` holder force payout to the maker if the taker stalls: [4](#0-3) 

Once the withdrawal window elapses, `cancel()` — callable only by the taker — returns the entire `immutables.amount` plus safety deposit to the taker without any fee subtraction, so it is unaffected by the underflow: [5](#0-4) 

Because `createDstEscrow` has no whitelist/access restriction (unlike `_postInteraction`, which explicitly requires the resolver to be whitelisted via `FeeTaker.OnlyWhitelistOrAccessToken`), the taker who funds the destination escrow fully controls every field of `dstImmutables`, including the fee parameters: [6](#0-5) 

### Impact Explanation
A taker who has already claimed the maker's source-chain tokens (by revealing the secret on `EscrowSrc`) can construct the corresponding `EscrowDst` with `protocolFeeAmount + integratorFeeAmount > amount`. This makes withdrawal to the maker permanently impossible (both private and public paths revert), and after the `DstCancellation` timelock passes, the same taker calls `cancel()` to reclaim 100% of the destination deposit. The maker loses the source-chain assets already taken by the taker and receives nothing on the destination chain — a direct theft/permanent loss of user funds, defeating the atomic-swap and public-withdraw safety guarantees that specifically exist to prevent a dishonest taker from unilaterally reneging on a swap.

### Likelihood Explanation
Medium-to-high: exploitation requires no privileged role — any address acting as taker can call `createDstEscrow()` with attacker-chosen `parameters`. It only requires the taker to also complete their own withdrawal on the source chain (which they are already incentivized to do to obtain the maker's tokens), then simply wait out the cancellation timelock instead of ever calling `withdraw()` honestly.

### Recommendation
Validate fee amounts against the escrowed amount at the point the destination escrow is created, mirroring the source-chain check:

```solidity
function createDstEscrow(IBaseEscrow.Immutables calldata dstImmutables, uint256 srcCancellationTimestamp) external payable {
    ...
    if (dstImmutables.protocolFeeAmount() + dstImmutables.integratorFeeAmount() >= dstImmutables.amount) {
        revert InvalidFeeAmounts();
    }
    ...
}
```
Alternatively/additionally, cap the fee deduction inside `EscrowDst._withdraw()` so it can never exceed `immutables.amount`, preventing the underflow regardless of how the escrow was created.

### Proof of Concept
1. Taker fills a maker's order via LOP; `EscrowSrc` is deployed and funded with the maker's `makingAmount` (no dst-side fee validation applies here).
2. Taker calls `escrowFactory.createDstEscrow{value: amount + safetyDeposit}(dstImmutables, srcCancellationTimestamp)` where `dstImmutables.parameters = abi.encode(protocolFeeAmount, integratorFeeAmount, protocolFeeRecipient, integratorFeeRecipient)` with `protocolFeeAmount + integratorFeeAmount > amount`. This succeeds because no check exists (see `contracts/BaseEscrowFactory.sol:165-185`).
3. Taker reveals the secret on `EscrowSrc.withdraw()` during the src withdrawal window, receiving the maker's tokens.
4. Anyone (including the maker) calling `EscrowDst.withdraw()`/`publicWithdraw()` with the now-public secret reverts due to underflow at `contracts/EscrowDst.sol:92`.
5. After `DstCancellation`, taker calls `EscrowDst.cancel()`, receiving the full `amount` + `safetyDeposit` back — the maker receives nothing on the destination chain despite having lost their source-chain assets.

### Citations

**File:** contracts/BaseEscrowFactory.sol (L91-92)
```text

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

**File:** test/unit/EscrowFactory.t.sol (L276-298)
```text
    // Only whitelisted resolver can deploy escrow
    function test_NoDeploymentForNotResolver() public {
        CrossChainTestLib.SwapData memory swapData = _prepareDataSrc(true, false);

        (bool success,) = address(swapData.srcClone).call{ value: SRC_SAFETY_DEPOSIT }("");
        assertEq(success, true);
        usdc.transfer(address(swapData.srcClone), MAKING_AMOUNT);

        inch.mint(alice.addr, 10 ether);

        vm.prank(address(limitOrderProtocol));
        vm.expectRevert(FeeTaker.OnlyWhitelistOrAccessToken.selector);
        escrowFactory.postInteraction(
            swapData.order,
            "", // extension
            swapData.orderHash,
            alice.addr, // taker
            MAKING_AMOUNT,
            TAKING_AMOUNT,
            0, // remainingMakingAmount
            swapData.extraData
        );
    }
```
