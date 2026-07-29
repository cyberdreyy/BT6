### Title
Attacker-controlled `integratorFeeRecipient` can permanently DoS native-token destination withdrawals - ([File: contracts/EscrowDst.sol])

### Summary
`EscrowDst._withdraw` pays out the integrator fee and protocol fee via `_uniTransfer` before paying the maker and the caller's safety deposit. When the destination asset is the native token, `_uniTransfer` routes to `_ethTransfer`, which reverts the *entire* transaction if the low-level `call` to the recipient fails [1](#0-0) . The `integratorFeeRecipient` (and `protocolFeeRecipient`) addresses are taken verbatim from attacker-suppliable `extraData`/order-extension bytes with no validation that they can actually receive native tokens [2](#0-1) , and are later read back out of `immutables.parameters` inside `EscrowDst._withdraw` with the exact same lack of validation [3](#0-2) [4](#0-3) . This is the same root cause as the reported `ConsensusLayerFeeDispatcher` issue: an unprivileged, attacker-controlled fee recipient can revert a shared payout call and DoS the whole withdrawal for every other party (maker, taker/resolver, protocol).

### Finding Description
The order/extension data that seeds `integratorFeeRecipient` and `protocolFeeRecipient` is parsed directly from `extraData` in `BaseEscrowFactory._postInteraction` and copied unchecked into `DstImmutablesComplement.parameters`, which later becomes the destination escrow's `Immutables.parameters` used on `createDstEscrow` [5](#0-4) . There is no check anywhere that this address is capable of accepting a plain native-token transfer.

When the destination token is native (`address(0)`) and `integratorFeeAmount > 0`, `EscrowDst._withdraw` calls `_uniTransfer(token, integratorFeeRecipient, integratorFeeAmount)`, which resolves to `_ethTransfer`, doing a bare `call{value: amount}("")` and reverting with `NativeTokenSendingFailure` on any failure [6](#0-5) . If `integratorFeeRecipient` is a contract without a `receive`/`payable fallback` (or one that deliberately reverts), *every* call to `withdraw` and `publicWithdraw` on that escrow will revert — for the taker, the access-token-holder public caller, and anyone else — because these all funnel into the same `_withdraw` internal function [7](#0-6) .

This mirrors the external report precisely: a party with no special privilege (the order creator, who fully controls the `integratorFeeRecipient` field embedded in the signed order extension) can plant a reverting recipient, causing the shared fee-routing call to revert and drag down the maker's and taker's legitimate payouts along with it.

### Impact Explanation
Because `withdraw`/`publicWithdraw` are blocked for the whole `DstWithdrawal`→`DstPublicWithdrawal`→`DstCancellation` window, the taker's deposited native `amount + safetyDeposit` sit frozen in the escrow and cannot be released to the maker, nor can the taker collect the safety deposit, for the duration of the withdrawal windows. The secret is also never revealed on the destination side while withdrawal is blocked, which can cascade into the resolver being unable to safely reveal/use the secret on the source escrow as well. Funds are only unblocked once the destination cancellation timelock passes and `cancel()` (which sends `amount` back to the taker, not through the broken fee path) is called [8](#0-7) . This is a temporary but attacker-triggerable freezing of funds during the live swap lifecycle, matching the bounty's High-impact category ("temporary freezing of funds during the live swap lifecycle").

### Likelihood Explanation
Likelihood is high for any native-token destination swap that includes a nonzero integrator fee: the order creator fully controls the `integratorFeeRecipient` bytes embedded in the extension/`extraData` before the order is signed and filled, and no code path validates that this address can receive ETH. No special role or timing race is required — simply deploying a small reverting contract as the fee recipient and using it in a normal order is sufficient.

### Recommendation
Wrap the fee-recipient native transfers in `EscrowDst._withdraw` (and any other place fee/native transfers go to an externally-supplied, unprivileged-controlled address) in a non-reverting low-level call that limits `returndata` and emits an event on failure instead of bubbling up a revert, so a hostile fee recipient cannot block the maker's and taker's legitimate payouts. Alternatively, credit failed fee payouts to a pull-based balance the recipient can claim later, decoupling their receive-logic from the critical withdrawal path.

### Proof of Concept
1. Attacker (order creator) deploys `RevertingRecipient` with no `receive`/`fallback`.
2. Attacker builds and signs a normal Fusion+ order whose `extraData`/extension sets `integratorFeeRecipient = address(RevertingRecipient)`, `dstToken = address(0)` (native), and a nonzero integrator fee, following the `extraData` layout parsed in `_postInteraction` [9](#0-8) .
3. A resolver fills the order normally; `postInteraction` emits `SrcEscrowCreated` with `parameters` embedding the malicious recipient [5](#0-4) .
4. Resolver calls `createDstEscrow{value: amount + safetyDeposit}(...)`, funding the destination escrow.
5. Once `DstWithdrawal` starts, resolver calls `withdraw(secret, immutables)`; `_withdraw` attempts to send the integrator fee to `RevertingRecipient`, the low-level call fails, and the whole transaction reverts with `NativeTokenSendingFailure` [10](#0-9) . The same happens for `publicWithdraw` during the public window — no one can withdraw until `DstCancellation`, at which point only `cancel()` (returning funds to the taker) is available.

### Citations

**File:** contracts/BaseEscrow.sol (L92-98)
```text
    /**
     * @dev Transfers native tokens to the recipient.
     */
    function _ethTransfer(address to, uint256 amount) internal {
        (bool success,) = to.call{ value: amount }("");
        if (!success) revert NativeTokenSendingFailure();
    }
```

**File:** contracts/BaseEscrowFactory.sol (L53-65)
```text
     * extraData consists of:
     * 20 bytes — integrator fee recipient
     * 20 bytes - protocol fee recipient
     * Fee structure determined by `super._getFeeAmounts`:
     *      2 bytes — integrator fee percentage (in 1e5)
     *      1 byte - integrator rev share percentage (in 1e2)
     *      2 bytes — resolver fee percentage (in 1e5)
     *      1 byte - whitelist discount numerator (in 1e2)
     * Whitelist structure:
     *      4 bytes - allowed time
     *      1 byte - size of the whitelist
     *      (bytes12)[N] — taker whitelist
     * bytes — custom data to call extra postInteraction (optional)
```

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

**File:** contracts/libraries/ImmutablesLib.sol (L115-121)
```text
    function integratorFeeRecipientCd(IBaseEscrow.Immutables calldata immutables) external pure returns (Address ret) {
        bytes calldata parameters = immutables.parameters;
        if (parameters.length < 0x80) revert IndexOutOfRange();
        assembly ("memory-safe") {
            ret := calldataload(add(parameters.offset, 0x60))
        }
    }
```
