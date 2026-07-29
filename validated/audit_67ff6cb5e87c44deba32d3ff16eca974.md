Confirmed: `_validateImmutables` only checks that the passed `Immutables` hash to the address computed via CREATE2, matching the deployed clone — it does not check whether the escrow's principal has already been withdrawn/cancelled. This confirms `rescueFunds` on `EscrowSrc` has no state tracking to distinguish "genuinely stuck" tokens from the live, still-owed maker principal.

### Title
Taker can use `rescueFunds` on `EscrowSrc` to steal the maker's principal instead of returning it via `cancel` - (File: `contracts/BaseEscrow.sol`)

### Summary
`BaseEscrow.rescueFunds` lets the address recorded as `immutables.taker` pull out any `token`/`amount` combination from the escrow clone once `block.timestamp >= immutables.timelocks.rescueStart(RESCUE_DELAY)`, sending funds to `msg.sender` (the taker) unconditionally [1](#0-0) . `rescueStart` is computed purely from the clone's deployment timestamp plus the immutable `RESCUE_DELAY` set at factory construction, independent of whether `cancel()` or `withdraw()` were ever called [2](#0-1) . The only defense against misuse, `_validateImmutables`, merely re-derives the CREATE2 address from the hash of the passed `Immutables` struct — it never checks the escrow's token balance state or whether the principal has already been distributed [3](#0-2) .

### Finding Description
On the source chain, `EscrowSrc._cancel` is the only function that returns the maker's locked principal back to the maker, and it can be called by the taker (or anyone, via `publicCancel`, holding the access token) once the cancellation timelock stage is reached [4](#0-3) [5](#0-4) . However, calling `cancel()` is not mandatory or automatic — it is a permissionless action that the taker (who is the only party who benefits from *not* calling it) can simply choose to skip.

If the taker never calls `withdraw`/`cancel`/`publicCancel` and the destination-side secret is never revealed (so no legitimate `withdraw` on the source occurs either), the maker's full principal sits untouched in the `EscrowSrc` clone through the withdrawal, public-withdrawal, cancellation, and public-cancellation windows. Once `RESCUE_DELAY` elapses from deployment — a value fixed at factory deployment (e.g. 7–8 days in tests/scripts) and unrelated to the per-order cancellation timelocks — the taker (the only address allowed to call `rescueFunds`, via the `onlyCaller(immutables.taker.get())` modifier) can call:

```
rescueFunds(immutables.token, immutables.amount, immutables)
```

This transfers the entire maker principal (`immutables.amount` of `immutables.token`) to the taker via `_uniTransfer` [6](#0-5) , exactly mirroring what `cancel()` would have sent to the maker, except the recipient is the taker instead. Nothing in the contract distinguishes "tokens that are accidentally stuck" (the documented intent of rescue, per README) from "the live escrow principal still owed to the maker."

### Impact Explanation
This is a direct theft of user (maker) funds at rest: the maker's original source-chain tokens, which the protocol's cancellation flow is designed to always return to the maker after the cancellation window, are instead permanently redirected to the taker. This matches the Critical severity bar ("direct theft of user funds at rest or in motion") under the bounty's required impacts.

### Likelihood Explanation
The attack requires only that the taker (an ordinary, unprivileged order-filler, not a governance/admin role) abstain from calling `cancel`/`publicCancel`/`withdraw` and instead wait out `RESCUE_DELAY`. Since the taker is financially incentivized to keep the full principal rather than return it to the maker for only a safety-deposit reward via `cancel`, this is a rational, always-available strategy any taker can execute unilaterally once they control an `EscrowSrc` clone (i.e., after normally filling an order). No secret leakage, governance compromise, or third-party trust failure is needed — only patience past `RESCUE_DELAY`.

### Recommendation
`rescueFunds` should not be able to release the "live" principal amount that the escrow was funded with for the swap. Options include: tracking whether the escrow has already been withdrawn/cancelled (e.g., a `withdrawn`/`cancelled` flag or checking actual remaining balance vs. expected residual) and disallowing `rescueFunds` from moving more than the "excess" balance above `immutables.amount` (+ `safetyDeposit` for native), or requiring `cancel`/`publicCancel` to be exhausted first before a *different*, receiver-favoring rescue/fallback path is available. At minimum, restrict `rescueFunds`'s allowed `amount` for `immutables.token` to strictly exceed the expected escrowed principal, so it can only recover truly incidental/stuck balances.

### Proof of Concept
1. Maker signs an order; taker fills it via the Limit Order Protocol, triggering `EscrowFactory.postInteraction`, which deploys and funds an `EscrowSrc` clone with `immutables.amount` of the maker's token plus the safety deposit.
2. Taker never calls `withdraw`, `cancel`, or `publicCancel` on the source clone, and never reveals the secret needed for a destination withdrawal (or simply never funds/uses the destination side meaningfully) — no external party can force cancellation.
3. Time passes: `srcTimelocks.withdrawal`, `publicWithdrawal`, `cancellation`, `publicCancellation` all elapse with the clone still fully funded, then `RESCUE_DELAY` also elapses (measured from `timelocks.setDeployedAt` at clone creation, see `rescueStart`) [7](#0-6) .
4. Taker calls `EscrowSrc(clone).rescueFunds(immutables.token, immutables.amount, immutables)`. `onlyCaller(immutables.taker.get())` passes because caller is the taker; `onlyValidImmutables` passes since the immutables are unchanged; `onlyAfter(rescueStart)` passes since `RESCUE_DELAY` has elapsed [1](#0-0) .
5. `_uniTransfer` sends the full `immutables.amount` of the maker's token to the taker, `emit FundsRescued(...)` fires, and the maker receives nothing — the exact tokens that `cancel()` was supposed to return to the maker have been stolen by the taker.

### Citations

**File:** contracts/BaseEscrow.sol (L71-90)
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

    /**
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

**File:** contracts/Escrow.sol (L21-28)
```text
    /**
     * @dev Verifies that the computed escrow address matches the address of this contract.
     */
    function _validateImmutables(bytes32 immutablesHash) internal view virtual override {
        if (Create2.computeAddress(immutablesHash, PROXY_BYTECODE_HASH, FACTORY) != address(this)) {
            revert InvalidImmutables();
        }
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
