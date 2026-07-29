## Title
Cancel path in `EscrowSrc` permanently reverts and locks maker funds + taker safety deposit if the maker's address is on the token's blacklist (e.g. USDC) - (File: contracts/EscrowSrc.sol)

### Summary
`EscrowSrc.cancel()` and `EscrowSrc.publicCancel()` both route through `_cancel()`, which unconditionally does `IERC20(token).safeTransfer(maker_, amount)` before returning the safety deposit to the caller [1](#0-0) . Unlike the withdrawal flow, which exposes `withdrawTo(secret, target, immutables)` allowing the caller to redirect funds to an alternate address [2](#0-1) , there is no equivalent "cancelTo" option for the cancellation flow. If the maker's address is (or becomes) blacklisted by a compliant ERC20 like USDC, the `safeTransfer` to `maker_` will revert unconditionally, and since both `cancel()` and `publicCancel()` share the same `_cancel()` internal function [3](#0-2) , neither the private-cancellation caller (taker) nor anyone during the public-cancellation window can ever successfully cancel the escrow.

### Finding Description
This is the same root cause as the referenced Sherlock report (M-6, nounsdao `Stream.cancel()`): a payout function combines a mandatory token transfer to a specific address with the state-changing/authorization logic in a single atomic call, so if that recipient's address is unable to receive the token (blacklist), the entire operation permanently reverts.

In this codebase:
- `EscrowSrc._cancel()` calls `IERC20(immutables.token.get()).safeTransfer(immutables.maker.get(), immutables.amount)` then sends the native safety deposit to `msg.sender` [1](#0-0) .
- `cancel()` is restricted to `onlyCaller(immutables.taker.get())` during the private cancellation window [4](#0-3) , and `publicCancel()` is open to any access-token holder during the public cancellation window [5](#0-4) . Both paths call the exact same `_cancel()` that always attempts to pay the fixed `maker` address, so there is no way to route around a blacklisted maker.
- The only remaining exit is `BaseEscrow.rescueFunds()`, restricted to `onlyCaller(immutables.taker.get())` and only callable after `RESCUE_DELAY` [6](#0-5) . Critically, `rescueFunds` sends the rescued token to `msg.sender` (the taker), not to the maker — so once the cancellation path is permanently blocked, the maker's principal tokens can eventually be extracted by the taker themselves via `rescueFunds`, converting a temporary DoS into an outright loss of the maker's escrowed principal.

### Impact Explanation
- Before `RESCUE_DELAY`: `EscrowSrc` funds (maker's principal token) and the taker's safety-deposit native tokens become permanently stuck for that swap instance, because neither `cancel()` nor `publicCancel()` can complete — this matches the "temporary freezing of funds during the live swap lifecycle" criterion (High).
- After `RESCUE_DELAY`: because `rescueFunds` sends the token balance to the taker instead of back to the maker, the maker's principal can be diverted away from its rightful owner, which is a loss of user funds tied directly to this production contract's payout logic.

### Likelihood Explanation
This requires only that the `maker` address used in the `Immutables` ends up on the underlying ERC20's blacklist (a state condition of the token/maker address, not a privileged protocol role) while an `EscrowSrc` for that maker is still open. No governance, admin, or privileged-resolver assumptions are needed — any unprivileged user who fills an order as maker and is later blacklisted (or is deliberately using a blacklisted address as `maker` to grief the taker who has already locked a safety deposit for that escrow) triggers the condition. Both `cancel()` and `publicCancel()` are always subject to it since they share `_cancel()`.

### Recommendation
Decouple the authorization/state transition from the token payout: on cancellation, record the amount owed to the maker (or emit a claimable balance) rather than performing a hard `safeTransfer` inline, and let the maker claim later via a separate function (mirroring the recommended fix in the referenced report), or allow a `cancelTo`-style redirect / pull-payment fallback so a stuck transfer to one blacklisted address cannot block the entire cancellation and safety-deposit-return flow for the taker.

### Proof of Concept
1. Maker fills a Fusion order; `EscrowSrc` clone is deployed holding `MAKING_AMOUNT` of USDC and the taker's `SRC_SAFETY_DEPOSIT` in native token, as in `test/unit/Escrow.t.sol`'s `_prepareDataSrc` flow.
2. Off-chain, the maker's address gets added to USDC's blacklist (e.g., via Circle compliance action) before the secret is shared or timelocks progress to cancellation.
3. Timelock advances past `SrcCancellation`; taker calls `EscrowSrc.cancel(immutables)` → `_cancel()` → `IERC20(usdc).safeTransfer(maker_, amount)` reverts because `maker_` is blacklisted, per USDC's `transfer` blacklist check — the whole transaction (including the safety-deposit refund to the taker) reverts.
4. Timelock advances past `SrcPublicCancellation`; any access-token holder calls `publicCancel(immutables)` → same `_cancel()` → same revert.
5. Both the maker's `MAKING_AMOUNT` USDC and the taker's `SRC_SAFETY_DEPOSIT` remain locked in the `EscrowSrc` clone indefinitely; after `RESCUE_DELAY`, the taker can call `rescueFunds(address(usdc), MAKING_AMOUNT, immutables)` and receive the maker's principal directly, as demonstrated by the existing `test_RescueFundsSrc` pattern [7](#0-6) , but here diverted from the intended maker.

### Citations

**File:** contracts/EscrowSrc.sol (L53-60)
```text
    function withdrawTo(bytes32 secret, address target, Immutables calldata immutables)
        external
        onlyCaller(immutables.taker.get())
        onlyAfter(immutables.timelocks.get(TimelocksLib.Stage.SrcWithdrawal))
        onlyBefore(immutables.timelocks.get(TimelocksLib.Stage.SrcCancellation))
    {
        _withdrawTo(secret, target, immutables);
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

**File:** contracts/BaseEscrow.sol (L68-79)
```text
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

**File:** test/unit/Escrow.t.sol (L273-281)
```text

        // rescue
        vm.warp(block.timestamp + RESCUE_DELAY);
        vm.expectEmit();
        emit IBaseEscrow.FundsRescued(address(usdc), SRC_SAFETY_DEPOSIT);
        swapData.srcClone.rescueFunds(address(usdc), SRC_SAFETY_DEPOSIT, swapData.immutables);
        assertEq(usdc.balanceOf(bob.addr), balanceBob + MAKING_AMOUNT + SRC_SAFETY_DEPOSIT);
        assertEq(usdc.balanceOf(address(swapData.srcClone)), 0);
    }
```
