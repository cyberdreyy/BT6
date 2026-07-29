## Analysis

The scoped code confirms this exploit path is real given the current contracts.

**Root cause:** `BaseEscrowFactory.createDstEscrow` accepts `dstImmutables` (including the arbitrary `parameters` field) directly from `msg.sender` with **no validation** of its length or of the fee amounts encoded inside it: [1](#0-0) 

This differs from the source-chain path (`_postInteraction`), which explicitly rejects bad fee totals via `if (integratorFeeAmount + protocolFeeAmount >= takingAmount) revert InvalidFeeAmounts();` — no equivalent check exists for the destination escrow: [2](#0-1) 

**Trigger point:** Both `withdraw` and `publicWithdraw` on `EscrowDst` route unconditionally to `_withdraw`, which decodes fees from `immutables.parameters` before any transfer: [3](#0-2) 

`integratorFeeAmountCd` requires `parameters.length >= 0x40` and reverts with `IndexOutOfRange` otherwise; `protocolFeeAmountCd` requires `>= 0x20`: [4](#0-3) 

So a `parameters` blob of exactly 32 bytes (only `protocolFeeAmount` present) makes `integratorFeeAmountCd()` always revert — this alone blocks both `withdraw` and `publicWithdraw` unconditionally, regardless of a valid secret. Separately, even with a full 0x80-byte `parameters` blob, if `protocolFeeAmount + integratorFeeAmount > immutables.amount`, the line `uint256 amount = immutables.amount - integratorFeeAmount - protocolFeeAmount;` underflows and panics (Solidity 0.8 checked arithmetic), again reverting unconditionally in both `withdraw` and `publicWithdraw`.

**Recovery path:** Critically, `cancel()` does **not** decode `parameters` at all — it transfers the full `immutables.amount` straight to `immutables.taker`: [5](#0-4) 

So the destination-escrow creator (the taker/resolver who called `createDstEscrow` and funded it) can deliberately craft a malformed `parameters` field to permanently block the maker from ever withdrawing (via either the private `withdraw` or the public `publicWithdraw`/access-token-holder path), while retaining the ability to reclaim the escrowed destination-side funds for themselves once `DstCancellation` is reached via `cancel()`, which bypasses fee decoding entirely.

This satisfies the "unprivileged actor entering through `createDstEscrow`" attacker model in the scope, does not rely on any owner/admin/governance/privileged-resolver assumption, and matches the "Temporary freezing of funds" / griefing pattern (High) since the maker's destination payout is stuck for the swap lifecycle while the taker can unilaterally reclaim the funds after cancellation — an asymmetric outcome that favors the escrow creator at the maker's expense.

---

### Title
Malformed/insufficient `parameters` in `createDstEscrow` bricks both private and public destination withdrawal, freezing maker funds - (File: contracts/EscrowDst.sol, contracts/BaseEscrowFactory.sol)

### Summary
`createDstEscrow` lets any unprivileged caller supply an arbitrary `parameters` byte blob with no length or fee-sum validation. `EscrowDst._withdraw` (used by both `withdraw` and `publicWithdraw`) unconditionally decodes fee fields from `parameters` via `ImmutablesLib`, which reverts on too-short `parameters` (`IndexOutOfRange`) or panics on underflow if `protocolFeeAmount + integratorFeeAmount > amount`. This permanently blocks all withdrawal paths, while `cancel()` (unaffected by `parameters`) still lets the escrow creator/taker reclaim the escrowed funds after the cancellation timelock.

### Finding Description
`createDstEscrow` (contracts/BaseEscrowFactory.sol:165-185) copies caller-supplied `dstImmutables`, including `parameters`, into the deployed clone's hash/state with zero validation of its length or encoded values. `EscrowDst._withdraw` (contracts/EscrowDst.sol:79-96) always calls `immutables.integratorFeeAmountCd()` and `immutables.protocolFeeAmountCd()` before performing any transfer. `ImmutablesLib` (contracts/libraries/ImmutablesLib.sol:76-95) reverts with `IndexOutOfRange` when `parameters.length` is insufficient for the requested field, and the subsequent `immutables.amount - integratorFeeAmount - protocolFeeAmount` subtraction panics on underflow when the decoded fees exceed `amount`. Since both `withdraw` and `publicWithdraw` share `_withdraw`, both the private, time-boxed path and the "public" access-token-holder fallback path are bricked identically. `cancel()` does not touch `parameters`, so the escrow creator (taker) retains a working exit once the private/public withdrawal window elapses.

### Impact Explanation
The maker can never receive the destination-side payout for a funded escrow even with a valid secret, while the taker who deployed the escrow can still reclaim the deposited funds via `cancel()` after `DstCancellation`. This is a temporary freeze of the maker's expected funds during the live swap lifecycle, and it is entirely triggerable by the unprivileged party that calls `createDstEscrow`, with the taker uniquely benefiting from the resulting asymmetry.

### Likelihood Explanation
High — no privileged role, governance action, or unusual gas/oracle conditions are required. The malformed `parameters` blob is trivially constructed by whoever calls `createDstEscrow` (the taker), since the function performs no sanity checks on it.

### Recommendation
Validate `dstImmutables.parameters` inside `createDstEscrow` (fixed expected length, e.g. exactly `0x80` bytes, and `protocolFeeAmount + integratorFeeAmount < amount`) before deploying the clone, mirroring the `InvalidFeeAmounts` check already used in `_postInteraction` for the source path.

### Proof of Concept
1. Attacker (as taker) calls `createDstEscrow` with `dstImmutables.parameters = abi.encode(uint256)` (32 bytes only), funding the escrow normally.
2. Time warps to `DstWithdrawal`; maker or resolver calls `withdraw(secret, immutables)` — reverts with `IndexOutOfRange` from `integratorFeeAmountCd`.
3. Time warps to `DstPublicWithdrawal`; any access-token holder calls `publicWithdraw(secret, immutables)` — reverts identically, confirming the "invariant to test" (public path should still finalize) is broken.
4. Time warps to `DstCancellation`; attacker (taker) calls `cancel(immutables)` — succeeds and reclaims `immutables.amount` plus the safety deposit, while the maker never received any destination funds.

### Citations

**File:** contracts/BaseEscrowFactory.sol (L92-92)
```text
        if (integratorFeeAmount + protocolFeeAmount >= takingAmount) revert InvalidFeeAmounts();
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

**File:** contracts/EscrowDst.sol (L64-73)
```text
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

**File:** contracts/libraries/ImmutablesLib.sol (L76-95)
```text
    function protocolFeeAmountCd(IBaseEscrow.Immutables calldata immutables) external pure returns (uint256 ret) {
        bytes calldata parameters = immutables.parameters;
        if (parameters.length < 0x20) revert IndexOutOfRange();
        assembly ("memory-safe") {
            ret := calldataload(parameters.offset)
        }
    }

    /**
     * @notice Returns the integrator fee amount from the immutables (calldata version).
     * @param immutables The immutables to extract the fee from.
     * @return ret The integrator fee amount.
     */
    function integratorFeeAmountCd(IBaseEscrow.Immutables calldata immutables) external pure returns (uint256 ret) {
        bytes calldata parameters = immutables.parameters;
        if (parameters.length < 0x40) revert IndexOutOfRange();
        assembly ("memory-safe") {
            ret := calldataload(add(parameters.offset, 0x20))
        }
    }
```
