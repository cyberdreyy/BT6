### Title
Missing Zero-Address Check in `send_token` Allows Permanent Token Burn — (File: crates/sui-framework/packages/bridge/sources/bridge.move)

---

### Summary

The `send_token` and `send_token_v2` public functions in the Sui bridge Move package validate that the caller-supplied `target_address` is exactly 20 bytes long (`EVM_ADDRESS_LENGTH`) but perform **no check that the bytes are non-zero**. A bridge user who accidentally passes `x"0000000000000000000000000000000000000000"` as the EVM recipient causes their tokens to be permanently burned on Sui while the corresponding EVM-side release either burns ETH at `address(0)` or reverts forever for ERC-20 tokens, producing an irrecoverable fund loss.

---

### Finding Description

`send_token` (and its v2 variant) in `bridge.move` enforces two guards before burning the caller's coin and recording the bridge message:

```move
assert!(target_address.length() == EVM_ADDRESS_LENGTH, EInvalidEvmAddress);
assert!(token_amount > 0, ETokenValueIsZero);
``` [1](#0-0) 

There is no assertion that `target_address` is not the all-zero vector. The existing test suite even exercises this path successfully — `test_execute_send_token` passes a zero EVM address with a non-zero coin and the call completes without error:

```move
let eth_address = x"0000000000000000000000000000000000000000";
env.send_token(@0xABCD, chain_ids::eth_sepolia(), eth_address, btc);
``` [2](#0-1) 

After `send_token` succeeds, `send_token_internal` immediately burns the coin on the Sui side:

```move
inner.treasury.burn(token);
``` [3](#0-2) 

The bridge message is then stored and awaits committee signatures. When the committee later tries to execute the message on EVM, `BridgeVault.transferERC20` calls OpenZeppelin's `SafeERC20.safeTransfer(IERC20(tokenAddress), address(0), amount)`:

```solidity
SafeERC20.safeTransfer(IERC20(tokenAddress), recipientAddress, amount);
``` [4](#0-3) 

OpenZeppelin's ERC-20 implementation reverts on transfer to `address(0)`, so the EVM transaction always reverts. The bridge message is never marked as processed, the vault tokens are permanently locked, and the Sui tokens are already burned. For the ETH token type, `vault.transferETH(payable(address(0)), amount)` succeeds (ETH is sent to `address(0)` and burned on EVM), so the loss is equally permanent.

The same structural gap exists in `send_token_v2`:

```move
assert!(target_address.length() == EVM_ADDRESS_LENGTH, EInvalidEvmAddress);
assert!(token_amount > 0, ETokenValueIsZero);
``` [5](#0-4) 

A symmetric gap exists on the inbound path: `claim_token_internal` derives the Sui recipient directly from the raw bytes in the bridge message without checking for the zero address:

```move
let owner = address::from_bytes(token_payload.token_target_address());
``` [6](#0-5) 

If an EVM user specifies 32 zero bytes as the Sui recipient, `claim_and_transfer_token` mints tokens and transfers them to `@0x0`, permanently locking them.

---

### Impact Explanation

For ERC-20 bridge tokens (USDC, USDT, WBTC): the user's Sui-side tokens are burned immediately and irrecoverably; the EVM vault retains the corresponding tokens but can never release them because every execution attempt reverts. For ETH: both sides burn the value. In both cases the result is **unintended permanent burning / permanent fund lock** below the 10 B SUI cap, matching the allowed High/Medium impact gate.

---

### Likelihood Explanation

Any ordinary bridge user can trigger this by mistyping or copy-pasting a zero address. No privileged role is required. The existing test suite demonstrates the path is reachable and currently passes without error, confirming there is no runtime guard.

---

### Recommendation

Add an explicit non-zero check in both `send_token` and `send_token_v2` immediately after the length check:

```move
assert!(target_address.length() == EVM_ADDRESS_LENGTH, EInvalidEvmAddress);
// Reject the all-zero EVM address
assert!(target_address != vector::tabulate!(EVM_ADDRESS_LENGTH, |_| 0u8), EInvalidEvmAddress);
assert!(token_amount > 0, ETokenValueIsZero);
```

Similarly, in `claim_token_internal`, assert that `owner != @0x0` before minting and transferring tokens.

---

### Proof of Concept

1. Deploy the Sui bridge on a test network (or use the existing `bridge_tests` harness).
2. Obtain any supported bridged coin with a non-zero balance (e.g., `BTC`).
3. Call `send_token` with `target_address = x"0000000000000000000000000000000000000000"` and a valid `target_chain`.
4. Observe: the call succeeds, the coin is burned on Sui, and a bridge record is stored.
5. Attempt to execute the resulting bridge message on the EVM side for any ERC-20 token — the transaction reverts every time because `SafeERC20.safeTransfer(..., address(0), ...)` is rejected by the token contract.
6. The user's funds are permanently lost with no recovery path.

The test at `bridge_tests.move:261-268` already demonstrates step 3–4 passing without any `expected_failure` annotation, confirming the missing guard. [7](#0-6) [8](#0-7) [9](#0-8)

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

**File:** crates/sui-framework/packages/bridge/sources/bridge.move (L560-560)
```text
    let owner = address::from_bytes(token_payload.token_target_address());
```

**File:** crates/sui-framework/packages/bridge/sources/bridge.move (L620-620)
```text
    inner.treasury.burn(token);
```

**File:** crates/sui-framework/packages/bridge/tests/bridge_tests.move (L261-268)
```text
fun test_execute_send_token() {
    let mut env = create_env(chain_ids::sui_testnet());
    env.create_bridge_default();
    let btc: Coin<BTC> = env.get_btc(1);
    let eth_address = x"0000000000000000000000000000000000000000";
    env.send_token(@0xABCD, chain_ids::eth_sepolia(), eth_address, btc);
    env.destroy_env();
}
```

**File:** bridge/evm/contracts/BridgeVault.sol (L37-45)
```text
    function transferERC20(address tokenAddress, address recipientAddress, uint256 amount)
        external
        override
        onlyOwner
        nonReentrant
    {
        // Transfer the tokens from the contract to the target address
        SafeERC20.safeTransfer(IERC20(tokenAddress), recipientAddress, amount);
    }
```
