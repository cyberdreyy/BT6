## Finding: Fee-recipient transfer failure permanently blocks `EscrowDst.withdraw`/`publicWithdraw` during the entire withdrawal window

### Title
Attacker-controlled `protocolFeeRecipient`/`integratorFeeRecipient` can revert `_withdraw`, freezing maker funds and resolver safety deposit for the whole withdrawal window - (`contracts/EscrowDst.sol`)

### Summary
`EscrowDst._withdraw` unconditionally sends the integrator and protocol fee portions to addresses that are embedded, unchecked, in the order's `extraData`/`immutables.parameters` before it releases the remaining amount to the maker. There is no error handling around these transfers - a revert in any of them (e.g. a fee recipient contract that reverts on receiving native ETH) bricks `withdraw`/`publicWithdraw` for the entire withdrawal window, exactly mirroring the root cause described in the external report (an unguarded transfer to a possibly-hostile/blocked recipient causing the whole function to halt).

### Finding Description
`_withdraw` in `EscrowDst.sol` does: [1](#0-0) 

The fee amounts/recipients come straight from `immutables.parameters`, decoded via `ImmutablesLib`: [2](#0-1) 

These values originate from `extraData` passed into `_postInteraction`, taken directly from the first 40 bytes of the caller-supplied extension with no allow-list or sanity check on the recipient addresses: [3](#0-2) 

Both `withdraw` and `publicWithdraw` funnel into the same `_withdraw`, so any revert inside it blocks the whole withdrawal window (private and public): [4](#0-3) 

Transfers go through `_uniTransfer`/`_ethTransfer`, which propagate any failure as a hard revert with no fallback handling — the direct analog of the reported `panic(err)` pattern (an unguarded, unrecoverable failure on transfer to a party outside the caller's control): [5](#0-4) 

Because `protocolFeeRecipient`/`integratorFeeRecipient` are embedded in the maker's own order extension (which the maker fully controls when signing the order, with no on-chain validation against a fixed/whitelisted protocol address), an unprivileged order creator can set one of these to a contract that reverts on receiving native tokens (when `dstToken == address(0)`). Once a resolver fills such an order and funds the destination escrow, every subsequent `withdraw`/`publicWithdraw` call reverts because the fee leg fails before the maker's principal is ever transferred.

The only escape hatch is `cancel()`, which bypasses the fee split entirely and returns the full amount to the taker after `DstCancellation`: [6](#0-5) 

### Impact Explanation
This matches the bounty's Medium/High criteria: "temporary freezing of funds during the live swap lifecycle." For the full withdrawal window (`DstWithdrawal` → `DstCancellation`), the maker cannot receive the swapped tokens and the resolver's committed `takingAmount` + `safetyDeposit` are stuck in the escrow — the swap silently fails even though the resolver correctly deployed the destination escrow and the maker/taker otherwise behaved honestly with a valid secret. Recovery only happens via `cancel()` after the cancellation timelock, so the swap itself never completes and the resolver's capital is locked for the duration, which is a real (if bounded) economic loss/DoS triggered entirely by an unprivileged maker crafting a hostile order.

### Likelihood Explanation
Low-to-moderate: constructing such an order requires the "attacker" to be the maker (any unprivileged user can create and sign an order with arbitrary `extraData`), and requires a resolver to select/fill that order and fund a native-token (`dstToken == address(0)`) destination escrow. No privileged role is needed, and nothing in `_postInteraction` validates the fee-recipient addresses.

### Recommendation
- Validate/whitelist `protocolFeeRecipient` (and ideally `integratorFeeRecipient`) against a small set of trusted addresses rather than trusting arbitrary extension bytes, or
- Wrap the fee transfers in `_withdraw` with try/catch (or use `call` with a gas-limited, non-reverting pattern) so that a failing fee transfer cannot block delivery of the maker's principal and the safety deposit, degrading gracefully (e.g., accruing the failed fee for later pull-based claim) instead of reverting the whole withdrawal.

### Proof of Concept
1. Attacker (as maker) deploys a `Reverter` contract whose `receive()`/fallback reverts unconditionally.
2. Attacker signs a cross-chain order whose `extraData` sets `protocolFeeRecipient = address(Reverter)` (or `integratorFeeRecipient`), with `dstToken = address(0)` (native ETH) and non-zero fee amounts.
3. A resolver fills the order on the source chain and calls `EscrowFactory.createDstEscrow` on the destination chain, funding it with native ETH (`takingAmount + safetyDeposit`).
4. After `DstWithdrawal`, the resolver (or an access-token holder for `publicWithdraw`) calls `withdraw(secret, immutables)`; the call reverts inside `_uniTransfer -> _ethTransfer` at line `EscrowDst.sol:90` because `Reverter` rejects the ETH transfer, per `BaseEscrow._ethTransfer` (`NativeTokenSendingFailure`).
5. Every subsequent `withdraw`/`publicWithdraw` attempt reverts identically until `DstCancellation`, at which point the resolver must fall back to `cancel()` and forfeit the swap, having had its capital locked for the entire withdrawal window.

### Citations

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

**File:** contracts/libraries/ImmutablesLib.sol (L102-121)
```text
    function protocolFeeRecipientCd(IBaseEscrow.Immutables calldata immutables) external pure returns (Address ret) {
        bytes calldata parameters = immutables.parameters;
        if (parameters.length < 0x60) revert IndexOutOfRange();
        assembly ("memory-safe") {
            ret := calldataload(add(parameters.offset, 0x40))
        }
    }

    /**
     * @notice Returns the integrator fee recipient from the immutables (calldata version).
     * @param immutables The immutables to extract the recipient from.
     * @return ret The integrator fee recipient.
     */
    function integratorFeeRecipientCd(IBaseEscrow.Immutables calldata immutables) external pure returns (Address ret) {
        bytes calldata parameters = immutables.parameters;
        if (parameters.length < 0x80) revert IndexOutOfRange();
        assembly ("memory-safe") {
            ret := calldataload(add(parameters.offset, 0x60))
        }
    }
```

**File:** contracts/BaseEscrowFactory.sol (L67-90)
```text
    function _postInteraction(
        IOrderMixin.Order calldata order,
        bytes calldata extension,
        bytes32 orderHash,
        address taker,
        uint256 makingAmount,
        uint256 takingAmount,
        uint256 remainingMakingAmount,
        bytes calldata extraData
    ) internal override(FeeTaker) {
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
