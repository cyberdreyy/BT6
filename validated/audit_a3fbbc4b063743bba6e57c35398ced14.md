## Title
Malicious maker can set an unpayable integrator/protocol fee recipient in the order extension, permanently blocking `EscrowDst._withdraw` and causing irrecoverable loss of the protocol/integrator fee - (File: `contracts/EscrowDst.sol`, `contracts/BaseEscrowFactory.sol`)

### Summary
`BaseEscrowFactory._postInteraction` decodes `integratorFeeRecipient` and `protocolFeeRecipient` directly from attacker-controlled `extraData` (part of the order extension that the maker signs) with no validation, and bakes these addresses unchecked into the `parameters` used to construct the destination escrow. `EscrowDst._withdraw` unconditionally transfers the fee amounts to these addresses before paying the maker, with no try/catch or fallback. A malicious maker can set either fee recipient to an address that always reverts on receiving the token (e.g., a bare non-payable contract, or a blacklisted address for tokens like USDC), which makes `withdraw`/`publicWithdraw` on `EscrowDst` permanently revert. Because the resolver obtains the secret off-chain (independent of a successful on-chain `EscrowDst` withdrawal) they can still claim the maker's source-chain funds via `EscrowSrc.withdraw`, while the protocol/integrator fee that should have been carved out on the destination side is never paid and can never be recovered (the fallback `cancel()` path returns the whole amount to the taker without paying fees at all).

### Finding Description
In `BaseEscrowFactory._postInteraction`, the fee-recipient addresses are taken verbatim from the order's extension data: [1](#0-0) 

These are embedded, unvalidated, into the destination escrow's immutable `parameters` that will later be used by `createDstEscrow`: [2](#0-1) 

`createDstEscrow` accepts these `dstImmutables` (including the embedded fee-recipient parameters) directly and deploys the destination escrow with them, without any allow-list or sanity check on the fee recipients: [3](#0-2) 

`EscrowDst._withdraw` then unconditionally transfers the integrator and protocol fees to these addresses, before the maker's portion, with no error handling: [4](#0-3) 

`_uniTransfer`/`safeTransfer` simply bubble up any revert from the recipient (blacklisted ERC20 address, non-payable contract, or a contract that explicitly reverts on `receive`), so a single malicious recipient permanently DoSes the entire `_withdraw` function for both `withdraw` (private) and `publicWithdraw`: [5](#0-4) 

Meanwhile, the maker knows the secret from the moment they create the hashlock and reveals it to the resolver off-chain to trigger destination delivery; the on-chain success of `EscrowDst.withdraw` is not a prerequisite for the resolver to learn the secret. Once known, the resolver can independently call `EscrowSrc.withdraw`/`withdrawTo`, which has no dependency on `parameters` (src immutables always carry empty `parameters`) or on the destination escrow's state: [6](#0-5) 

The only fallback for the destination side is `cancel()`, which returns the full amount to the taker and never attempts to pay protocol/integrator fees: [7](#0-6) 

So: the resolver still gets the maker's source funds using the already-known secret, the resolver can reclaim its own destination-side principal via `cancel()` after the timelock, but the protocol/integrator fee that should have been carved out of the destination transfer is never paid in either path (blocked forever in `withdraw`, skipped entirely in `cancel`). This is a direct structural analog of the Witch.sol bug: a reward/fee recipient address supplied by an unprivileged, self-interested party (the vault owner / here, the maker) is never validated, and a reverting recipient permanently breaks the payout leg meant for a third party (the auctioneer / here, the protocol and integrator), while the attacker still achieves their primary objective.

### Impact Explanation
This matches the Medium/High bounty criteria for "theft or permanent loss of unclaimed fee-like value" in the live swap lifecycle: the protocol and integrator fee amounts computed and reserved in the immutables (`protocolFeeAmountCd`/`integratorFeeAmountCd`) are permanently unclaimable for any swap where the maker sets a reverting fee recipient, with no admin recovery path (the fee is never transferred anywhere; it just remains stuck as part of an escrow that is eventually drained via `cancel()` to the taker without the fee being deducted).

### Likelihood Explanation
The attacker model is a fully unprivileged order-creating maker who controls the extension data of their own order (which is what determines `extraData` decoded in `_postInteraction`), requiring no privileged role, governance, or resolver collusion. Any resolver willing to fill a seemingly-attractive order would unknowingly trigger this outcome once they attempt to complete the destination-side withdrawal.

### Recommendation
- Wrap the fee transfers in `EscrowDst._withdraw` in a way that failure to pay a fee recipient does not block the maker's core payout (e.g., use a low-level call with a bounded gas stipend and continue on failure, escrowing the failed fee amount for later rescue), or
- Validate/whitelist `protocolFeeRecipient`/`integratorFeeRecipient` against a small set of protocol-approved addresses before allowing an escrow to be created with them, and
- Ensure `cancel()`/fallback paths still route the intended protocol/integrator fee correctly, or otherwise guarantee fee funds can never become permanently stuck due to a single non-cooperative recipient.

### Proof of Concept
1. Maker crafts an order and extension where `extraData[:20]` (`integratorFeeRecipient`) or `extraData[20:40]` (`protocolFeeRecipient`) points to a contract with no payable `receive`/`fallback` (or a token-blacklisted address for the destination token), per `BaseEscrowFactory.sol:77-78`.
2. A resolver fills the order; `_postInteraction` deploys `EscrowSrc` and emits `SrcEscrowCreated` with `immutablesComplement.parameters` containing the malicious fee recipient (`BaseEscrowFactory.sol:139-151`).
3. Resolver calls `createDstEscrow` with `dstImmutables` (unchanged fee-recipient parameters), funding the destination escrow (`BaseEscrowFactory.sol:165-185`).
4. Maker shares the secret with resolver off-chain (as per normal flow) to trigger destination delivery.
5. Resolver calls `EscrowDst.withdraw(secret, immutables)`; the call reverts inside `_withdraw` at the fee transfer step (`EscrowDst.sol:86-91`) every time, for the entire withdrawal window (private and public).
6. Resolver, already knowing the secret from step 4 regardless of step 5's outcome, calls `EscrowSrc.withdraw(secret, immutables)` and receives the maker's source tokens (`EscrowSrc.sol:38-45`).
7. After `DstCancellation`, resolver calls `EscrowDst.cancel()` to reclaim their destination principal + safety deposit (`EscrowDst.sol:64-73`), with no fee ever paid to the protocol/integrator — a permanent loss of the reserved fee amount.

### Citations

**File:** contracts/BaseEscrowFactory.sol (L77-78)
```text
        address integratorFeeRecipient = address(bytes20(extraData[:20]));
        address protocolFeeRecipient = address(bytes20(extraData[20:40]));
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

**File:** contracts/BaseEscrow.sol (L84-90)
```text
    function _uniTransfer(address token, address to, uint256 amount) internal {
        if (token == address(0)) {
            _ethTransfer(to, amount);
        } else {
            IERC20(token).safeTransfer(to, amount);
        }
    }
```

**File:** contracts/EscrowSrc.sol (L38-45)
```text
    function withdraw(bytes32 secret, Immutables calldata immutables)
        external
        onlyCaller(immutables.taker.get())
        onlyAfter(immutables.timelocks.get(TimelocksLib.Stage.SrcWithdrawal))
        onlyBefore(immutables.timelocks.get(TimelocksLib.Stage.SrcCancellation))
    {
        _withdrawTo(secret, msg.sender, immutables);
    }
```
