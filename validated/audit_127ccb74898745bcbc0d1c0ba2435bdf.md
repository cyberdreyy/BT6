### Title
Malicious order maker can permanently block `EscrowSrc.cancel`/`publicCancel` by using a token-blacklisted maker address, freezing the resolver's safety deposit and locked maker tokens - (File: `contracts/EscrowSrc.sol`)

### Summary
`EscrowSrc._cancel` unconditionally `safeTransfer`s the locked maker tokens back to `immutables.maker.get()` before releasing the resolver's native safety deposit to the caller. Because `immutables.maker` is chosen entirely by the (unprivileged) order maker at order-signing time and is baked into the immutable, hash-validated `Immutables` struct for the lifetime of the escrow, a maker who signs an order from an address that is (or later becomes) blacklisted by the source token (e.g. USDC/USDT-style `Blacklistable`) can make every `cancel`/`publicCancel` call on `EscrowSrc` permanently revert. This is the same root cause as the referenced Predy finding: an attacker-controlled, unprivileged-set recipient address that a blacklisting ERC20 refuses to receive tokens from, used to defeat a critical liquidity-recovery code path (there: liquidation; here: cancellation).

### Finding Description
`EscrowSrc._cancel`: [1](#0-0) 

sends `immutables.amount` of the source token to `immutables.maker.get()` unconditionally, then forwards the native safety deposit to `msg.sender`. Both `cancel` (private, callable only by taker) and `publicCancel` (callable by anyone holding the access token) route through this same `_cancel` function: [2](#0-1) 

`immutables.maker` originates from `order.maker`, which is fully controlled by the unprivileged user who signs the Fusion order in `BaseEscrowFactory._postInteraction`: [3](#0-2) 

Because the `Immutables` struct (including `maker`) is hashed and re-validated on every call via `onlyValidImmutables`/`_validateImmutables` (`Create2.computeAddress` check against `FACTORY`), there is no mechanism to change the maker address after the escrow is deployed — unlike Predy's mutable `vault.recipient`, this value is fixed for the life of the clone, but it is fixed to a value the attacker chose from the start.

If the maker signs the order using an address that the source token issuer has blacklisted (or that later gets blacklisted while the swap is pending — a scenario entirely plausible for stablecoins like USDC/USDT), then:
- `EscrowSrc.withdraw`/`withdrawTo`/`publicWithdraw` are unaffected, since they send the maker's tokens to the taker/target, not to the maker — so a resolver who already has the secret can still complete a normal fill.
- `EscrowSrc.cancel` and `EscrowSrc.publicCancel` will always revert on the `safeTransfer` to the blacklisted maker, and because that call happens before the safety-deposit `_ethTransfer`, the whole transaction (including the safety-deposit refund to the canceller) reverts.

The only remaining recovery path is `BaseEscrow.rescueFunds`, callable exclusively by the taker after `RESCUE_DELAY` has elapsed from deployment: [4](#0-3) 

Until `RESCUE_DELAY` elapses, the resolver's native safety deposit (and the maker's own locked source tokens) are unrecoverable — a pure griefing outcome for the resolver, who bears the cost of depositing capital into an escrow whose maker deliberately weaponized their own address.

### Impact Explanation
This fits the bounty's High-severity category of "temporary freezing of funds during the live swap lifecycle": an unprivileged actor (the order maker) can, at order-creation time, guarantee that `cancel`/`publicCancel` on the resulting `EscrowSrc` clone will always revert, locking the resolver's native safety deposit (and the maker's own escrowed tokens) for the full duration up to `RESCUE_DELAY`. The resolver has committed real capital (the safety deposit, sent pre-`postInteraction` to the deterministic clone address) before it can determine the maker will never be able to receive a refund, and cannot mitigate mid-flight since the `Immutables` are hash-locked. This is reachable purely from filling a live, signed order — no owner/governance/resolver privilege is required from the attacker (maker) side.

### Likelihood Explanation
Likelihood is moderate: it requires the source token to implement an issuer-controlled blacklist (true for USDC and USDT, both commonly supported "quote"/settlement assets in cross-chain swap flows) and requires the maker to use/obtain a blacklisted address. A malicious maker can trivially achieve this by using an address they know is already blacklisted (e.g., a previously sanctioned address they control) as the order's `maker`/signer, since nothing in `_postInteraction` or escrow deployment checks the maker's eligibility to receive the source token back.

### Recommendation
- Before relying on `cancel`/`publicCancel` returning funds to `maker`, consider a pull-based refund pattern (e.g., record a withdrawable balance for the maker rather than pushing tokens), so a reverting transfer cannot block the safety-deposit release to the canceller.
- Alternatively, decouple the safety-deposit transfer from the token transfer (e.g., attempt the maker token transfer with try/catch or perform the safety-deposit transfer first / in a separate call), so a blacklisted-maker griefing vector cannot freeze the resolver's own capital.
- Consider allowing `rescueFunds`-style recovery for the *canceller's* safety deposit independent of whether the maker's token leg succeeds.

### Proof of Concept
1. Malicious user signs a Fusion order with `maker` = an address already blacklisted by the source token (e.g. USDC's blacklist), using `_prepareDataSrc`-style order construction as in `test/unit/EscrowCancel.t.sol`.
2. A resolver fills the order via `escrowFactory.postInteraction(...)`, pre-funding the deterministic `EscrowSrc` clone with the safety deposit and source tokens, as shown in the existing test flow: [5](#0-4) 
3. Time advances past `srcTimelocks.cancellation`; the resolver (or any access-token holder via `publicCancel`) calls `cancel`/`publicCancel`.
4. `_cancel`'s `IERC20(...).safeTransfer(immutables.maker.get(), immutables.amount)` reverts because `maker` is blacklisted by the token, reverting the entire cancellation, including the safety-deposit refund that would otherwise go to the caller: [6](#0-5) 
5. Neither `cancel` nor `publicCancel` can ever succeed for this escrow; funds remain frozen until `RESCUE_DELAY` elapses and the taker calls `rescueFunds`.

### Citations

**File:** contracts/EscrowSrc.sol (L83-103)
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
```

**File:** contracts/EscrowSrc.sol (L121-132)
```text
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

**File:** test/unit/EscrowCancel.t.sol (L127-145)
```text
    function test_CancelPublicSrc() public {
        // deploy escrow
        CrossChainTestLib.SwapData memory swapData = _prepareDataSrc(true, false);

        (bool success,) = address(swapData.srcClone).call{ value: SRC_SAFETY_DEPOSIT }("");
        assertEq(success, true);
        usdc.transfer(address(swapData.srcClone), MAKING_AMOUNT);

        vm.prank(address(limitOrderProtocol));
        escrowFactory.postInteraction(
            swapData.order,
            "", // extension
            swapData.orderHash,
            bob.addr, // taker
            MAKING_AMOUNT,
            TAKING_AMOUNT,
            0, // remainingMakingAmount
            swapData.extraData
        );
```
