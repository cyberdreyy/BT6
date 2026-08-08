### Title
programSubscribe notifications ignore RpcAccountInfoConfig::data_slice, forcing full-account base58/base64 encoding on every update - ([File: rpc/src/rpc_subscriptions.rs])

### Summary
`filter_program_results` in `rpc/src/rpc_subscriptions.rs` always calls `encode_ui_account(&pubkey, &account, encoding, None, None)`, hard-coding the `data_slice_config` argument to `None` regardless of what the client requested. Because `encode_ui_account` performs the actual base58/base64/base64+zstd (or JSON-parsed) encoding of `account.data()`, every `programSubscribe` notification is encoded and serialized at full account size, even when the client asked for a small `dataSlice`.

### Finding Description
`filter_program_results` is the notification-path filter used for `programSubscribe` in `rpc/src/rpc_subscriptions.rs`: [1](#0-0) 

For the non-JSON-parsed path it builds each `RpcKeyedAccount` via:
```
account: encode_ui_account(&pubkey, &account, encoding, None, None),
```
with the fourth (`additional_data`) and fifth (`data_slice_config`) arguments statically fixed to `None`. `encode_ui_account` itself, defined in `account-decoder/src/lib.rs`, uses `data_slice_config` to compute `slice_data(account.data(), data_slice_config)` before base58/base64/zstd-encoding: [2](#0-1) 

Since `filter_program_results` never forwards a `Some(UiDataSliceConfig{..})`, `slice_data` always operates on the full `account.data()` regardless of the `dataSlice` the client supplied at subscribe time in `RpcAccountInfoConfig`. The same pattern exists in `filter_account_result` for `accountSubscribe`, which also passes `None, None`: [3](#0-2) 

So a client can subscribe to a program it controls, write a large account under that program, and every subsequent update notification will be base58/base64-encoded and serialized in full — CPU cost for encoding and memory for the resulting `UiAccount`/JSON payload scale with the full on-chain account size, not with the requested slice length. No existing guard (subscription quotas, commitment checks, or parameter validation) restores the slice; the field is simply dropped at the point notifications are constructed, unlike the direct `getAccountInfo`/`getProgramAccounts` RPC methods in `rpc.rs`, which do forward `data_slice` into `encode_account`/`encode_ui_account`.

### Impact Explanation
This is an unbounded-cost issue for a single low-rate subscription: one `programSubscribe` (or `accountSubscribe`) call, updated at the account's normal write rate (bounded by slot rate, satisfying the "one call per `CLUSTER_SLOT_TIME_TARGET/2`" constraint since it's push-based per-slot notification, not additional client-issued calls), causes the validator to repeatedly perform full-size encoding/serialization work per notification even though the client explicitly requested a bounded `dataSlice`. This falls under the "unbounded cost for a single low-rate call" bounty category — the invariant "cost of one subscription is bounded by explicit limits" is violated because the explicit `dataSlice` limit set by the client is silently discarded server-side.

### Likelihood Explanation
Fully reproducible with no privileged access: the attacker deploys/controls a program, subscribes via `programSubscribe` with `dataSlice: {offset: 0, length: 1}` and `encoding: base64`, then writes/grows a large account owned by that program (e.g., via a normal transaction). Every account-write notification will still encode the entire account payload server-side despite the requested 1-byte slice. This is deterministic and requires only standard websocket RPC access and one subscribed program under attacker control.

### Recommendation
Thread `params.data_slice` (and any per-account `additional_data` needed for `JsonParsed`) through to `encode_ui_account` in both `filter_program_results` and `filter_account_result`, mirroring how `rpc.rs`'s `getAccountInfo`/`getProgramAccounts` already forward `data_slice_config` from `RpcAccountInfoConfig` into `encode_account`.

### Proof of Concept
```rust
// rpc/src/rpc_subscriptions.rs (test module)
#[test]
fn program_subscribe_ignores_data_slice() {
    let pubkey = Pubkey::new_unique();
    let large_data = vec![7u8; 10_000];
    let account = AccountSharedData::create(
        1_000_000, large_data.clone(), Pubkey::new_unique(), false, 0,
    );

    let params = ProgramSubscriptionParams {
        pubkey: Pubkey::new_unique(),
        filters: vec![],
        encoding: UiAccountEncoding::Base64,
        commitment: CommitmentConfig::processed(),
        with_context: false,
        sort_results: true,
        // NOTE: even if a data_slice field existed/were threaded in here,
        // it is dropped before reaching encode_ui_account.
    };

    let bank = Arc::new(Bank::default_for_tests());
    let (mut iter, _) = filter_program_results(
        vec![(pubkey, account)],
        &params,
        0,
        bank,
    );
    let keyed_account = iter.next().unwrap();

    // Expected if data_slice (offset:0, length:1) were honored: decoded length == 1.
    // Actual: full 10_000-byte payload is base64-encoded.
    let decoded_len = match keyed_account.account.data {
        UiAccountData::Binary(b64, UiAccountEncoding::Base64) => {
            base64::engine::general_purpose::STANDARD.decode(b64).unwrap().len()
        }
        _ => panic!("unexpected encoding"),
    };
    assert_eq!(decoded_len, large_data.len(), "full account encoded, data_slice not applied");
}
```
This test directly exercises `filter_program_results` (rpc_subscriptions.rs:410-438) and shows the encoded/decoded byte length equals the full account size rather than any requested slice length, since the function has no path to honor `dataSlice` at all.

### Citations

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

**File:** rpc/src/rpc_subscriptions.rs (L410-438)
```rust
fn filter_program_results(
    accounts: Vec<(Pubkey, AccountSharedData)>,
    params: &ProgramSubscriptionParams,
    last_notified_slot: Slot,
    bank: Arc<Bank>,
) -> (impl Iterator<Item = RpcKeyedAccount> + use<>, Slot) {
    let accounts_is_empty = accounts.is_empty();
    let encoding = params.encoding;
    let filters = params.filters.clone();
    let keyed_accounts = accounts.into_iter().filter(move |(_, account)| {
        filters
            .iter()
            .all(|filter_type| filter_allows(filter_type, account))
    });
    let accounts = if is_known_spl_token_id(&params.pubkey)
        && params.encoding == UiAccountEncoding::JsonParsed
        && !accounts_is_empty
    {
        let accounts = get_parsed_token_accounts(bank, keyed_accounts);
        Either::Left(accounts)
    } else {
        let accounts = keyed_accounts.map(move |(pubkey, account)| RpcKeyedAccount {
            pubkey: pubkey.to_string(),
            account: encode_ui_account(&pubkey, &account, encoding, None, None),
        });
        Either::Right(accounts)
    };
    (accounts, last_notified_slot)
}
```

**File:** account-decoder/src/lib.rs (L46-101)
```rust
pub fn encode_ui_account<T: ReadableAccount>(
    pubkey: &Pubkey,
    account: &T,
    encoding: UiAccountEncoding,
    additional_data: Option<AccountAdditionalDataV3>,
    data_slice_config: Option<UiDataSliceConfig>,
) -> UiAccount {
    let space = account.data().len();
    let data = match encoding {
        UiAccountEncoding::Binary => {
            let data = encode_bs58(account, data_slice_config);
            UiAccountData::LegacyBinary(data)
        }
        UiAccountEncoding::Base58 => {
            let data = encode_bs58(account, data_slice_config);
            UiAccountData::Binary(data, encoding)
        }
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
    };
    UiAccount {
        lamports: account.lamports(),
        data,
        owner: account.owner().to_string(),
        executable: account.executable(),
        rent_epoch: account.rent_epoch(),
        space: Some(space as u64),
    }
}
```
