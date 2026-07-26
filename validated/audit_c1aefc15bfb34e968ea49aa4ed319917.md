### Title
Missing Zero-Address Check in `bridge::send_token` / `send_token_v2` Enables Permanent Fund Lock — (File: `crates/sui-framework/packages/bridge/sources/bridge.move`)

---

### Summary

`bridge::send_token` and `bridge::send_token_v2` validate that `target_address` is exactly 20 bytes long (the EVM address length) but perform no check that the bytes are non-zero. A user who supplies a 20-byte all-zero `target_address` passes the only guard, their tokens are deposited into the bridge treasury and burned/locked on the Sui side, and the corresponding EVM-side claim will revert when the vault attempts to transfer to `address(0)`. No refund path exists in the protocol, so the tokens are permanently lost.

---

### Finding Description

Both public entry points for outbound token bridging share the same validation pattern:

```move
assert!(target_address.length() == EVM_ADDRESS_LENGTH, EInvalidEvmAddress);
assert!(token_amount > 0, ETokenValueIsZero);
``` [1](#0-0) [2](#0-1) 

A 20-byte vector of all zeros (`vector[0u8, 0u8, …, 0u8]`) satisfies `length() == 20`, so both asserts pass. The function then calls `inner.send_token_internal(target_chain, token, message)`, which deposits the token into the bridge treasury (burning wrapped tokens or locking native ones) and records the bridge message. [3](#0-2) [4](#0-3) 

On the EVM side, `claimTokensWithSignatures` decodes the recipient from the payload and calls the vault to transfer ERC20 tokens to `address(0)`. Standard ERC20 implementations (OpenZeppelin) revert on transfer to the zero address, so the claim transaction fails. Because the bridge protocol contains no refund or cancellation mechanism for failed claims, the Sui-side tokens are permanently locked.

The same root cause exists in the EVM-to-Sui direction: `SuiBridge.bridgeERC20` and `bridgeETH` check `recipientAddress.length == SUI_ADDRESS_LENGTH` (32 bytes) but do not reject an all-zero Sui address. [5](#0-4) [6](#0-5) 

---

### Impact Explanation

Tokens deposited via `send_token` or `send_token_v2` with `target_address = [0u8; 20]` are irrecoverably lost:

- Wrapped bridged tokens (WBTC, USDC, etc.) are **burned** on the Sui side inside `send_token_internal`; the EVM vault never releases the corresponding ERC20 amount because the claim reverts.
- Native SUI bridged outbound is **locked** in the bridge treasury with no unlock path.

This satisfies the "permanent fund lock" impact class. The loss is proportional to the token amount the user supplies; there is no upper bound enforced by the missing check.

---

### Likelihood Explanation

The trigger is a single public Move call reachable by any SUI holder. No special privilege is required. The scenario mirrors the external report exactly: a buggy or malicious front-end, a mis-typed address, or an uninitialized variable in a script could supply an all-zero byte vector that is 20 bytes long. The length check provides a false sense of completeness, making the omission easy to overlook during code review.

---

### Recommendation

Add an explicit non-zero check immediately after the length assertion in both `send_token` and `send_token_v2`:

```move
assert!(target_address.length() == EVM_ADDRESS_LENGTH, EInvalidEvmAddress);
// Add:
assert!(target_address != vector[0u8, 0u8, 0u8, 0u8, 0u8, 0u8, 0u8, 0u8,
                                  0u8, 0u8, 0u8, 0u8, 0u8, 0u8, 0u8, 0u8,
                                  0u8, 0u8, 0u8, 0u8], EInvalidEvmAddress);
```

Apply the symmetric fix to `SuiBridge.bridgeERC20` and `bridgeETH` on the EVM side, rejecting a 32-byte all-zero Sui recipient address. Comprehensively audit all other bridge entry points for the same pattern.

---

### Proof of Concept

1. Obtain any supported bridged token (e.g., WBTC) on Sui.
2. Construct a PTB calling `bridge::bridge::send_token<WBTC>` with:
   - `target_chain` = a supported EVM chain ID
   - `target_address` = `x"0000000000000000000000000000000000000000"` (20 zero bytes)
   - `token` = a non-zero WBTC `Coin` object
3. Submit the transaction. It succeeds: the length check passes, the token is burned/locked in the bridge treasury, and a `TokenDepositedEvent` is emitted.
4. Bridge validators observe the event and produce signatures for the EVM claim.
5. Call `claimTokensWithSignatures` on the EVM bridge with those signatures. The call reverts because the ERC20 vault attempts `transfer(address(0), amount)`.
6. The WBTC is permanently lost — burned on Sui, unclaimed on EVM — with no recovery path in the protocol. [7](#0-6) [8](#0-7)

### Citations

**File:** crates/sui-framework/packages/bridge/sources/bridge.move (L218-256)
```text
public fun send_token<T>(
    bridge: &mut Bridge,
    target_chain: u8,
    target_address: vector<u8>,
    token: Coin<T>,
    ctx: &mut TxContext,
) {
    let inner = load_inner_mut(bridge);

    let bridge_seq_num = inner.get_current_seq_num_and_increment(message_types::token());
    let token_id = inner.treasury.token_id<T>();
    let token_amount = token.balance().value();
    assert!(target_address.length() == EVM_ADDRESS_LENGTH, EInvalidEvmAddress);
    assert!(token_amount > 0, ETokenValueIsZero);

    // create bridge message
    let message = message::create_token_bridge_message(
        inner.chain_id,
        bridge_seq_num,
        address::to_bytes(ctx.sender()),
        target_chain,
        target_address,
        token_id,
        token_amount,
    );

    inner.send_token_internal(target_chain, token, message);

    // emit event
    event::emit(TokenDepositedEvent {
        seq_num: bridge_seq_num,
        source_chain: inner.chain_id,
        sender_address: address::to_bytes(ctx.sender()),
        target_chain,
        target_address,
        token_type: token_id,
        amount: token_amount,
    });
}
```

**File:** crates/sui-framework/packages/bridge/sources/bridge.move (L260-299)
```text
public fun send_token_v2<T>(
    bridge: &mut Bridge,
    target_chain: u8,
    target_address: vector<u8>,
    token: Coin<T>,
    clock: &Clock,
    ctx: &mut TxContext,
) {
    let inner = load_inner_mut(bridge);
    let bridge_seq_num = inner.get_current_seq_num_and_increment(message_types::token());
    let token_id = inner.treasury.token_id<T>();
    let token_amount = token.balance().value();
    assert!(target_address.length() == EVM_ADDRESS_LENGTH, EInvalidEvmAddress);
    assert!(token_amount > 0, ETokenValueIsZero);

    let message = message::create_token_bridge_message_v2(
        inner.chain_id,
        bridge_seq_num,
        address::to_bytes(ctx.sender()),
        target_chain,
        target_address,
        token_id,
        token_amount,
        clock.timestamp_ms(),
    );

    inner.send_token_internal(target_chain, token, message);

    // emit event
    event::emit(TokenDepositedEventV2 {
        seq_num: bridge_seq_num,
        source_chain: inner.chain_id,
        sender_address: address::to_bytes(ctx.sender()),
        target_chain,
        target_address,
        token_type: token_id,
        amount: token_amount,
        timestamp_ms: clock.timestamp_ms(),
    });
}
```

**File:** bridge/evm/contracts/SuiBridge.sol (L141-144)
```text
        require(
            recipientAddress.length == SUI_ADDRESS_LENGTH,
            "SuiBridge: Invalid recipient address length"
        );
```

**File:** bridge/evm/contracts/SuiBridgeV2.sol (L82-85)
```text
        require(
            recipientAddress.length == SUI_ADDRESS_LENGTH,
            "SuiBridge: Invalid recipient address length"
        );
```
