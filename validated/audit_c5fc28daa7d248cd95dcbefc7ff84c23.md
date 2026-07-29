## Title
Blacklisted maker/taker permanently DoSes `cancel`/`withdraw` refund path, letting the counter‑party steal the stuck funds via `rescueFunds` - (File: `contracts/EscrowSrc.sol`, `contracts/EscrowDst.sol`, `contracts/BaseEscrow.sol`)

### Summary
The reported BendDAO bug class (blacklist-token DoS on a "return excess funds to owner" transfer) has a direct, and more severe, analog in this repo: the unconditional, single-recipient `safeTransfer`/`_uniTransfer` calls inside `EscrowSrc._cancel`, `EscrowSrc.publicCancel`, and `EscrowDst._withdraw`/`cancel`. If the recipient (maker or taker) is blacklisted by the swapped ERC20 (e.g. USDC), the refund/withdraw call permanently reverts, and after `RESCUE_DELAY` the counter-party (an unprivileged resolver acting as `taker`) can call `rescueFunds` to redirect those same tokens to itself.

### Finding Description
- `EscrowSrc._cancel` transfers `immutables.amount` to `immutables.maker.get()` with a plain `safeTransfer`; both `cancel` and `publicCancel` call it unconditionally: [1](#0-0) 
- `EscrowDst._withdraw` transfers the remaining amount to `immutables.maker.get()` via `_uniTransfer`, and this is the only path (`withdraw`/`publicWithdraw`) that ever completes the swap: [2](#0-1) 
- `_uniTransfer` in `BaseEscrow` just does `IERC20(token).safeTransfer(to, amount)` with no try/catch, so a blacklisted `to` makes the whole call revert: [3](#0-2) 
- `rescueFunds` is gated only by `onlyCaller(immutables.taker.get())` and a time check (`rescueStart = deployedAt + RESCUE_DELAY`), with **no check that `amount`/`token` is actually "excess" or unrelated to the escrowed swap funds** — it can rescue any token/amount sitting in the clone, including the exact swap tokens that are stuck because the recipient is blacklisted: [4](#0-3) [5](#0-4) 

Attack/failure flow (source chain example, unprivileged actors only):
1. Maker fills a Fusion order; `EscrowSrc` clone is funded with `MAKING_AMOUNT` of the src ERC20 (e.g. USDC).
2. Maker becomes blacklisted by the USDC issuer for any reason (unrelated regulatory action) — no privileged/admin action inside this protocol is required, matching the "unprivileged actor" model since neither party controls the token's blacklist.
3. Taker never reveals the secret (or maker/taker dispute), so at `SrcCancellation` the intended recovery path is `cancel()`/`publicCancel()` → `_cancel()` → `safeTransfer(maker, amount)`, which now **always reverts** because `maker` is blacklisted. Every account (including `publicCancel`, callable by any access-token holder) trying to return the maker's funds is DOSed indefinitely.
4. After `RESCUE_DELAY` elapses from deployment (`rescueStart`), the escrow clone still holds the src tokens. `taker` — an ordinary, unprivileged resolver — calls `rescueFunds(srcToken, MAKING_AMOUNT, immutables)`, which is authorized purely by `onlyCaller(taker)` and the elapsed-time check, with zero validation that these are "stray" tokens rather than the maker's principal. `_uniTransfer` sends the maker's tokens straight to the taker. [6](#0-5) 

The same pattern applies symmetrically on the destination chain: if the maker is blacklisted by the dst token, `EscrowDst.withdraw`/`publicWithdraw` can never succeed (line 93 above), the dst funds intended for the maker sit in the clone, and after `RESCUE_DELAY` the taker can `rescueFunds` them away.

### Impact Explanation
This is Critical under the bounty's "direct theft of user funds at rest" definition: an unprivileged counter-party (the resolver acting as `taker`, not an owner/governance/privileged role) ends up permanently receiving tokens that legitimately belong to the maker, purely because the escrow's fund-return functions have a single hardcoded recipient with no fallback and `rescueFunds` has no restriction preventing it from paying out the very funds that were supposed to be returned to the other party. Even absent the theft step, the DoS alone (funds unrecoverable by the legitimate owner) is a "permanent freezing of funds" impact.

### Likelihood Explanation
Requires only that a party's address is blacklisted by the underlying ERC20 (a real, documented condition for USDC/USDT-class tokens) and that the swap subsequently needs a cancel/withdraw refund to that blacklisted address — plausible any time a swap fails to complete via secret reveal. No admin/governance action, malicious relayer, or bridge assumption is needed; the "attacker" role is simply the counter-party resolver waiting out `RESCUE_DELAY`, which is squarely in-scope as an unprivileged actor exploiting the live withdraw/cancel/rescueFunds flow.

### Recommendation
- Wrap the recipient transfers in `EscrowSrc._cancel`, `EscrowDst._withdraw`, and `EscrowDst.cancel` in a try/catch (or pull-payment pattern) so a reverting/blacklisted recipient does not brick the whole cancel/withdraw call; on failure, escrow the funds for later claim by the intended recipient via an alternate address.
- Restrict `rescueFunds` so it cannot be used to withdraw the exact `immutables.token`/`immutables.amount` that represents the still-unresolved swap principal (e.g., track and exclude the escrowed principal from rescuable balance, or require the position be resolved via a fallback claim first).

### Proof of Concept
1. Deploy `EscrowSrc` clone with `maker = M`, `token = USDC`, `amount = MAKING_AMOUNT`.
2. Have the USDC issuer blacklist `M` (simulated in test with a mock USDC that has `blacklist(address)`).
3. Warp past `SrcCancellation`; call `cancel(immutables)` — reverts every time (`safeTransfer` to blacklisted `M` reverts): [1](#0-0) 
4. Warp past `rescueStart` (`deployedAt + RESCUE_DELAY`); as `taker`, call `rescueFunds(address(usdc), MAKING_AMOUNT, immutables)` — succeeds, transferring `M`'s tokens to `taker`: [6](#0-5) 
5. Assert `usdc.balanceOf(taker)` increased by `MAKING_AMOUNT` while `M` never regains the funds.

### Citations

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

**File:** contracts/BaseEscrow.sol (L82-90)
```text
     * @dev Transfers ERC20 or native tokens to the recipient.
     */
    function _uniTransfer(address token, address to, uint256 amount) internal {
        if (token == address(0)) {
            _ethTransfer(to, amount);
        } else {
            IERC20(token).safeTransfer(to, amount);
        }
    }
```

**File:** contracts/libraries/TimelocksLib.sol (L58-67)
```text
    /**
     * @notice Returns the start of the rescue period.
     * @param timelocks The timelocks to get the rescue delay from.
     * @return The start of the rescue period.
     */
    function rescueStart(Timelocks timelocks, uint256 rescueDelay) internal pure returns (uint256) {
        unchecked {
            return rescueDelay + (Timelocks.unwrap(timelocks) >> _DEPLOYED_AT_OFFSET);
        }
    }
```
