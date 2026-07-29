### Title
Malicious maker address permanently reverts `EscrowDst.withdraw`/`publicWithdraw` for native-token swaps, temporarily freezing resolver funds — (File: contracts/EscrowDst.sol)

### Summary
`EscrowDst._withdraw` forwards the destination asset to the `maker` address taken verbatim from the immutables using an unbounded-gas `.call`. When the destination asset is native token (`token == address(0)`), a user who creates the order can set `maker` to a smart contract whose `receive()` deliberately burns gas until out-of-gas, forcing `_ethTransfer` to revert on every `withdraw()`/`publicWithdraw()` attempt — the exact same "gas-guzzling receiver" root cause as the Hubble `ProcessWithdrawals` DOS.

### Finding Description
`_ethTransfer` performs a raw `.call{value: amount}("")` without a gas stipend, forwarding up to 63/64 of remaining gas to the recipient: [1](#0-0) 

`EscrowDst._withdraw` sends the escrowed amount to `immutables.maker.get()` via `_uniTransfer`, which for `token == address(0)` routes through `_ethTransfer`: [2](#0-1) 

Both the private `withdraw` (callable only by `taker`) and `publicWithdraw` (callable by any access-token holder) call this same internal `_withdraw`, so the maker-controlled call happens on every invocation: [3](#0-2) 

`maker` is part of the `Immutables` struct that is fixed at order-creation time by the order maker (an unprivileged, ordinary swap user) and is validated only by hash equality, not by any code-size/EOA check — an attacker can set it to a contract with a `receive()` that spins a loop long enough to exhaust the forwarded gas (mirroring the `MaliciousReceiver` PoC from the referenced Hubble report). Any such call reverts with `NativeTokenSendingFailure`, and because there is no gas cap/try-catch, the revert propagates and undoes the whole transaction, so no `withdraw`/`publicWithdraw` on that escrow instance can ever succeed.

### Impact Explanation
While the attack cannot make the funds vanish forever — `EscrowDst.cancel` sends the destination token to the fixed `taker` (the resolver, not attacker-controlled) instead of `maker`, so once `Stage.DstCancellation` begins the resolver can cancel and reclaim their locked token/safety deposit — the destination funds remain unwithdrawable for the entire private+public withdrawal window that the resolver configured. This fits the bounty's High-impact category "temporary freezing of funds during the live swap lifecycle": an unprivileged order maker can force the resolver's native-token deposit and safety deposit to sit frozen and unclaimable by the intended recipient until the cancellation stage is reached, at which point the value is only returned to the resolver, never to the maker — the swap can never legitimately complete for that maker with native destination assets.

### Likelihood Explanation
The attacker only needs to be the order's `maker` and specify a smart-contract address (fully under their control, chosen off-chain when the order is signed) as the receiving address for a native-asset destination swap; no privileged role, governance, relayer, or third party is required, and the trigger conditions (`withdraw`/`publicWithdraw` on `EscrowDst` with `token == address(0)`) are part of the normal, documented swap flow.

### Recommendation
Use a fixed, bounded gas stipend for the maker-directed native transfer in `_withdraw` (and consider a pull-payment pattern for the maker leg specifically), or wrap the maker `.call` so that failure only marks the withdrawal amount as claimable later (e.g., allow the taker to complete withdrawal even if the maker leg fails, while crediting the stuck native amount to a rescuable balance) instead of reverting the whole withdrawal.

### Proof of Concept
1. Order maker deploys `MaliciousReceiver` with a `receive()` that loops until gas is nearly exhausted (same pattern as the Hubble PoC).
2. Maker signs an order with `maker = address(MaliciousReceiver)` and `dstToken = address(0)` (native destination asset).
3. Resolver fills the order; `EscrowFactory.createDstEscrow` deploys `EscrowDst` funded with native token + safety deposit.
4. Once `Stage.DstWithdrawal` begins, resolver calls `withdraw(secret, immutables)`; internally `_withdraw` -> `_uniTransfer(address(0), maker, amount)` -> `_ethTransfer` -> `MaliciousReceiver.receive()` consumes the forwarded gas -> call fails -> `NativeTokenSendingFailure()` reverts the whole transaction.
5. Every subsequent `withdraw`/`publicWithdraw` call reverts the same way; the resolver cannot deliver funds to the maker and must wait until `Stage.DstCancellation` to call `cancel()` and reclaim the tokens/safety deposit for themselves instead.

### Citations

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
