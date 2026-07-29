### Title
Unvalidated fee amounts in `createDstEscrow` cause permanent underflow-revert of `EscrowDst.withdraw`/`publicWithdraw`, defeating the public-safety fallback and enabling theft of the maker's source-side funds - (File: `contracts/BaseEscrowFactory.sol`, `contracts/EscrowDst.sol`)

### Summary
`createDstEscrow` in `BaseEscrowFactory.sol` is a fully permissionless entry point that accepts an entire `Immutables` struct — including the `parameters` field encoding `protocolFeeAmount` and `integratorFeeAmount` — directly from the caller, with **no validation that `protocolFeeAmount + integratorFeeAmount <= amount`**. This is the exact same class of bug as the reported Compound `DAIInterestRateModel` issue: an externally-influenced numeric input is subtracted without a bounds check, and any downstream call that performs the subtraction will unconditionally revert.

### Finding Description
On the source side, `_postInteraction` explicitly guards against this exact condition: [1](#0-0) 

```
(uint256 integratorFeeAmount, uint256 protocolFeeAmount, bytes calldata tail) = FeeTaker._getFeeAmounts(...);
if (integratorFeeAmount + protocolFeeAmount >= takingAmount) revert InvalidFeeAmounts();
```

However, `createDstEscrow` never re-derives or validates these fee amounts — it simply forwards whatever `Immutables` (including the raw `parameters` bytes) the caller supplies: [2](#0-1) 

There is no check on `dstImmutables.parameters` at all: only `msg.value` (safety deposit + amount if native) and the timelock ordering are validated. Since `Immutables.hashMem()` (used as the CREATE2 salt) hashes the full struct including `parameters`, the deployed clone is permanently bound to whatever fee values were supplied.

When the secret is later revealed, `EscrowDst._withdraw` performs an unchecked subtraction of these attacker-supplied fee values from `immutables.amount`: [3](#0-2) 

```
uint256 amount = immutables.amount - integratorFeeAmount - protocolFeeAmount;
```

If `integratorFeeAmount + protocolFeeAmount > immutables.amount`, this line underflows and reverts under Solidity 0.8's built-in overflow checks. Because both `withdraw` (`onlyCaller(taker)`) and `publicWithdraw` (`onlyAccessTokenHolder()`) call the same internal `_withdraw`, **both the private and the public-safety withdrawal paths are permanently broken** for that specific escrow — there is no way to fix the immutables post-deployment since they are hash-bound.

The only remaining exit is `cancel()`, which does not perform this subtraction and returns the full `immutables.amount` to the taker (not the maker) after the cancellation timelock: [4](#0-3) 

### Impact Explanation
The `publicWithdraw` mechanism exists specifically as a trustless safety net so that even if the taker/resolver disappears or misbehaves after obtaining the secret, any access-token holder can force settlement of the maker's destination funds. This bug lets an unprivileged caller of `createDstEscrow` (in practice the resolver/taker itself, since they fund and control this call) poison that specific escrow's immutables so this safety net can never fire. A malicious taker can: (1) call `createDstEscrow` with `protocolFeeAmount + integratorFeeAmount > amount`, funding the escrow normally so it appears correctly balance-funded off-chain; (2) obtain the secret from the maker (who is relying on the public-withdraw guarantee as protection); (3) use that secret to withdraw the maker's tokens from `EscrowSrc` (which has no such fee-sum bug); (4) never (and no one else can) successfully call `EscrowDst.withdraw`/`publicWithdraw`, since both revert; (5) reclaim its own deposited destination tokens via `cancel()` once the cancellation timelock passes. Net effect: the maker's source funds are taken while the maker never receives the corresponding destination funds — direct theft of user funds, matching a Critical impact under the bounty scope.

### Likelihood Explanation
`createDstEscrow` is `external payable` with no whitelist or role check on the caller, and is one of the explicitly named scoped entry points ("Destination path: `createDstEscrow` -> timelock binding -> ... -> destination withdraw/cancel flow"). Crafting `parameters` with an inflated fee sum requires no special access — it is pure calldata the attacker fully controls. The only precondition is that off-chain observers (maker/relayer) do not independently re-validate `protocolFeeAmount`/`integratorFeeAmount` against `amount` before releasing the secret, which the contracts themselves do nothing to enforce on-chain, unlike the symmetric `_postInteraction` check on the source side.

### Recommendation
Add the same guard used in `_postInteraction` to `createDstEscrow`:
```
if (dstImmutables.protocolFeeAmount() + dstImmutables.integratorFeeAmount() >= dstImmutables.amount) revert InvalidFeeAmounts();
```
using the existing `ImmutablesLib.protocolFeeAmountCd`/`integratorFeeAmountCd` accessors, so a malformed fee configuration is rejected at creation time rather than causing a permanent revert at withdrawal time. Additionally, consider having `EscrowDst._withdraw` use a saturating/checked computation that reverts explicitly with a clear error at withdrawal-eligible states, or better, disallow the ambiguity entirely by validating at deployment.

### Proof of Concept
1. Attacker (acting as taker) computes `dstImmutables` with `amount = 100`, `parameters = abi.encode(protocolFeeAmount = 60, integratorFeeAmount = 60, protocolFeeRecipient, integratorFeeRecipient)` (sum = 120 > 100).
2. Attacker calls `createDstEscrow(dstImmutables, srcCancellationTimestamp)` with `msg.value = safetyDeposit` (+`amount` if native) and, if ERC20, has approved the factory for `amount` — this succeeds; no check rejects the fee sum.
   - [2](#0-1) 
3. Maker, believing the escrow is funded correctly (balance == `amount` is true), reveals the secret to the taker as per protocol expectation.
4. Attacker calls `EscrowSrc.withdraw(secret, ...)` on the source chain to claim the maker's `makingAmount` — succeeds, no fee-sum check exists there for withdrawal.
5. Anyone attempts `EscrowDst.withdraw` / `publicWithdraw` on the destination escrow — reverts due to underflow at `immutables.amount - integratorFeeAmount - protocolFeeAmount` (100 - 60 - 60 underflows).
   - [5](#0-4) 
6. Maker never receives destination funds; attacker recovers their own deposited destination funds via `cancel()` after the cancellation timelock, having already taken the maker's source funds.

**Note on confidence:** I was not able to inspect the full off-chain resolver/relayer trust assumptions (e.g., whether `ResolverExample.sol` or any front-end logic independently validates `parameters` before secret disclosure) beyond what's in the indexed contracts, since some auxiliary files were only partially available in the index. This does not affect the on-chain root cause identified above, but it's worth confirming with a Devin session that has full repository access if a stricter off-chain mitigation already exists in deployment tooling.

### Citations

**File:** contracts/BaseEscrowFactory.sol (L84-92)
```text
        (uint256 integratorFeeAmount, uint256 protocolFeeAmount, bytes calldata tail) = FeeTaker._getFeeAmounts(
            order,
            taker,
            takingAmount,
            makingAmount,
            extraData[:superArgsLength]
        );

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
