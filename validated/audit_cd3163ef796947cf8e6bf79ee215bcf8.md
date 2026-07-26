### Title
Fee-Free Bridge Limit Exhaustion Enables 24-Hour DoS on Ethereum→Sui Token Claims — (File: `crates/sui-framework/packages/bridge/sources/limiter.move`)

---

### Summary

The Sui native bridge enforces a 24-hour rolling-window USD limit on inbound (Ethereum→Sui) token claims via `TransferLimiter`. Because neither `send_token` nor `claim_and_transfer_token` charges any protocol fee, an ordinary token holder who owns enough bridgeable assets can exhaust the entire daily limit in a single sequence of transactions, silently blocking every other user's pending claim for up to 24 hours at the cost of only gas.

---

### Finding Description

`TransferLimiter` in `limiter.move` tracks per-route USD value over a 24-hour sliding window: [1](#0-0) 

The genesis limit for the Ethereum-mainnet → Sui-mainnet route is hardcoded at **5 000 000 USD** (and the live network documentation states $16 M): [2](#0-1) 

Every inbound claim goes through `claim_token_internal`, which calls `check_and_record_sending_transfer`. If the window is full the function returns `(option::none(), owner)` — the mint is silently skipped and the `TokenTransferLimitExceed` event is emitted: [3](#0-2) 

`claim_and_transfer_token` is declared `public fun` with **no access control** — any address may call it for any approved sequence number: [4](#0-3) 

`send_token` and `send_token_v2` are likewise `public fun` with **no fee deducted**: [5](#0-4) 

`check_and_record_sending_transfer` records the notional USD value and returns `false` once the window is full, with no mechanism to distinguish a legitimate large transfer from a deliberate exhaustion attempt: [6](#0-5) 

---

### Impact Explanation

An attacker who deposits the full daily limit worth of ETH/WBTC/USDC on Ethereum, waits for bridge-authority signatures (normal operation), then calls `claim_and_transfer_token` on Sui exhausts `TransferRecord.total_amount` to the route limit. Every subsequent claim attempt by any other user returns `option::none()` until the 24-hour window rolls over. The attacker's own tokens are not lost — they are simply moved from Ethereum to Sui. The only cost is Sui gas (fractions of a cent per transaction). All pending legitimate Ethereum→Sui bridge transfers are silently deferred for up to 24 hours, constituting harmful smart-contract behavior (Medium impact). [7](#0-6) 

---

### Likelihood Explanation

The attack requires capital equal to the route limit (genesis: $5 M USD; live: up to $16 M USD). The attacker recovers all capital on Sui, so the net cost is gas only. A well-capitalised actor with a governance or market incentive (e.g., blocking a competitor's cross-chain arbitrage, influencing a time-sensitive vote) faces no permanent loss. The attack is repeatable every 24 hours. [8](#0-7) 

---

### Recommendation

1. **Introduce a per-transfer protocol fee** (even a small basis-point fee) so that exhausting the daily limit carries a real economic cost proportional to the USD value bridged.
2. **Per-address sub-limits**: cap the fraction of the daily window any single sender address may consume within one hour.
3. **Minimum transfer size**: reject dust transfers that consume limit quota without meaningful economic purpose.

---

### Proof of Concept

```
// Attacker owns $5 M USD worth of ETH on Ethereum mainnet.

// Step 1 – Ethereum side (no Sui code change needed):
//   Attacker calls SuiBridge.bridgeETH{value: 5_000_000 USD worth of ETH}(suiRecipient, SUI_CHAIN_ID)
//   Bridge authorities observe the deposit and co-sign a BridgeMessage.

// Step 2 – Sui side:
//   Attacker calls bridge::claim_and_transfer_token<ETH>(bridge, clock, ETH_CHAIN_ID, seq_num, ctx)
//   Inside claim_token_internal:
//     route = get_route(eth_mainnet, sui_mainnet)
//     check_and_transfer_sending_transfer<ETH>(..., amount = 5_000_000 USD)
//     → total_amount reaches route_limit (5_000_000 * USD_VALUE_MULTIPLIER)
//     → returns true, token minted to attacker

// Step 3 – All subsequent legitimate claims within the 24-hour window:
//   Any user calls claim_and_transfer_token for their pending transfer
//   check_and_record_sending_transfer returns false (limit exhausted)
//   event::emit(TokenTransferLimitExceed { message_key })
//   (option::none(), owner) returned → token NOT minted
//   User must wait up to 24 hours for the window to roll over.
```

The exact broken value is `TransferRecord.total_amount` reaching `route_limit` (5 000 000 × 10^8 in the genesis config), after which `(record.total_amount as u128) * decimal_multiplier + notional_amount_with_token_multiplier > route_limit_adjusted` evaluates to `true` and every subsequent claim is silently dropped. [9](#0-8) [10](#0-9)

### Citations

**File:** crates/sui-framework/packages/bridge/sources/limiter.move (L23-35)
```text
public struct TransferLimiter has store {
    transfer_limits: VecMap<BridgeRoute, u64>,
    // Per hour transfer amount for each bridge route
    transfer_records: VecMap<BridgeRoute, TransferRecord>,
}

public struct TransferRecord has store {
    hour_head: u64,
    hour_tail: u64,
    per_hour_amounts: vector<u64>,
    // total amount in USD, 8 DP accuracy, so 100000000 => 1USD
    total_amount: u64,
}
```

**File:** crates/sui-framework/packages/bridge/sources/limiter.move (L64-122)
```text
public(package) fun check_and_record_sending_transfer<T>(
    self: &mut TransferLimiter,
    treasury: &BridgeTreasury,
    clock: &Clock,
    route: BridgeRoute,
    amount: u64,
): bool {
    // Create record for route if not exists
    if (!self.transfer_records.contains(&route)) {
        self
            .transfer_records
            .insert(
                route,
                TransferRecord {
                    hour_head: 0,
                    hour_tail: 0,
                    per_hour_amounts: vector[],
                    total_amount: 0,
                },
            )
    };
    let record = self.transfer_records.get_mut(&route);
    let current_hour_since_epoch = current_hour_since_epoch(clock);

    record.adjust_transfer_records(current_hour_since_epoch);

    // Get limit for the route
    let route_limit = self.transfer_limits.try_get(&route);
    assert!(route_limit.is_some(), ELimitNotFoundForRoute);
    let route_limit = route_limit.destroy_some();
    let route_limit_adjusted = (route_limit as u128) * (treasury.decimal_multiplier<T>() as u128);

    // Compute notional amount
    // Upcast to u128 to prevent overflow, to not miss out on small amounts.
    let value = (treasury.notional_value<T>() as u128);
    let notional_amount_with_token_multiplier = value * (amount as u128);

    // Check if transfer amount exceed limit
    // Upscale them to the token's decimal.
    if (
        (record.total_amount as u128)
            * (treasury.decimal_multiplier<T>() as u128)
            + notional_amount_with_token_multiplier > route_limit_adjusted
    ) {
        return false
    };

    // Now scale down to notional value
    let notional_amount =
        notional_amount_with_token_multiplier / (treasury.decimal_multiplier<T>() as u128);
    // Should be safe to downcast to u64 after dividing by the decimals
    let notional_amount = (notional_amount as u64);

    // Record transfer value
    let new_amount = record.per_hour_amounts.pop_back() + notional_amount;
    record.per_hour_amounts.push_back(new_amount);
    record.total_amount = record.total_amount + notional_amount;
    true
}
```

**File:** crates/sui-framework/packages/bridge/sources/limiter.move (L149-179)
```text
fun adjust_transfer_records(self: &mut TransferRecord, current_hour_since_epoch: u64) {
    if (self.hour_head == current_hour_since_epoch) {
        return // nothing to backfill
    };

    let target_tail = current_hour_since_epoch - 23;

    // If `hour_head` is even older than 24 hours ago, it means all items in
    // `per_hour_amounts` are to be evicted.
    if (self.hour_head < target_tail) {
        self.per_hour_amounts = vector[];
        self.total_amount = 0;
        self.hour_tail = target_tail;
        self.hour_head = target_tail;
        // Don't forget to insert this hour's record
        self.per_hour_amounts.push_back(0);
    } else {
        // self.hour_head is within 24 hour range.
        // some items in `per_hour_amounts` are still valid, we remove stale hours.
        while (self.hour_tail < target_tail) {
            self.total_amount = self.total_amount - self.per_hour_amounts.remove(0);
            self.hour_tail = self.hour_tail + 1;
        }
    };

    // Backfill from hour_head to current hour
    while (self.hour_head < current_hour_since_epoch) {
        self.per_hour_amounts.push_back(0);
        self.hour_head = self.hour_head + 1;
    }
}
```

**File:** crates/sui-framework/packages/bridge/sources/limiter.move (L185-215)
```text
fun initial_transfer_limits(): VecMap<BridgeRoute, u64> {
    let mut transfer_limits = vec_map::empty();
    // 5M limit on Sui -> Ethereum mainnet
    transfer_limits.insert(
        chain_ids::get_route(chain_ids::eth_mainnet(), chain_ids::sui_mainnet()),
        5_000_000 * USD_VALUE_MULTIPLIER,
    );

    // MAX limit for testnet and devnet
    transfer_limits.insert(
        chain_ids::get_route(chain_ids::eth_sepolia(), chain_ids::sui_testnet()),
        MAX_TRANSFER_LIMIT,
    );

    transfer_limits.insert(
        chain_ids::get_route(chain_ids::eth_sepolia(), chain_ids::sui_custom()),
        MAX_TRANSFER_LIMIT,
    );

    transfer_limits.insert(
        chain_ids::get_route(chain_ids::eth_custom(), chain_ids::sui_testnet()),
        MAX_TRANSFER_LIMIT,
    );

    transfer_limits.insert(
        chain_ids::get_route(chain_ids::eth_custom(), chain_ids::sui_custom()),
        MAX_TRANSFER_LIMIT,
    );

    transfer_limits
}
```

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

**File:** crates/sui-framework/packages/bridge/sources/bridge.move (L392-407)
```text
// This function can be called by anyone to claim and transfer the token to the recipient
// If the token has already been claimed or hits limiter currently, it will return instead of aborting.
public fun claim_and_transfer_token<T>(
    bridge: &mut Bridge,
    clock: &Clock,
    source_chain: u8,
    bridge_seq_num: u64,
    ctx: &mut TxContext,
) {
    let (token, owner) = bridge.claim_token_internal<T>(clock, source_chain, bridge_seq_num, ctx);
    if (token.is_some()) {
        transfer::public_transfer(token.destroy_some(), owner)
    } else {
        token.destroy_none();
    };
}
```

**File:** crates/sui-framework/packages/bridge/sources/bridge.move (L583-598)
```text
    let amount = token_payload.token_amount();
    // Make sure transfer is within limit.
    if (
        !bypass_limiter &&
        !inner
            .limiter
            .check_and_record_sending_transfer<T>(
                &inner.treasury,
                clock,
                route,
                amount,
            )
    ) {
        event::emit(TokenTransferLimitExceed { message_key: key });
        return (option::none(), owner)
    };
```
