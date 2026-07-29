### Title
Malicious maker/receiver can permanently DOS `EscrowDst.withdraw`/`publicWithdraw`, temporarily freezing resolver's locked funds until cancellation - (File: contracts/EscrowDst.sol)

### Summary
`EscrowDst._withdraw()` sends the destination amount to the `maker` using `_uniTransfer`, which for native token (`address(0)`) resolves to `_ethTransfer` — a low-level `.call{value: amount}("")` that reverts the whole transaction if the call is unsuccessful. Because the `maker`/`receiver` address is fully controlled by the order-creating user (an unprivileged party) and is never validated to be capable of receiving native ETH, a malicious maker can set their receiver to a contract that always reverts on receiving ETH. This makes `withdraw` and `publicWithdraw` permanently revert for that escrow, freezing the resolver's (taker's) deposited destination funds and safety deposit until the `DstCancellation` timelock elapses and `cancel()` becomes callable.

### Finding Description
`EscrowDst._withdraw` performs the maker payout before the safety-deposit payout, and both are gated behind the same atomic call: [1](#0-0) 

`_uniTransfer` routes native-token transfers through `_ethTransfer`, which reverts the entire call on transfer failure: [2](#0-1) 

The `maker` address used here originates from `order.receiver` (or `order.maker` if unset), which is chosen by the maker when creating the src order and propagated to the `DstImmutablesComplement` used to deploy the destination escrow: [3](#0-2) 

The resolver (taker) then deploys the destination escrow via `createDstEscrow`, funding it with the native `amount + safetyDeposit` when the destination token is `address(0)`, with no validation that the `maker` recipient can actually accept a plain ETH transfer: [4](#0-3) 

If the maker deploys a contract as their receiver that unconditionally reverts on `receive()`/has no payable fallback, every call to `EscrowDst.withdraw` (private, by the designated taker) and `EscrowDst.publicWithdraw` (by any access-token holder) will revert, because the maker leg of `_withdraw` always fails first. This is the same root cause as the reNFT bug: an unprivileged counterparty (the maker, standing in for the "lender") can make their own receiving codepath revert, thereby DOS-ing a state-transition function that is supposed to atomically move funds to two parties, freezing funds belonging to the *other* party (the resolver/taker, standing in for the "renter") until an unrelated, separately-triggered fallback path becomes available.

The only recovery path is `cancel()`, callable only after `DstCancellation` timelock, which returns the destination amount and safety deposit to the taker instead of the maker: [5](#0-4) 

Until that timelock elapses, the resolver's capital (the `takingAmount` worth of native token plus the `safetyDeposit`) is locked and unusable, and no keeper can earn the public-withdrawal safety-deposit incentive since `publicWithdraw` shares the same vulnerable `_withdraw` path.

### Impact Explanation
This matches the "High: temporary freezing of funds during the live swap lifecycle" category in the bounty scope. A malicious/unprivileged maker can, at zero cost to themselves beyond forgoing the destination tokens they'd otherwise receive, lock a resolver's real capital (destination-chain native tokens + safety deposit) in the destination escrow for the entire withdrawal/public-withdrawal window. The resolver cannot recover funds until `DstCancellation`, and the maker can also selectively toggle their receiving contract's revert condition (identical to the reNFT PoC pattern) to extort the resolver, e.g., demanding an off-chain side-payment in exchange for allowing withdraw to succeed before the timelock passes.

### Likelihood Explanation
Likelihood is moderate: it requires the destination token to be native (`address(0)`, an explicitly supported configuration per `createDstEscrow`/tests such as `test_WithdrawByResolverDstNative`), and the maker to set `order.receiver` to a purpose-built reverting contract. Both preconditions are fully within an ordinary maker's control and require no privileged role, matching the "unprivileged user only" attacker model in scope. Resolvers currently have no on-chain way to detect this before locking funds into the destination escrow.

### Recommendation
- Use a pull-payment pattern for the maker's destination payout (e.g., credit an internal balance the maker must claim) instead of pushing native ETH synchronously inside `_withdraw`.
- Alternatively, bound the gas forwarded in `_ethTransfer` and treat failure to the maker leg as non-fatal (e.g., route failed native transfers to a rescuable/claimable balance) rather than reverting the entire withdrawal, so the safety deposit and secret-reveal mechanics are not blocked by a hostile recipient.
- Consider validating/whitelisting that `maker` can receive native transfers (or disallowing native destination token when `order.receiver` differs from an EOA-verifiable maker) before allowing `createDstEscrow` to lock resolver funds.

### Proof of Concept
1. Maker creates a `MaliciousReceiver` contract with no payable `receive()`/`fallback()` (or one that always reverts), and sets it as `order.receiver` in their src order.
2. Resolver fills the order (deploys `EscrowSrc` normally) and then calls `createDstEscrow` with `dstImmutables.token == address(0)`, sending `msg.value = amount + safetyDeposit`, using the malicious address as `maker` (matching `DstImmutablesComplement.maker` emitted on `SrcEscrowCreated`), mirroring the deployment pattern in `test_WithdrawByResolverDstNative`: [6](#0-5) 
3. After `DstWithdrawal` stage begins, resolver calls `EscrowDst.withdraw(secret, immutables)`. The internal `_uniTransfer(address(0), maliciousMaker, amount)` → `_ethTransfer` call reverts, causing the whole `withdraw` transaction (and any subsequent `publicWithdraw` attempts by other access-token holders) to revert with `NativeTokenSendingFailure`.
4. The resolver's `amount + safetyDeposit` remains locked in the `EscrowDst` clone until `block.timestamp >= DstCancellation`, at which point `cancel()` must be used to recover the funds — confirming the temporary freeze caused solely by the maker's malicious receiving contract.

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

**File:** test/unit/Escrow.t.sol (L438-455)
```text
    function test_WithdrawByResolverDstNative() public {
        (IBaseEscrow.Immutables memory immutables, uint256 srcCancellationTimestamp, IEscrowDst dstClone) = _prepareDataDstCustom(
            HASHED_SECRET,
            TAKING_AMOUNT,
            alice.addr,
            bob.addr,
            address(0x00),
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
```
