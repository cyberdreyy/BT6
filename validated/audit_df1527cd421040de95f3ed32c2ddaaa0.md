### Title
Per-notification cost of a single accountSubscribe is unbounded by attacker-grown account data (no size cap in `filter_account_result`/`encode_ui_account` for non-base58 encodings) - ([File: rpc/src/rpc_subscriptions.rs])

### Summary
`SubscriptionControl::subscribe` only bounds the *count* of live subscriptions via `max_active_subscriptions`/`SubscriberCountGuard`, not the *cost* of each subscription's ongoing notifications. For `accountSubscribe`, every bank update re-encodes the full current account via `filter_account_result` → `encode_ui_account`, and for `Base64`/`Base64Zstd`/`JsonParsed` encodings there is no size cap analogous to the `MAX_BASE58_BYTES` guard used for `Binary`/`Base58`. An attacker who grows the subscribed account's data on-chain (e.g., reassigning ownership to token-2022 and adding extensions, or repeated `realloc`) causes the cost of that one long-lived subscription to scale with the account's size on every subsequent notification, with no explicit bound tied to the subscription itself.

### Finding Description
- `RpcSolPubSubImpl::subscribe` (`rpc/src/rpc_pubsub.rs:380-393`) delegates to `SubscriptionControl::subscribe` (`rpc/src/rpc_subscription_tracker.rs:219-281`), which reserves a slot via `SubscriberCountGuard::try_reserve` (`rpc/src/rpc_subscription_tracker.rs:600-611`). This guard enforces only a numeric cap (`max_active_subscriptions`) on the *number* of subscriptions/subscribers — it has no notion of per-subscription payload size or CPU cost, and there is no mechanism that re-checks or throttles a subscription based on the size of the account it watches. [1](#0-0) 
- Each bank update triggers `RpcSubscriptions::notify_watchers`, which for every `SubscriptionParams::Account` subscription calls `check_commitment_and_notify` → `filter_account_result`, unconditionally re-fetching and re-encoding the *entire current* account state with `encode_ui_account`, regardless of how large the account has grown since the client subscribed. [2](#0-1) [3](#0-2) 
- `encode_ui_account` (`account-decoder/src/lib.rs:46-101`) only guards the `Binary`/`Base58` paths via `encode_bs58`, which caps output to `MAX_BASE58_BYTES` (128 bytes) and substitutes an error string if exceeded. [4](#0-3) 
For `Base64`/`Base64Zstd`, the *entire* account data is base64-encoded (or zstd-compressed then base64-encoded) with no size cap at all; for `JsonParsed`, the full data is passed to `parse_account_data_v3`, and for `spl_token_2022` mints this walks extension data (`rpc/src/parsed_token_accounts.rs:1-50`), whose cost scales with the number/size of extensions. [5](#0-4) 
- Compare this to the equivalent one-shot RPC path, `encode_account` in `rpc/src/rpc.rs`, which explicitly rejects (`InvalidRequest`) any `Binary`/`Base58` encoded response whose sliced data exceeds `MAX_BASE58_BYTES` — i.e., the one-shot call has an explicit "refuse to do the expensive thing" guard that the pubsub notification path lacks entirely for the account-subscription case (the pubsub path silently substitutes the error string only for `Binary`, and does nothing at all for `Base64`/`Base64Zstd`/`JsonParsed`). [6](#0-5) 
- Attacker flow: an unprivileged party (which may be the same actor as the subscriber, per the question's premise) subscribes once via `accountSubscribe` to a pubkey with `Base64`/`Base64Zstd`/`JsonParsed` encoding, then issues ordinary permissionless transactions that grow/reassign that account (e.g., `realloc` up to the max account size, or reassigning ownership to `spl_token_2022` with maximal extensions). No further RPC calls from the attacker are required — every subsequent bank update (driven by the validator's normal per-slot commitment/gossip notifications) causes `notify_watchers` to re-serialize the now-large account on the rayon notification thread pool and push a correspondingly large JSON payload through the unbounded `broadcast::channel` and into the `RpcNotifier::recent_items` buffer, all attributable to a single, still-uncounted-beyond-1 subscription.

### Impact Explanation
This falls under "unbounded cost for a single low-rate call": one `accountSubscribe` (a single RPC/websocket call) results in recurring, unbounded per-notification CPU (base64/zstd encoding, JSON-parsing of extension data) and memory/bandwidth cost that scales with attacker-controlled on-chain account size, entirely decoupled from the fixed subscription-count cap enforced by `SubscriptionControl`. Because `notify_watchers` iterates all commitment watchers in parallel via rayon on every bank commitment update (potentially multiple times per slot for processed/confirmed/finalized), this can repeatedly consume CPU/memory proportional to account size, degrading RPC-node responsiveness for other clients that share the same notification thread pool and `broadcast_sender` channel — a genuine RPC resource-exhaustion vector for a subscription-based API, not a mere best-practice nit.

### Likelihood Explanation
Preconditions are minimal and fully within the unprivileged-attacker envelope: only one `accountSubscribe` call is needed, and account growth is achieved via ordinary permissionless transactions (system-program `realloc`/reassignment, or a token-2022 mint initialization with extensions), which are the kind of on-chain data writes explicitly allowed under the rules ("writing on-chain data that is later returned through those APIs"). No elevated privileges, no leader/gossip control, and no additional RPC calls beyond the single subscribe are required — the notification cost recurs automatically as the validator processes normal bank updates. This is straightforward and repeatable.

### Recommendation
Add an explicit size/cost bound to the pubsub account-notification path analogous to the `MAX_BASE58_BYTES` guard already used in `rpc.rs::encode_account`:
- In `filter_account_result` (`rpc/src/rpc_subscriptions.rs`), before calling `encode_ui_account`/`get_parsed_token_account`, check the account's data length (post `data_slice`) against a configurable maximum for `Base64`/`Base64Zstd`/`JsonParsed` encodings, and emit a bounded error/notification (or unsubscribe/downgrade the subscription) instead of encoding the full payload.
- Alternatively/additionally, enforce a per-subscription notification-size cap enforced once at encode time (independent of encoding), so that growth of a previously-small account cannot silently balloon recurring per-slot notification cost.
- Consider tracking aggregate "subscription cost" (e.g., total watched account bytes) in `SubscriptionControl`, rejecting or capping new/ongoing account subscriptions whose current account size exceeds a configured threshold, re-evaluated as account size changes.

### Proof of Concept
```rust
// rpc/src/rpc_subscriptions.rs (test module)
#[test]
fn test_account_subscribe_cost_grows_unbounded_with_attacker_controlled_size() {
    // 1. Set up bank_forks/RpcSubscriptions as in existing tests (see
    //    test_account_notification / test_account_subscribe_with_encoding).
    // 2. Subscribe once via rpc.account_subscribe(pubkey, Base64 or JsonParsed encoding).
    // 3. Store a small account for `pubkey` (e.g. 16 bytes), call
    //    subscriptions.notify_subscribers(...) and measure elapsed time/bytes of the
    //    resulting notification via `receiver.recv()`.
    // 4. "Attacker" transaction: reassign/realloc the SAME pubkey's account to a much
    //    larger owner (e.g., spl_token_2022 mint with several extensions, or a raw
    //    buffer near MAX_PERMITTED_DATA_LENGTH), store it via bank.store_account.
    // 5. Call subscriptions.notify_subscribers(...) again for the new slot and measure
    //    elapsed time / payload bytes of the notification.
    // 6. Assert: payload size / encode time for step 5 does NOT stay within a fixed
    //    bound (e.g., is proportional to account size and exceeds any small constant
    //    such as MAX_BASE58_BYTES), demonstrating the subscription's cost is
    //    unbounded and controlled entirely by on-chain data the subscriber does not
    //    control.
    //
    // Expected (buggy) result: notification payload/encode cost scales linearly with
    // attacker-grown account size for Base64/Base64Zstd/JsonParsed encodings, with no
    // rejection/error analogous to `encode_account`'s MAX_BASE58_BYTES guard.
}
```
This complements the existing `test_account_subscribe_with_encoding` / `notify_watchers` tests in `rpc/src/rpc_pubsub.rs` and `rpc/src/rpc_subscriptions.rs`, which already demonstrate the notify path calling `encode_ui_account` per bank update but do not assert any cost bound.

### Citations

**File:** rpc/src/rpc_subscription_tracker.rs (L594-611)
```rust
// RAII guard for one subscriber slot. The slot is reserved atomically against
// the configured cap by `try_reserve` (returns `None` if the cap is reached)
// and released on drop. Intentionally not `Clone`: each holder must go through
// `try_reserve` so duplicates cannot bypass the cap.
struct SubscriberCountGuard(Arc<SubscriptionControlInner>);

impl SubscriberCountGuard {
    fn try_reserve(control: &Arc<SubscriptionControlInner>) -> Option<Self> {
        let max = control.max_active_subscriptions;
        control
            .subscriber_count
            .fetch_update(Ordering::Relaxed, Ordering::Relaxed, |current| {
                (current < max).then_some(current + 1)
            })
            .ok()?;
        Some(Self(Arc::clone(control)))
    }
}
```

**File:** rpc/src/rpc_subscriptions.rs (L370-392)
```rust
fn filter_account_result(
    result: Option<(AccountSharedData, Slot)>,
    params: &AccountSubscriptionParams,
    last_notified_slot: Slot,
    bank: Arc<Bank>,
) -> (Option<UiAccount>, Slot) {
    // If the account is not found, `last_modified_slot` will default to zero and
    // we will notify clients that the account no longer exists if we haven't already
    let (account, last_modified_slot) = result.unwrap_or_default();

    // If last_modified_slot < last_notified_slot this means that we last notified for a fork
    // and should notify that the account state has been reverted.
    let account = (last_modified_slot != last_notified_slot).then(|| {
        if is_known_spl_token_id(account.owner())
            && params.encoding == UiAccountEncoding::JsonParsed
        {
            get_parsed_token_account(&bank, &params.pubkey, account, None)
        } else {
            encode_ui_account(&params.pubkey, &account, params.encoding, None, None)
        }
    });
    (account, last_modified_slot)
}
```

**File:** rpc/src/rpc_subscriptions.rs (L948-966)
```rust
            match subscription.params() {
                SubscriptionParams::Account(params) => {
                    num_accounts_found.fetch_add(1, Ordering::Relaxed);
                    if let Some(slot) = slot {
                        let notified = check_commitment_and_notify(
                            params,
                            subscription,
                            bank_forks,
                            slot,
                            |bank, params| bank.get_account_modified_slot(&params.pubkey),
                            filter_account_result,
                            notifier,
                            false,
                        );

                        if notified {
                            num_accounts_notified.fetch_add(1, Ordering::Relaxed);
                        }
                    }
```

**File:** account-decoder/src/lib.rs (L31-44)
```rust
pub type StringDecimals = String;
pub const MAX_BASE58_BYTES: usize = 128;

fn encode_bs58<T: ReadableAccount>(
    account: &T,
    data_slice_config: Option<UiDataSliceConfig>,
) -> String {
    let slice = slice_data(account.data(), data_slice_config);
    if slice.len() <= MAX_BASE58_BYTES {
        bs58::encode(slice).into_string()
    } else {
        "error: data too large for bs58 encoding".to_string()
    }
}
```

**File:** account-decoder/src/lib.rs (L63-91)
```rust
        UiAccountEncoding::Base64 => UiAccountData::Binary(
            BASE64_STANDARD.encode(slice_data(account.data(), data_slice_config)),
            encoding,
        ),
        UiAccountEncoding::Base64Zstd => {
            let mut encoder = zstd::stream::write::Encoder::new(Vec::new(), 0).unwrap();
            match encoder
                .write_all(slice_data(account.data(), data_slice_config))
                .and_then(|()| encoder.finish())
            {
                Ok(zstd_data) => UiAccountData::Binary(BASE64_STANDARD.encode(zstd_data), encoding),
                Err(_) => UiAccountData::Binary(
                    BASE64_STANDARD.encode(slice_data(account.data(), data_slice_config)),
                    UiAccountEncoding::Base64,
                ),
            }
        }
        UiAccountEncoding::JsonParsed => {
            if let Ok(parsed_data) =
                parse_account_data_v3(pubkey, account.owner(), account.data(), additional_data)
            {
                UiAccountData::Json(parsed_data)
            } else {
                UiAccountData::Binary(
                    BASE64_STANDARD.encode(slice_data(account.data(), data_slice_config)),
                    UiAccountEncoding::Base64,
                )
            }
        }
```

**File:** rpc/src/rpc.rs (L2575-2601)
```rust
fn encode_account<T: ReadableAccount>(
    account: &T,
    pubkey: &Pubkey,
    encoding: UiAccountEncoding,
    data_slice: Option<UiDataSliceConfig>,
) -> Result<UiAccount> {
    if (encoding == UiAccountEncoding::Binary || encoding == UiAccountEncoding::Base58)
        && data_slice
            .map(|s| min(s.length, account.data().len().saturating_sub(s.offset)))
            .unwrap_or(account.data().len())
            > MAX_BASE58_BYTES
    {
        let message = format!(
            "Encoded binary (base 58) data should be less than {MAX_BASE58_BYTES} bytes, please \
             use Base64 encoding."
        );
        Err(error::Error {
            code: error::ErrorCode::InvalidRequest,
            message,
            data: None,
        })
    } else {
        Ok(encode_ui_account(
            pubkey, account, encoding, None, data_slice,
        ))
    }
}
```
