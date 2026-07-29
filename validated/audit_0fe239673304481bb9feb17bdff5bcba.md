I found a solid structural analog to the M-23 pattern. Let me confirm the missing validation in `createDstEscrow`.Confirmed: `createDstEscrow` in `contracts/BaseEscrowFactory.sol` accepts an arbitrary `IBaseEscrow.Immutables` struct from any caller with **no validation** that `protocolFeeAmount + integratorFeeAmount <= amount`, unlike `_postInteraction`, which enforces `if (integratorFeeAmount + protocolFeeAmount >= takingAmount) revert InvalidFeeAmounts();` [1](#0-0)  before creating the corresponding source-side data. The destination path has no equivalent guard [2](#0-1) [3](#0-2) , and `EscrowDst._withdraw` performs an unchecked subtraction `immutables.amount - integratorFeeAmount - protocolFeeAmount` that reverts on underflow if the fee sum exceeds the amount [4](#0-3) .

### Title
Missing fee-sum validation in `createDstEscrow` causes underflow-revert DoS on `EscrowDst.withdraw`/`publicWithdraw` - (File: contracts/BaseEscrowFactory.sol)

### Summary
`createDstEscrow` deploys an `EscrowDst` clone using an `Immutables` struct supplied entirely by the caller (`dstImmutables`), including the `parameters` field that encodes `protocolFeeAmount` and `integratorFeeAmount`. Unlike the source-side `_postInteraction` path, which explicitly rejects fee sums that meet or exceed the swap amount (`InvalidFeeAmounts`), `createDstEscrow` performs no equivalent check. This mirrors the M-23 root cause: one code path validates a subtraction's safety (`isHoldAmountAllowed`-style check in `_postInteraction`), while the parallel path performing the actual subtraction (`isSubAmountAllowed`-style logic in `EscrowDst._withdraw`) has no matching guard.

### Finding Description
`_postInteraction` (source escrow creation) computes `integratorFeeAmount`/`protocolFeeAmount` via `FeeTaker._getFeeAmounts` and explicitly guards against fee sums that would exceed `takingAmount`:
```solidity
if (integratorFeeAmount + protocolFeeAmount >= takingAmount) revert InvalidFeeAmounts();
``` [1](#0-0) 

However, `createDstEscrow` is a separately callable, permissionless entrypoint that takes the full `Immutables` struct (including `parameters`, which packs `protocolFeeAmount`/`integratorFeeAmount`/recipients) directly as calldata from the caller, with zero validation of internal consistency between `amount` and the encoded fee amounts:
```solidity
function createDstEscrow(IBaseEscrow.Immutables calldata dstImmutables, uint256 srcCancellationTimestamp) external payable {
    ...
    bytes32 salt = immutables.hashMem();
    address escrow = _deployEscrow(salt, msg.value, ESCROW_DST_IMPLEMENTATION);
    if (token != address(0)) {
        IERC20(token).safeTransferFrom(msg.sender, escrow, immutables.amount);
    }
    emit DstEscrowCreated(escrow, immutables.hashlock, immutables.taker);
}
``` [2](#0-1) 

Once deployed, any attempt to `withdraw`/`publicWithdraw` from that specific `EscrowDst` instance executes the unchecked subtraction:
```solidity
uint256 amount = immutables.amount - integratorFeeAmount - protocolFeeAmount;
``` [5](#0-4) 

If `integratorFeeAmount + protocolFeeAmount > immutables.amount` (a value fully controlled by whoever called `createDstEscrow`, since `Immutables` is passed as calldata and hashed/validated only against itself via `onlyValidImmutables`, not cross-checked against `SrcEscrowCreated`'s `DstImmutablesComplement.parameters`), this line reverts on arithmetic underflow (Solidity 0.8 checked arithmetic) for every call to `withdraw` and `publicWithdraw`, for the entire withdrawal window.

### Impact Explanation
This matches the Medium-severity criterion: "smart contract unable to operate because required token/native balances can be broken by an unprivileged actor." Any unprivileged caller of `createDstEscrow` (it has no whitelist restriction, unlike `_postInteraction`'s `postInteraction`/`FeeTaker.OnlyWhitelistOrAccessToken` check) can deploy a destination escrow whose fee accounting is broken by construction, making `withdraw`/`publicWithdraw` permanently revert on that escrow for the entire withdrawal period. The only remaining exit is `cancel`/`publicCancel` after the cancellation timelock, which returns funds to the `taker` field set in the same corrupted immutables — so the deployment itself is non-functional for its intended purpose during the live swap window (temporary freezing during the live swap lifecycle).

I was unable to fully verify, within available tool budget, whether the off-chain resolver/relayer matching logic (outside this repo) cross-checks a created `EscrowDst`'s fee `parameters` against the `DstImmutablesComplement` emitted in `SrcEscrowCreated` before revealing secrets — that off-chain behavior is out of scope for this repository, but it's material to whether a third party could ever be tricked into treating a malformed dst escrow as the legitimate counterpart to a real order. Absent that off-chain verification, the practical effect of this bug is limited to whichever party (typically the resolver themselves) funds and deploys the malformed escrow, i.e., it is more likely a self-inflicted misconfiguration hazard than a directed griefing vector against a passive third party, since no on-chain mechanism forces a resolver to use validated fee parameters.

### Likelihood Explanation
Likelihood is Medium: this requires a caller (typically a resolver) to supply fee parameters that were not derived from `_postInteraction`'s validated `FeeTaker._getFeeAmounts`/`InvalidFeeAmounts` check — e.g., a resolver's own off-chain integration bug, a malicious integrator-controlled fee parameter, or a rounding mismatch between systems. Because `createDstEscrow` is permissionless and takes raw `Immutables` calldata, nothing on-chain prevents this state from being reached.

### Recommendation
Add an explicit check in `createDstEscrow` mirroring the `_postInteraction` guard:
```solidity
uint256 integratorFeeAmount = dstImmutables.integratorFeeAmountCd();
uint256 protocolFeeAmount = dstImmutables.protocolFeeAmountCd();
if (integratorFeeAmount + protocolFeeAmount >= dstImmutables.amount) revert InvalidFeeAmounts();
```
before deploying the escrow, so the destination path enforces the same invariant as the source path, preventing any `EscrowDst` from being deployed in a state where `_withdraw`'s subtraction can underflow.

### Proof of Concept
1. Caller (any address) constructs `dstImmutables` with `amount = 100`, and `parameters` encoding `protocolFeeAmount = 60`, `integratorFeeAmount = 60` (sum = 120 > 100), plus arbitrary valid `hashlock`, `timelocks`, `maker`, `taker` (self), `token`.
2. Caller calls `createDstEscrow{value: safetyDeposit}(dstImmutables, srcCancellationTimestamp)` — succeeds; no check rejects the fee sum [6](#0-5) . Escrow is funded with 100 tokens.
3. After the withdrawal timelock, caller (as `taker`) calls `withdraw(secret, dstImmutables)`. Execution reaches `uint256 amount = immutables.amount - integratorFeeAmount - protocolFeeAmount;` → `100 - 60 - 60` underflows and reverts [4](#0-3) .
4. `withdraw` and `publicWithdraw` revert for the entire window; only `cancel` after the cancellation timelock returns the 100 tokens and safety deposit back to `taker`.


No plan generated — this is an analysis-only response per ask-only mode instructions.

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

**File:** contracts/interfaces/IEscrowFactory.sol (L60-67)
```text
    /**
     * @notice Creates a new escrow contract for taker on the destination chain.
     * @dev The caller must send the safety deposit in the native token along with the function call
     * and approve the destination token to be transferred to the created escrow.
     * @param dstImmutables The immutables of the escrow contract that are used in deployment.
     * @param srcCancellationTimestamp The start of the cancellation period for the source chain.
     */
    function createDstEscrow(IBaseEscrow.Immutables calldata dstImmutables, uint256 srcCancellationTimestamp) external payable;
```

**File:** contracts/EscrowDst.sol (L84-93)
```text
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
```
