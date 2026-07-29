## Analysis: Confirmed vulnerability

Tracing the source-escrow lifecycle confirms this is a valid finding, though the primary impact is fund-freeze griefing rather than "theft" of a foreign deposit — the safety deposit is native ETH the taker/resolver itself pre-funded to the deterministic escrow address before `_postInteraction` deployed it (see `BaseEscrowFactory._postInteraction` doc: "the caller must be whitelisted and pre-send the safety deposit in a native token to a pre-computed deterministic address"). [1](#0-0) 

The critical defect is that `rescueFunds` lets the taker unilaterally sweep the native `safetyDeposit` balance out-of-band from the normal `withdraw`/`cancel` flow, while `EscrowSrc._cancel` and `_withdrawTo` both unconditionally attempt to pay that same native amount to the caller as part of an atomic transaction that also returns the maker's/taker's ERC20 principal:

- `rescueFunds` is restricted to `onlyCaller(immutables.taker.get())` and gated only by `RESCUE_DELAY`, transferring arbitrary `token`/`amount` (here `address(0)`, `immutables.safetyDeposit`) straight to `msg.sender`. [2](#0-1) 

- `_cancel` sends the maker's ERC20 principal back to the maker **and then** sends `immutables.safetyDeposit` in native ETH to the caller in the same call, with `_ethTransfer` reverting the whole transaction (`NativeTokenSendingFailure`) if the contract's ETH balance is insufficient. [3](#0-2) [4](#0-3) 

- `_withdrawTo` has the identical pattern: ERC20 to `target`, then native `safetyDeposit` to `msg.sender`. [5](#0-4) 

Because `cancel`/`publicCancel`/`withdraw`/`publicWithdraw` all require this native transfer to succeed atomically alongside the ERC20 transfer, if the taker calls `rescueFunds(address(0), immutables.safetyDeposit, immutables)` after `RESCUE_DELAY` (instead of simply calling `cancel()`, which would have paid them the identical amount while also returning the maker's tokens), the escrow's native balance drops to zero. Every subsequent call to `cancel`/`publicCancel` (and `withdraw`/`publicWithdraw`, if a secret ever surfaces) will then revert on the `_ethTransfer` step, permanently trapping the maker's ERC20 principal in the escrow with no legitimate way to unstick it (short of an unrelated party voluntarily donating ETH to the clone). This matches the bounty's Medium/Critical criteria: "smart contract unable to operate because required token/native balances can be broken by an unprivileged actor," escalating to permanent freezing of the maker's principal.

Since a rational taker who merely wants their own deposit back has no incentive to prefer `rescueFunds` over `cancel` (both pay them the same amount, but only `cancel` unwinds the maker's side), the only reason to use this path is to deliberately grief/freeze the maker's funds — which is exactly the unprivileged, single-entrypoint attack pattern the question describes.

### Title
Taker can drain source-escrow safety deposit via `rescueFunds`, permanently bricking `cancel`/`withdraw` and freezing maker funds - (File: `contracts/BaseEscrow.sol`, `contracts/EscrowSrc.sol`)

### Summary
`BaseEscrow.rescueFunds` allows the taker to withdraw the native `safetyDeposit` from a live `EscrowSrc` clone after `RESCUE_DELAY`, independent of `cancel`/`withdraw`. Because `EscrowSrc._cancel`/`_withdrawTo` require that exact native balance to still be present to atomically complete their ERC20 transfer, an unprivileged taker can call `rescueFunds` first to strand the maker's ERC20 principal permanently.

### Finding Description
`_postInteraction` deploys `EscrowSrc` clones pre-funded by the taker with `immutables.safetyDeposit` native ETH. [6](#0-5) 
`cancel`/`publicCancel` and `withdraw`/`publicWithdraw` all pay out that same safety deposit to the caller as the second half of an atomic operation whose first half moves the ERC20 principal. [7](#0-6) 
`rescueFunds` has no awareness of this dependency: it lets the taker (`onlyCaller(immutables.taker.get())`) pull the native balance directly once `RESCUE_DELAY` elapses, with no check on whether `cancel`/`withdraw` has already occurred or whether doing so would break the escrow's remaining incentive/payout logic. [2](#0-1) 

### Impact Explanation
Once the native balance is drained, any subsequent `cancel`, `publicCancel`, `withdraw`, or `publicWithdraw` call reverts at `_ethTransfer` (`NativeTokenSendingFailure`), because these functions require the safety deposit to still be present. Since the ERC20 transfer and native transfer happen in the same atomic call, the maker's principal tokens become permanently unrecoverable — a Critical-tier permanent freeze of user funds, and independently a Medium-tier "contract unable to operate due to broken required native balance."

### Likelihood Explanation
The attacker is simply the order's taker/resolver — no privileged or governance role is needed, matching the in-scope unprivileged attacker model. The only precondition is that no other access-token holder calls `publicCancel` before `RESCUE_DELAY`, which is plausible for low-value or unremarkable orders, or can be intentionally engineered by the taker (e.g., by choosing a small `RESCUE_DELAY`/timelock configuration relative to public-cancellation windows or simply outracing any public canceller).

### Recommendation
`rescueFunds` should not be permitted to withdraw the native `safetyDeposit` amount while `cancel`/`withdraw` for that escrow has not yet been executed, or `_cancel`/`_withdrawTo` should decouple the ERC20 transfer from the native safety-deposit transfer (e.g., use a best-effort/non-reverting native transfer, or track/require only `min(balance, safetyDeposit)`), so a missing/rescued safety deposit cannot block release of the principal tokens.

### Proof of Concept
1. Maker signs an order; taker fills it, LOP calls `_postInteraction`, which deploys `EscrowSrc` pre-funded with `immutables.safetyDeposit` ETH and `immutables.amount` maker tokens. [8](#0-7) 
2. Taker never triggers `createDstEscrow`, so no secret is ever revealed; nobody calls `cancel`/`publicCancel` before `RESCUE_DELAY` elapses.
3. Taker calls `rescueFunds(address(0), immutables.safetyDeposit, immutables)`; `onlyCaller` and `onlyAfter(rescueStart)` pass, and the full native balance is sent to the taker. [2](#0-1) 
4. Any later call to `cancel()`/`publicCancel()` now reverts in `_ethTransfer` because the escrow's ETH balance is 0, leaving the maker's ERC20 principal permanently stuck in the clone. [3](#0-2)

### Citations

**File:** contracts/BaseEscrowFactory.sol (L47-52)
```text
    /**
     * @notice Creates a new escrow contract for maker on the source chain.
     * @dev The caller must be whitelisted and pre-send the safety deposit in a native token
     * to a pre-computed deterministic address of the created escrow.
     * The external postInteraction function call will be made from the Limit Order Protocol
     * after all funds have been transferred. See {IPostInteraction-postInteraction}.
```

**File:** contracts/BaseEscrowFactory.sol (L127-159)
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
            parameters: abi.encode(
                protocolFeeAmount,
                integratorFeeAmount,
                protocolFeeRecipient,
                integratorFeeRecipient
            )
        });

        emit SrcEscrowCreated(immutables, immutablesComplement);

        bytes32 salt = immutables.hashMem();
        address escrow = _deployEscrow(salt, 0, ESCROW_SRC_IMPLEMENTATION);
        if (escrow.balance < immutables.safetyDeposit || IERC20(order.makerAsset.get()).safeBalanceOf(escrow) < makingAmount) {
            revert InsufficientEscrowBalance();
        }
```

**File:** contracts/BaseEscrow.sol (L71-79)
```text
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

**File:** contracts/EscrowSrc.sol (L83-132)
```text
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

    /**
     * @dev Transfers ERC20 tokens to the target and native tokens to the caller.
     * @param secret The secret that unlocks the escrow.
     * @param target The address to transfer ERC20 tokens to.
     * @param immutables The immutable values used to deploy the clone contract.
     */
    function _withdrawTo(bytes32 secret, address target, Immutables calldata immutables)
        internal
        onlyValidImmutables(immutables.hash())
        onlyValidSecret(secret, immutables.hashlock)
    {
        IERC20(immutables.token.get()).safeTransfer(target, immutables.amount);
        _ethTransfer(msg.sender, immutables.safetyDeposit);
        emit EscrowWithdrawal(secret);
    }

    /**
     * @dev Transfers ERC20 tokens to the maker and native tokens to the caller.
     * @param immutables The immutable values used to deploy the clone contract.
     */
    function _cancel(Immutables calldata immutables)
        internal
        onlyValidImmutables(immutables.hash())
    {
        IERC20(immutables.token.get()).safeTransfer(immutables.maker.get(), immutables.amount);
        _ethTransfer(msg.sender, immutables.safetyDeposit);
        emit EscrowCancelled();
    }
```
