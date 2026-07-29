## Verdict: Valid vulnerability

### Title
Taker can prematurely drain the native safety deposit via `rescueFunds`, permanently freezing the maker's principal in `EscrowSrc` - (File: `contracts/BaseEscrow.sol`, `contracts/EscrowSrc.sol`)

### Summary
`BaseEscrow.rescueFunds` lets the `taker` pull out *any* token/amount from the escrow clone once `RESCUE_DELAY` has elapsed, with no accounting for what balance is still owed to the withdraw/cancel flow. Because `EscrowSrc._withdrawTo` and `EscrowSrc._cancel` unconditionally try to push out `immutables.safetyDeposit` in native coin as part of releasing the maker's/taker's principal, a taker who calls `rescueFunds(address(0), immutables.safetyDeposit, immutables)` first can permanently break both `withdraw`/`withdrawTo` and `cancel`/`publicCancel`, since the contract's native balance will then be insufficient and the mandatory `_ethTransfer` will revert. This freezes the maker's ERC20 principal in the escrow with no other exit path.

### Finding Description
`rescueFunds` is gated only by: [1](#0-0) 
It is `onlyCaller(immutables.taker.get())` and `onlyAfter(rescueStart)`, but it performs a blind `_uniTransfer(token, msg.sender, amount)` — there is no check that `token`/`amount` correspond to "excess"/foreign funds rather than the escrow's own accounted safety deposit or principal.

Every source-side exit path that finalizes the swap always also sends the *entire* `immutables.safetyDeposit` in native coin to the caller: [2](#0-1) [3](#0-2) 

`cancel()` has no upper time bound (only `onlyAfter(SrcCancellation)`), so once `RESCUE_DELAY` has passed the cancellation window is normally already open too: [4](#0-3) 

If the taker (who is the only address allowed to call `rescueFunds`) calls `rescueFunds(address(0), immutables.safetyDeposit, immutables)` first, the contract's native balance drops to (near) zero. Any subsequent call to `withdraw`, `withdrawTo`, `publicWithdraw`, `cancel`, or `publicCancel` will still attempt `_ethTransfer(msg.sender, immutables.safetyDeposit)`, which will now fail (`success == false`) and revert the whole call via `NativeTokenSendingFailure()`: [5](#0-4) 

Since none of these functions has a path that skips the safety-deposit transfer, none of them can ever succeed again — the maker's ERC20 principal (and, in the withdraw case, the taker's own entitlement) becomes permanently stuck in the clone.

### Impact Explanation
This is not merely "who gets the safety deposit" (the taker was already the intended recipient of it via `cancel`/`withdraw` in the private window). The real impact is that the taker can unilaterally and irreversibly disable the only functions capable of moving the escrowed ERC20 principal, permanently freezing the maker's funds in the source escrow. This matches the "permanent freezing of funds" / "smart contract unable to operate because required token/native balances can be broken by an unprivileged actor" impact tiers. Additionally, since `rescueFunds` places no restriction on `token`/`amount`, the same taker could subsequently (or in the same transaction batch) also call `rescueFunds(immutables.token.get(), immutables.amount, immutables)` to directly steal the maker's principal outright, which is outright theft of user funds rather than just griefing.

### Likelihood Explanation
Requires only that `RESCUE_DELAY` elapse and that the taker (a normal, unprivileged swap participant, not an owner/admin/governance role) act — no collusion, no special permissions, no chain-congestion assumption is even strictly necessary beyond reaching `RESCUE_DELAY`. This is straightforward to trigger by any taker who wants to grief a swap or extract extra value.

### Recommendation
`rescueFunds` should not be able to touch balances still owed under the escrow's own accounting (the `safetyDeposit` and `amount` fields of `immutables`). Either: (1) explicitly disallow `token == address(0) && amount <= immutables.safetyDeposit` and `token == immutables.token.get()`-with-amount-up-to-`immutables.amount` from being rescued, or (2) track/settle the intended obligations before allowing arbitrary rescue, or (3) make the `withdraw`/`cancel` safety-deposit transfer resilient to insufficient balance (e.g., transfer `min(balance, safetyDeposit)` and don't revert on shortfall) so a partial native balance drain cannot brick the principal transfer.

### Proof of Concept
1. Deploy an `EscrowSrc` clone for a partial fill (~50%) with a native `safetyDeposit`.
2. Let time pass so `RESCUE_DELAY` elapses without anyone calling `withdraw`/`cancel`/`publicWithdraw`/`publicCancel`.
3. As `taker`, call `rescueFunds(address(0), immutables.safetyDeposit, immutables)` — this succeeds and drains the escrow's native balance to the taker.
4. Any subsequent call to `withdraw`, `withdrawTo`, `cancel`, or `publicCancel` with correct immutables/secret now reverts with `NativeTokenSendingFailure()` because `_ethTransfer(msg.sender, immutables.safetyDeposit)` fails against the drained balance.
5. The maker's ERC20 principal held by the clone is now permanently unreachable through any exposed function.

### Citations

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

**File:** contracts/EscrowSrc.sol (L111-119)
```text
    function _withdrawTo(bytes32 secret, address target, Immutables calldata immutables)
        internal
        onlyValidImmutables(immutables.hash())
        onlyValidSecret(secret, immutables.hashlock)
    {
        IERC20(immutables.token.get()).safeTransfer(target, immutables.amount);
        _ethTransfer(msg.sender, immutables.safetyDeposit);
        emit EscrowWithdrawal(secret);
    }
```

**File:** contracts/EscrowSrc.sol (L125-132)
```text
    function _cancel(Immutables calldata immutables)
        internal
        onlyValidImmutables(immutables.hash())
    {
        IERC20(immutables.token.get()).safeTransfer(immutables.maker.get(), immutables.amount);
        _ethTransfer(msg.sender, immutables.safetyDeposit);
        emit EscrowCancelled();
    }
```
