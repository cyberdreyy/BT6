### Title
Missing fee-vs-amount validation in `createDstEscrow` permanently freezes maker's destination funds via arithmetic underflow — ([File: contracts/BaseEscrowFactory.sol])

### Summary
`_postInteraction` (source path) explicitly guards against corrupted fee accounting with `if (integratorFeeAmount + protocolFeeAmount >= takingAmount) revert InvalidFeeAmounts();` [1](#0-0) . No equivalent check exists in `createDstEscrow` [2](#0-1) , which accepts an arbitrary `Immutables` struct (including the fee `parameters` blob) directly from the caller. This mirrors the Moloch root cause: an unguarded arithmetic operation on attacker-influenced values causes the checked-math VM to revert unconditionally in the core fund-release function, freezing funds for the intended recipient while a side-channel (`cancel`) lets the party who caused the corruption reclaim the funds.

### Finding Description
`createDstEscrow` deploys the destination escrow clone using whatever `dstImmutables` the caller supplies, only validating `msg.value`/timelock ordering — never that `protocolFeeAmount + integratorFeeAmount < amount` [2](#0-1) . The fee amounts and recipients are embedded in `immutables.parameters`, an opaque `bytes` blob decoded later by `ImmutablesLib` [3](#0-2) .

When the maker's secret is later used, `EscrowDst._withdraw` unconditionally executes:
```
uint256 amount = immutables.amount - integratorFeeAmount - protocolFeeAmount;
``` [4](#0-3) 

Because the contract compiles under Solidity 0.8.23 with default checked arithmetic, if `integratorFeeAmount + protocolFeeAmount >= immutables.amount`, this subtraction reverts for every caller and every time window (`withdraw` and `publicWithdraw` both funnel into `_withdraw`) [5](#0-4) . There is no fallback path for the maker to ever receive destination funds once this state is reached — only `cancel()`, which returns the *entire* deposited amount to the `taker` (the resolver who created the malicious escrow) after the cancellation timelock, with no fee subtraction at all [6](#0-5) .

Since a resolver (the caller/funder of `createDstEscrow`) fully controls the `parameters` field, an unprivileged resolver acting as taker can deploy a destination escrow that appears correctly funded (satisfies `msg.value`/`safeTransferFrom` checks) but is permanently unable to pay out to the maker, then reclaim 100% of the funds via `cancel` once the timelock passes — while the maker has already revealed the secret enabling the resolver to withdraw the maker's source-chain tokens via `EscrowSrc`.

### Impact Explanation
This breaks the source-immutables/hashlock guarantee that a revealed secret entitles the maker to their destination funds. The corrupted, unguarded fee subtraction causes `withdraw`/`publicWithdraw` to revert unconditionally for the entire escrow lifetime, and the resolver can reclaim all destination-side value via `cancel`, resulting in direct theft of the maker's destination funds (and freezing of the maker's funds throughout the live swap lifecycle). This falls under Critical (direct theft of user funds in motion / permanent freezing of funds) per the bounty scope, since the trigger is reachable by an unprivileged resolver entering through the documented `createDstEscrow → destination withdraw/cancel` path.

### Likelihood Explanation
The attack requires the resolver/taker to be the malicious actor constructing `dstImmutables.parameters` when calling `createDstEscrow`, which is an ordinary, permissionless call in the destination-escrow-creation flow — no privileged role or off-chain manipulation of third parties is needed beyond the resolver itself acting maliciously, which is within the "unprivileged user" model for this contract per the audit scope (resolvers are not treated as privileged/trusted for fund-safety invariants elsewhere in this codebase, e.g., they must pre-fund safety deposits and pass on-chain balance checks). The one open uncertainty is the off-chain protocol convention that the maker should only reveal the secret after verifying the deployed dst escrow's parameters match the expected fee terms; if such off-chain verification is assumed to always occur correctly, exploitability depends on a lapse in that off-chain check, which is outside this contract's on-chain guarantees — the on-chain code itself provides no protection against this specific corrupted state.

### Recommendation
Add the same fee-integrity check present in `_postInteraction` to `createDstEscrow`:
```solidity
uint256 integratorFeeAmount = immutables.integratorFeeAmountCd();
uint256 protocolFeeAmount = immutables.protocolFeeAmountCd();
if (integratorFeeAmount + protocolFeeAmount >= immutables.amount) revert InvalidFeeAmounts();
```
before deploying the clone, so a destination escrow can never be created in a state where `withdraw`/`publicWithdraw` are guaranteed to revert.

### Proof of Concept
1. Resolver (taker) calls `createDstEscrow` with `dstImmutables.amount = X` and `parameters` encoding `protocolFeeAmount + integratorFeeAmount >= X` [2](#0-1) .
2. `msg.value`/ERC20 transfer checks pass since they only validate `safetyDeposit`/`amount`, not the fee breakdown.
3. Maker, expecting a correctly funded/parameterized escrow, reveals the secret to the resolver (off-chain), enabling resolver's `EscrowSrc.withdraw` on the source chain.
4. Resolver (or anyone with the access token during the public window) calls `EscrowDst.withdraw`/`publicWithdraw`; `_withdraw` reverts every time at `uint256 amount = immutables.amount - integratorFeeAmount - protocolFeeAmount;` [7](#0-6) .
5. After the cancellation timelock, resolver calls `cancel()`, receiving the full `immutables.amount` plus safety deposit back [6](#0-5) , while the maker never receives destination funds despite having already surrendered the source-chain tokens.

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

**File:** contracts/libraries/ImmutablesLib.sol (L24-43)
```text
    function protocolFeeAmount(IBaseEscrow.Immutables memory immutables) internal pure returns (uint256 ret) {
        bytes memory parameters = immutables.parameters;
        if (parameters.length < 0x20) revert IndexOutOfRange();
        assembly ("memory-safe") {
            ret := mload(add(parameters, 0x20))
        }
    }

    /**
     * @notice Returns the integrator fee amount from the immutables.
     * @param immutables The immutables to extract the fee from.
     * @return ret The integrator fee amount.
     */
    function integratorFeeAmount(IBaseEscrow.Immutables memory immutables) internal pure returns (uint256 ret) {
        bytes memory parameters = immutables.parameters;
        if (parameters.length < 0x40) revert IndexOutOfRange();
        assembly ("memory-safe") {
            ret := mload(add(parameters, 0x40))
        }
    }
```

**File:** contracts/EscrowDst.sol (L36-57)
```text
    function withdraw(bytes32 secret, Immutables calldata immutables)
        external
        onlyCaller(immutables.taker.get())
        onlyAfter(immutables.timelocks.get(TimelocksLib.Stage.DstWithdrawal))
        onlyBefore(immutables.timelocks.get(TimelocksLib.Stage.DstCancellation))
    {
        _withdraw(secret, immutables);
    }

    /**
     * @notice See {IBaseEscrow-publicWithdraw}.
     * @dev The function works on the time intervals highlighted with capital letters:
     * ---- contract deployed --/-- finality --/-- private withdrawal --/-- PUBLIC WITHDRAWAL --/-- private cancellation ----
     */
    function publicWithdraw(bytes32 secret, Immutables calldata immutables)
        external
        onlyAccessTokenHolder()
        onlyAfter(immutables.timelocks.get(TimelocksLib.Stage.DstPublicWithdrawal))
        onlyBefore(immutables.timelocks.get(TimelocksLib.Stage.DstCancellation))
    {
        _withdraw(secret, immutables);
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
