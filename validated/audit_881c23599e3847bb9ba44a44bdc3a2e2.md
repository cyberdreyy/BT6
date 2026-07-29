### Title
Malicious order maker can permanently DoS `EscrowSrc.cancel`/`publicCancel` with an ERC-777 (or ERC-1820-registered) src token, freezing the resolver's safety deposit until `RESCUE_DELAY` - (File: `contracts/EscrowSrc.sol`)

### Summary
`EscrowSrc._cancel` sends the locked src-chain tokens back to `immutables.maker` and only afterwards releases the resolver's native safety deposit to the caller. Because the maker address and the src token are both chosen by the (unprivileged) order maker, a malicious maker can pick a token that supports ERC-1820/ERC-777-style `tokensReceived` hooks and register a hook on their own maker address that unconditionally reverts. Every call to `cancel`/`publicCancel` will then revert in the same transaction that also tries to pay out the resolver's safety deposit, blocking the resolver from reclaiming it for the entire `SrcCancellation`→`RESCUE_DELAY` window.

### Finding Description
`_cancel` performs the token transfer and the safety-deposit transfer in a single atomic call: [1](#0-0) 

`_uniTransfer` routes ERC20 transfers through `IERC20.safeTransfer`, which internally is just a `call` to `transfer(to, amount)`: [2](#0-1) 

Neither `_uniTransfer` nor `EscrowSrc` restricts which ERC20-compatible token can be used as the src consideration asset, and there is no token whitelist in this codebase (the only whitelist found is a resolver/fee-discount whitelist in `BaseEscrowFactory.sol`, not a token whitelist). If the src token implements the ERC-1820/ERC-777 receiver-hook pattern, a `transfer`/`transferFrom` call to the maker's address will invoke `tokensReceived` on any contract the maker has registered as an implementer for their own address, exactly as described in the referenced report. Since the maker fully controls both the token choice and their own receiving address at order-creation time (an unprivileged, no-permission action), they can register a hook that always reverts.

Because `_cancel` executes the token transfer to `maker` *before* `_ethTransfer(msg.sender, immutables.safetyDeposit)`, a revert in the hook reverts the whole transaction — `cancel()` (taker-only) and `publicCancel()` (anyone holding the access token) both fail identically, since the hook reverts unconditionally regardless of caller.

The only working exit for the resolver is `rescueFunds`, which sends both the token and the safety deposit to `msg.sender` (the taker itself), bypassing the malicious maker's hook entirely: [3](#0-2) 

but that path is gated by `rescueStart(RESCUE_DELAY)`, which is computed from the clone's deployment timestamp, independent of and normally much later than `SrcCancellation`: [4](#0-3) 

### Impact Explanation
This does not cause permanent loss — `rescueFunds` eventually returns both the src token and the resolver's native safety deposit to the resolver (`msg.sender == taker`) once `RESCUE_DELAY` elapses. However, from `SrcCancellation`/`SrcPublicCancellation` until `RESCUE_DELAY`, the resolver's own native safety deposit is provably frozen by an unprivileged maker's choice of token/hook, with no way for the resolver (or any public-cancel caller) to unlock it earlier. This matches the bounty's High-severity criterion "temporary freezing of funds during the live swap lifecycle," since the safety deposit is the resolver's at-risk capital committed for the swap and its release is delayed well beyond the intended cancellation window by an unprivileged actor's action.

### Likelihood Explanation
Likelihood is moderate: it requires the maker to (a) select or convince a resolver to accept an ERC-777/ERC-1820-hookable token as the src consideration asset, and (b) register a reverting `tokensReceived` implementer for their own maker address before order fill. Nothing in `EscrowSrc`, `Escrow`, `BaseEscrow`, or `BaseEscrowFactory` restricts the src token type, so this is fully reachable through the normal, unprivileged order-fill → `EscrowFactory.postInteraction` → `EscrowSrc` clone flow described in the docs.

### Recommendation
- Disallow/whitelist tokens with ERC-1820/ERC-777 hook support as valid src (and dst) consideration tokens, or
- Reorder `_cancel` (and `_withdraw`/`_withdrawTo`) so the safety-deposit native transfer to `msg.sender` happens before the (potentially hook-triggering) ERC20 transfer to the maker/taker, or wrap the token transfer in a try/catch so a hook revert cannot block the safety-deposit payout, or
- Shorten the effective gap between `SrcCancellation`/`SrcPublicCancellation` and `rescueStart`, or allow the taker to invoke `rescueFunds`-style self-payout immediately once cancellation conditions are met instead of waiting for the separate `RESCUE_DELAY`.

### Proof of Concept
1. Maker deploys `MaliciousReceiver`, registers it via ERC-1820 as the `tokensReceived` implementer for its own address, and has that hook always `revert()`.
2. Maker signs a Fusion order using an ERC-777-compatible token as the src asset, with `immutables.maker` set to `MaliciousReceiver`.
3. Resolver fills the order via `postInteraction`, deploying the `EscrowSrc` clone and depositing `SRC_SAFETY_DEPOSIT` in native token plus the src ERC-777 tokens.
4. Maker never reveals the secret; time advances past `SrcCancellation`.
5. Resolver calls `EscrowSrc.cancel(immutables)` — the ERC-777 token transfer to `MaliciousReceiver` triggers `tokensReceived`, which reverts, reverting the whole `cancel()` call (and equally `publicCancel()` by any access-token holder).
6. The resolver's safety deposit and the maker's src tokens remain locked in the clone until `block.timestamp >= rescueStart(RESCUE_DELAY)`, at which point the resolver must call `rescueFunds` twice (once for the token, once for `address(0)` safety deposit) to recover funds to themselves — well after the intended `SrcCancellation` release point.

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

**File:** contracts/BaseEscrow.sol (L84-90)
```text
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
