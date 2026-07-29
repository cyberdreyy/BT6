### Title
Missing minimum `safetyDeposit` enforcement lets the escrow-creating party disable the permissionless rescue incentive, risking funds frozen in `EscrowSrc`/`EscrowDst` - (File: `contracts/BaseEscrowFactory.sol`)

### Summary
The protocol's `publicWithdraw`/`publicCancel` rescue mechanism exists specifically so that if the privileged `taker` (resolver) becomes unresponsive or malicious, any `accessToken` holder can step in and complete the swap lifecycle, being paid the `safetyDeposit` as a reward. Neither `BaseEscrowFactory` nor `BaseEscrow`/`EscrowSrc`/`EscrowDst` enforce any minimum value for `safetyDeposit`. This is the exact same root cause as the referenced `minLoanSize = 0` finding: an economic incentive designed to guarantee permissionless cleanup can be set to zero (or dust) by an unprivileged party, removing any rational reason for a third party to perform the rescue action.

### Finding Description
`safetyDeposit` is packed by the taker-controlled `extraDataArgs.deposits` value during `_postInteraction` (source side) and is directly supplied by the taker as `msg.value` in `createDstEscrow` (destination side): [1](#0-0) 

Neither of these paths validates a minimum deposit amount before deploying the escrow clone — the only check performed is that the *balance* matches the declared `safetyDeposit`, not that the value is economically meaningful: [2](#0-1) 

The `safetyDeposit` is the sole reward paid to the caller of `publicWithdraw`/`publicCancel`: [3](#0-2) [4](#0-3) 

Both `withdraw`/`cancel` (private) are gated to `onlyCaller(taker)`, and `rescueFunds` is *also* gated to the same taker after `RESCUE_DELAY`: [5](#0-4) 

So the only non-taker-controlled path to free the escrow is `publicWithdraw`/`publicCancel`, which are permissionless to any `accessToken` holder but pay zero reward if `safetyDeposit` is set to 0 (or dust). If the taker who created the escrow simply never calls `withdraw`/`cancel`, no `accessToken` holder has an economic reason to pay gas to unlock someone else's funds.

### Impact Explanation
On the source chain, this can strand the maker's ERC20 tokens (already transferred into `EscrowSrc` before `postInteraction` runs) with no economically rational path to recovery: the taker's own `cancel`/`rescueFunds` are the only alternatives besides the disincentivized `publicCancel`. On the destination chain, the maker's expected `takingAmount` can be stuck in `EscrowDst` past the intended windows for the same reason. This matches the bounty's "temporary freezing of funds during the live swap lifecycle" (High) bucket, and in the worst case (taker permanently unresponsive, no altruistic caller ever appears) approaches indefinite freezing, since `rescueFunds` is also gated to that same non-cooperating taker.

### Likelihood Explanation
The safety deposit is fully attacker-controlled data — a resolver preparing extension data for a Fusion order, or calling `createDstEscrow` directly, can set `deposits`/`safetyDeposit` to `0` at no extra cost, with the transaction otherwise looking like an ordinary swap. No validation anywhere in `BaseEscrowFactory`, `BaseEscrow`, `EscrowSrc`, or `EscrowDst` rejects a zero/negligible deposit, so exploitation requires no privileged role, no race condition, and no unusual gas cost — only a deliberately crafted safety-deposit value.

### Recommendation
Enforce a protocol-level minimum `safetyDeposit` (absolute or relative to gas cost of `publicWithdraw`/`publicCancel`) in `_postInteraction` and `createDstEscrow`, reverting if the declared/deposited amount is below the floor, mirroring the acknowledged fix in the referenced report (deploy with a "reasonable `minLoanSize`" analog for safety deposits).

### Proof of Concept
1. Resolver crafts order extension data (or calls `createDstEscrow`) with `safetyDeposit = 0` in `extraDataArgs.deposits` and/or as `msg.value` for the destination escrow.
2. `_postInteraction`/`createDstEscrow` deploy the escrow clone without any minimum-deposit check (`contracts/BaseEscrowFactory.sol:127-185`).
3. Resolver (taker) never calls `withdraw`/`cancel` during the private windows.
4. Once public windows open, `publicWithdraw`/`publicCancel` remain technically callable by any `accessToken` holder, but pay `_ethTransfer(msg.sender, immutables.safetyDeposit)` of `0` (`contracts/EscrowSrc.sol:111-119`, `contracts/EscrowDst.sol:79-96`), so no rational third party calls them.
5. Maker's locked assets remain stuck; only the same uncooperative taker can call `rescueFunds` after `RESCUE_DELAY` (`contracts/BaseEscrow.sol:71-79`).

### Citations

**File:** contracts/BaseEscrowFactory.sol (L127-144)
```text
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
```

**File:** contracts/BaseEscrowFactory.sol (L155-185)
```text
        bytes32 salt = immutables.hashMem();
        address escrow = _deployEscrow(salt, 0, ESCROW_SRC_IMPLEMENTATION);
        if (escrow.balance < immutables.safetyDeposit || IERC20(order.makerAsset.get()).safeBalanceOf(escrow) < makingAmount) {
            revert InsufficientEscrowBalance();
        }
    }

    /**
     * @notice See {IEscrowFactory-createDstEscrow}.
     */
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

**File:** contracts/EscrowSrc.sol (L68-103)
```text
    function publicWithdraw(bytes32 secret, Immutables calldata immutables)
        external
        onlyAccessTokenHolder()
        onlyAfter(immutables.timelocks.get(TimelocksLib.Stage.SrcPublicWithdrawal))
        onlyBefore(immutables.timelocks.get(TimelocksLib.Stage.SrcCancellation))
    {
        _withdrawTo(secret, immutables.taker.get(), immutables);
    }

    /**
     * @notice See {IBaseEscrow-cancel}.
     * @dev The function works on the time intervals highlighted with capital letters:
     * ---- contract deployed --/-- finality --/-- private withdrawal --/-- public withdrawal --/--
     * --/-- PRIVATE CANCELLATION --/-- PUBLIC CANCELLATION ----
     */
    function cancel(Immutables calldata immutables)
        external
        onlyCaller(immutables.taker.get())
        onlyAfter(immutables.timelocks.get(TimelocksLib.Stage.SrcCancellation))
    {
        _cancel(immutables);
    }

    /**
     * @notice See {IEscrowSrc-publicCancel}.
     * @dev The function works on the time intervals highlighted with capital letters:
     * ---- contract deployed --/-- finality --/-- private withdrawal --/-- public withdrawal --/--
     * --/-- private cancellation --/-- PUBLIC CANCELLATION ----
     */
    function publicCancel(Immutables calldata immutables)
        external
        onlyAccessTokenHolder()
        onlyAfter(immutables.timelocks.get(TimelocksLib.Stage.SrcPublicCancellation))
    {
        _cancel(immutables);
    }
```

**File:** contracts/EscrowDst.sol (L50-73)
```text
    function publicWithdraw(bytes32 secret, Immutables calldata immutables)
        external
        onlyAccessTokenHolder()
        onlyAfter(immutables.timelocks.get(TimelocksLib.Stage.DstPublicWithdrawal))
        onlyBefore(immutables.timelocks.get(TimelocksLib.Stage.DstCancellation))
    {
        _withdraw(secret, immutables);
    }

    /**
     * @notice See {IBaseEscrow-cancel}.
     * @dev The function works on the time interval highlighted with capital letters:
     * ---- contract deployed --/-- finality --/-- private withdrawal --/-- public withdrawal --/-- PRIVATE CANCELLATION ----
     */
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

**File:** contracts/BaseEscrow.sol (L63-79)
```text
    modifier onlyAccessTokenHolder() {
        if (_ACCESS_TOKEN.balanceOf(msg.sender) == 0) revert InvalidCaller();
        _;
    }

    /**
     * @notice See {IBaseEscrow-rescueFunds}.
     */
    function rescueFunds(address token, uint256 amount, Immutables calldata immutables)
        external
        onlyCaller(immutables.taker.get())
        onlyValidImmutables(immutables.hash())
        onlyAfter(immutables.timelocks.rescueStart(RESCUE_DELAY))
    {
        _uniTransfer(token, msg.sender, amount);
        emit FundsRescued(token, amount);
    }
```
