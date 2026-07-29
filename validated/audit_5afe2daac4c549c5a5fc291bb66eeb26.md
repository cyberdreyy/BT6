## Title
Maker-controlled `makerAsset` token can permanently block `EscrowSrc.cancel`/`publicCancel`, freezing the resolver's native safety deposit until `RESCUE_DELAY` — (File: `contracts/EscrowSrc.sol`)

### Summary
`EscrowSrc`'s cancellation path performs an unconditional `IERC20.safeTransfer` of `immutables.token` to the maker before releasing the resolver's native safety deposit, and `immutables.token` is set directly from the maker-supplied `order.makerAsset` with no validation or whitelist. A malicious maker can use a token contract that reverts on the outbound transfer back to itself, permanently blocking both the resolver's private `cancel` and the permissionless `publicCancel`, freezing the resolver's ETH safety deposit inside the escrow clone until the long-dated `RESCUE_DELAY` elapses. This mirrors the external report's root cause: an unprivileged actor supplies an unrestricted `IERC20` address that is used in a refund/cancel code path, causing that path to revert forever.

### Finding Description
`BaseEscrowFactory._postInteraction` builds the `EscrowSrc` immutables directly from the signed order with no token allow-listing: [1](#0-0) 

`EscrowSrc._cancel`, invoked by both the resolver-only `cancel` and the permissionless `publicCancel`, transfers `immutables.token` to the maker and only then releases the caller's safety deposit: [2](#0-1) 

Because both the token transfer and the safety-deposit `_ethTransfer` occur in the same function/transaction, a maker who deploys a malicious ERC20 as `makerAsset` — one that allows the initial `safeTransferFrom` into the escrow to succeed (so the order fills normally) but reverts specifically on the later `safeTransfer` back to the maker during cancellation — makes `_cancel` unconditionally revert. This blocks:
- The resolver's private `cancel()` (their only intended path to reclaim the native safety deposit and give the maker's tokens back).
- Any other resolver's `publicCancel()` during the public cancellation window (which also relies on `_cancel`).

The only remaining recovery path is `BaseEscrow.rescueFunds`, callable solely by the taker after `RESCUE_DELAY` has elapsed: [3](#0-2) 

`rescueFunds` lets the taker specify an arbitrary `token`/`amount`, so the taker can eventually rescue the native safety deposit (`token == address(0)`), but only after waiting the full `RESCUE_DELAY`, which is a long, deployment-configured period. During that entire window the resolver's native safety deposit is locked in the malicious maker's escrow clone with no way to exit, and normal cancel/publicCancel functionality for that swap is permanently broken.

### Impact Explanation
This matches the "temporary freezing of funds during the live swap lifecycle" category: an unprivileged maker, by choosing a hostile `makerAsset`, can force a resolver's native safety deposit to be locked for the duration of `RESCUE_DELAY` and disables the intended private/public cancellation mechanism for that escrow entirely. It can be repeated cheaply for every order the malicious maker creates that gets filled, griefing multiple resolvers and consuming their capital/gas with no on-chain mitigation, exactly as in the analog report where uncancelable orders wasted user/protocol resources.

### Likelihood Explanation
Any unprivileged user acting as a maker can deploy a custom ERC20 and set it as `order.makerAsset` when signing a Fusion order — no code path in `BaseEscrowFactory` or `EscrowSrc` restricts or whitelists `makerAsset`. As long as a resolver fills such an order (the malicious token can be crafted to allow the initial `safeTransferFrom` into the escrow while reverting only on the return transfer to the maker), the described DoS is triggered with certainty during cancellation. This requires no privileged role and no race condition — it is a deterministic consequence of the contract's token-transfer ordering in `_cancel`.

### Recommendation
- Do not gate the resolver's safety-deposit release on the success of the maker-token transfer; separate the two transfers so a reverting/malicious `tokenIn`/`makerAsset` cannot block safety-deposit recovery (e.g., use low-level calls with try/catch for the token leg, or let the caller reclaim the safety deposit even if the token transfer fails, recording the token amount as rescuable).
- Alternatively, allow the resolver to skip/isolate a failing token transfer (pull-based accounting) rather than reverting the whole `cancel`/`publicCancel` call.
- Consider shortening or adding an emergency, permissionless safety-deposit-only reclaim path independent of `RESCUE_DELAY` for cases where the token leg specifically fails.

### Proof of Concept
1. Maker (Alice) deploys `EvilToken`, an ERC20 whose `transfer`/`transferFrom` succeeds when called by the Limit Order Protocol (funding the escrow) but reverts when the escrow (`msg.sender == FACTORY`-deployed clone) tries to `safeTransfer` back to Alice's own address.
2. Alice signs an order with `makerAsset = EvilToken`.
3. A resolver (Bob) sends the safety deposit to the deterministic `EscrowSrc` address and fills the order via `fillOrderArgs` → `postInteraction`, per `BaseEscrowFactory._postInteraction` (contracts/BaseEscrowFactory.sol:127-159); the escrow is funded successfully since the inbound transfer works.
4. After the cancellation timelock, Bob calls `EscrowSrc.cancel(immutables)`; `_cancel` reverts at the `IERC20(...).safeTransfer(immutables.maker.get(), immutables.amount)` line (contracts/EscrowSrc.sol:129), reverting the whole transaction and blocking safety-deposit recovery.
5. `publicCancel` by any other resolver during the public cancellation window fails for the same reason.
6. Bob's only recovery is `rescueFunds(address(0), safetyDeposit, immutables)` after waiting the full `RESCUE_DELAY` (contracts/BaseEscrow.sol:71-79), during which his native deposit remains frozen and the escrow's intended cancel mechanics stay permanently broken.

### Citations

**File:** contracts/BaseEscrowFactory.sol (L127-137)
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
