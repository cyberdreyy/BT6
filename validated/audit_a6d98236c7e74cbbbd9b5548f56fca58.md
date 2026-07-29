The report's root cause — a subtraction of fee-derived values from a token amount without a prior bounds check, causing an arithmetic-underflow revert — has a direct, reachable analog in this repository's destination-escrow withdrawal path.

### Title
Missing fee-sum validation in `createDstEscrow` lets an unprivileged taker brick `withdraw`/`publicWithdraw` via arithmetic underflow, then reclaim all funds via `cancel` - (File: contracts/BaseEscrowFactory.sol, contracts/EscrowDst.sol)

### Summary
`EscrowDst._withdraw` computes `uint256 amount = immutables.amount - integratorFeeAmount - protocolFeeAmount;` with no check that the fees do not exceed `immutables.amount` [1](#0-0) . On the source side, the analogous computation in `_postInteraction` is explicitly guarded: `if (integratorFeeAmount + protocolFeeAmount >= takingAmount) revert InvalidFeeAmounts();` [2](#0-1) . No equivalent guard exists for the destination path. `createDstEscrow` is a permissionless, unprivileged entry point that accepts caller-supplied `dstImmutables` (including the `parameters` blob that encodes `protocolFeeAmount`/`integratorFeeAmount`) with no validation of their internal consistency against `immutables.amount` [3](#0-2) .

### Finding Description
This mirrors the reported bug class exactly: a value is derived by subtracting two amounts without first checking that the minuend is large enough, so Solidity's built-in overflow/underflow checks (`panic 0x11`) revert the transaction instead of the code safely returning/handling zero.

- `createDstEscrow(dstImmutables, srcCancellationTimestamp)` has no access control and no fee-sanity check; it only validates `msg.value` and the cancellation-timing relationship, then deploys the clone and funds it [3](#0-2) .
- The fee amounts are read back out of `immutables.parameters` via `ImmutablesLib.protocolFeeAmountCd`/`integratorFeeAmountCd`, which are just raw calldata loads with no bound relative to `immutables.amount` [4](#0-3) .
- `EscrowDst._withdraw`, invoked from both `withdraw` (private window) and `publicWithdraw` (public window), always performs `immutables.amount - integratorFeeAmount - protocolFeeAmount` before transferring the remainder to the maker [5](#0-4) . If `integratorFeeAmount + protocolFeeAmount > immutables.amount`, this line always underflows and reverts — for every call, by every caller (taker or public access-token holder), because `immutables` (and thus its hash checked by `onlyValidImmutables`) is fixed at deployment.
- Crucially, `cancel()` performs **no** fee subtraction at all — it returns the full `immutables.amount` to `immutables.taker` after the cancellation timelock: `_uniTransfer(immutables.token.get(), immutables.taker.get(), immutables.amount);` [6](#0-5) .

So the taker who calls `createDstEscrow` fully controls both the escrow deployment and the fee fields baked into its immutables hash. By setting `integratorFeeAmount + protocolFeeAmount >= immutables.amount`, the taker guarantees that:
1. No `withdraw`/`publicWithdraw` call can ever succeed for this escrow (permanent panic-revert), so the maker can never receive their destination tokens even with the correct secret, and fee recipients never receive their share.
2. Once the `DstCancellation` timelock passes, the same taker calls `cancel()` and receives back the **entire** `immutables.amount` plus the safety deposit — money that was supposed to be split between the maker and the fee recipients.

### Impact Explanation
This is an unprivileged actor (the resolver/taker calling the permissionless `createDstEscrow`) using a self-controlled input (fee fields in `parameters`) to guarantee a broken invariant: destination funds meant for the maker/fee recipients become permanently unwithdrawable, and are then reclaimed entirely by the taker via `cancel()`. This is theft of user funds and of fee-like value during the live swap lifecycle, and it also fits the Medium bucket ("smart contract unable to operate because required token/native balances can be broken by an unprivileged actor") since `withdraw`/`publicWithdraw` are bricked by construction.

### Likelihood Explanation
High. `createDstEscrow` has no on-chain check tying the caller-supplied fee parameters to `immutables.amount`, unlike the equivalent and pre-existing check on the source side. Any resolver/taker can trigger this with a single crafted call; no privileged role, governance, or malicious external actor (bridge/relayer/node) is required — only the taker themselves, who is explicitly an in-scope, unprivileged participant in the destination-escrow creation flow.

### Recommendation
Add the same guard used in `_postInteraction` to `createDstEscrow` (or to `EscrowDst._withdraw`), e.g.:
```solidity
uint256 integratorFeeAmount = dstImmutables.integratorFeeAmountCd();
uint256 protocolFeeAmount = dstImmutables.protocolFeeAmountCd();
if (integratorFeeAmount + protocolFeeAmount >= dstImmutables.amount) revert InvalidFeeAmounts();
```
placed in `createDstEscrow` before deployment (mirroring `contracts/BaseEscrowFactory.sol:92`), so a destination escrow can never be created in a state that makes `_withdraw` unconditionally revert.

### Proof of Concept
1. Attacker (taker) computes `dstImmutables` with `amount = 100`, and `parameters = abi.encode(protocolFeeAmount = 60, integratorFeeAmount = 60, protocolFeeRecipient, integratorFeeRecipient)` (`60 + 60 > 100`).
2. Attacker calls `escrowFactory.createDstEscrow{value: nativeAmount}(dstImmutables, srcCancellationTimestamp)` — succeeds, no fee-sanity check exists [3](#0-2) .
3. After `DstWithdrawal` timelock, maker/relayer (or attacker itself as taker) calls `withdraw(secret, dstImmutables)` with the correct secret: `_withdraw` computes `100 - 60 - 60` → underflow → `panic(0x11)` revert every time, including during `publicWithdraw` window [7](#0-6) .
4. After `DstCancellation` timelock, attacker (as `immutables.taker`) calls `cancel(dstImmutables)`, receiving the full `amount = 100` plus the safety deposit back [6](#0-5) , while the maker and fee recipients receive nothing.

### Citations

**File:** contracts/EscrowDst.sol (L36-96)
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

    /**
     * @notice See {IBaseEscrow-cancel}.
     * @dev The function works on the time interval highlighted with capital letters:
     * ---- contract deployed --/-- finality --/-- private withdrawal --/-- public withdrawal --/-- PRIVATE CANCELLATION ----
     */
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

    /**
     * @dev Transfers ERC20 (or native) tokens to the maker and native tokens to the caller.
     * @param immutables The immutable values used to deploy the clone contract.
     */
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
