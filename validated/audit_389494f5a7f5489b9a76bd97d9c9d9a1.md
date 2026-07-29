## Analysis

The LP-fee report's root cause—**a fee that is only computed and enforced at one entry point, but not enforced at a second, functionally-equivalent entry point**—maps directly onto how integrator/protocol fees are handled between the source and destination chains in this codebase.

### Root cause

On the source chain, `_postInteraction` computes `integratorFeeAmount`/`protocolFeeAmount` from the order's fee configuration and merely **emits** them inside `DstImmutablesComplement.parameters` via `SrcEscrowCreated` — it does not lock them on-chain anywhere that a later step must honor: [1](#0-0) 

The actual destination escrow is deployed later, in a **separate transaction**, via `createDstEscrow`, whose `dstImmutables` (including `amount`, `token`, and — critically — the ABI-encoded `parameters` field holding `protocolFeeAmount`/`integratorFeeAmount`/recipients) is supplied entirely by the caller with **no on-chain check against the `SrcEscrowCreated` event data**: [2](#0-1) 

`EscrowDst._withdraw` then trusts these self-declared `parameters` bytes verbatim to decide how much (if anything) goes to the protocol/integrator recipients before paying the remainder to the maker: [3](#0-2) 

The `parameters` bytes are read via `ImmutablesLib` accessors that just `mload` fixed offsets from whatever calldata the deploying caller chose — no comparison to the fee amounts computed during `_postInteraction` exists anywhere in the contracts: [4](#0-3) 

### Why this matches the "avoidable fee" pattern

Just like `increaseLiquidity` lets a caller add funds to a UniV3 position without going through the fee-charging `lock` path, `createDstEscrow` lets a caller (any resolver/taker, not a privileged role) fund a destination escrow with **arbitrary self-chosen fee-split parameters**, completely decoupled from the fee percentages that were actually computed for that order during `_postInteraction`. A resolver can set `protocolFeeAmount = 0` and `integratorFeeAmount = 0` in `dstImmutables.parameters` while still funding the escrow with the full expected `amount`, so `_withdraw` pays 100% to the maker and 0% to the protocol/integrator fee recipients — this is most impactful for a self-fill scenario where the same party controls both maker and taker/resolver roles, letting them capture 100% of what should have been protocol/integrator fee revenue.

This differs from the report's UNCX conclusion ("not in our control, also possible on `NonfungiblePositionManager`") because here the entire mechanism — fee computation in `_postInteraction`, escrow creation in `createDstEscrow`, and fee payout in `EscrowDst._withdraw` — is 1inch's own production code, not a third-party contract.

### Title
Destination-chain protocol/integrator fees are self-declared and unenforced, allowing a taker to bypass fee collection - (File: `contracts/BaseEscrowFactory.sol`, `contracts/EscrowDst.sol`)

### Summary
`createDstEscrow` accepts a caller-supplied `Immutables.parameters` field encoding `protocolFeeAmount`, `integratorFeeAmount`, and their recipients, with no on-chain validation against the fee amounts computed and emitted during the source-chain `_postInteraction` call. `EscrowDst._withdraw` blindly trusts this field when splitting funds, so any resolver deploying the destination escrow can set both fee amounts to zero and cause 100% of the deposited taking amount to go to the maker, permanently denying the protocol and integrator their fee.

### Finding Description
`_postInteraction` on `BaseEscrowFactory.sol` (lines 67-160) computes `integratorFeeAmount`/`protocolFeeAmount` for informational purposes only, embedding them in the `SrcEscrowCreated` event's `DstImmutablesComplement.parameters`. There is no storage commitment, hash lock, or later cross-check tying these values to what is actually used when the destination escrow is created. `createDstEscrow` (lines 165-185) takes `dstImmutables` fully from `msg.sender` and deploys the clone using `immutables.hashMem()` as salt — the fee split is baked into the deterministic address but never validated against the source chain. `EscrowDst._withdraw` (`contracts/EscrowDst.sol` lines 79-96) reads `integratorFeeAmountCd()`/`protocolFeeAmountCd()` straight out of this attacker-chosen `parameters` blob and pays whatever is declared (including zero) to the fee recipients, sending the remainder to the maker.

### Impact Explanation
This falls under the Medium/High bounty impact "theft or permanent loss of unclaimed fee-like value": whoever calls `createDstEscrow` (an ordinary, unprivileged resolver/taker) can permanently deny the protocol and integrator their fee on any given swap by simply encoding zero fee amounts in `dstImmutables.parameters`, most profitably when the same actor controls both the maker and taker/resolver side of the swap (self-fill), capturing the entire taking amount that should have been split with fee recipients.

### Likelihood Explanation
Likelihood is high for any resolver willing to self-fill their own orders (permissionless once outside the private whitelist window), since no on-chain guard prevents mismatched fee parameters between the source-side computation and the destination-side escrow deployment; the only defense is an off-chain relayer/maker check that is not enforced by the contracts themselves.

### Recommendation
Commit the fee split (amount, recipients) computed in `_postInteraction` to on-chain state (e.g., a mapping keyed by `orderHash`) and require `createDstEscrow` to read/validate against that commitment rather than trusting caller-supplied `parameters`, or otherwise cryptographically bind the destination `parameters` field to the source-side `DstImmutablesComplement.parameters` so it cannot be altered by the escrow-creating caller.

### Proof of Concept
1. Resolver (optionally also acting as maker) fills an order via LOP; `_postInteraction` computes `protocolFeeAmount`/`integratorFeeAmount` and emits `SrcEscrowCreated` with these values in `dstImmutablesComplement.parameters`.
2. The same resolver calls `createDstEscrow` on the destination chain, submitting `dstImmutables.parameters = abi.encode(0, 0, protocolFeeRecipient, integratorFeeRecipient)` while funding `dstImmutables.amount` equal to the full expected taking amount.
3. Once the secret is revealed, `EscrowDst._withdraw` computes `integratorFeeAmount = 0`, `protocolFeeAmount = 0`, and transfers the entire `immutables.amount` to the maker, per `contracts/EscrowDst.sol` lines 84-93 — the protocol and integrator receive nothing despite the fee having been "charged" in the source-chain fee computation.

### Citations

**File:** contracts/BaseEscrowFactory.sol (L139-153)
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

        emit SrcEscrowCreated(immutables, immutablesComplement);
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

**File:** contracts/libraries/ImmutablesLib.sol (L19-43)
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
