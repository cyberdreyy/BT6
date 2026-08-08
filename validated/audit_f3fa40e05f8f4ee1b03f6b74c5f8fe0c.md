### Title
Single oversized account under a queried program permanently breaks `getProgramAccounts`/`getTokenAccountsByOwner`/`getTokenAccountsByDelegate` for all callers - ([File: rpc/src/rpc.rs])

### Summary
`OmoVault` allowed an unprivileged, unapproved `OmoAgent` account to be registered and then poison the aggregate `totalAssets()` loop, permanently reverting a function that every depositor/withdrawer depends on. Agave's RPC layer has a structurally identical pattern: `JsonRpcRequestProcessor::get_program_accounts` aggregates every account under a `program_id` and encodes each one with `encode_account`, using `.collect::<Result<Vec<_>>>()?` — a single failing element aborts the whole response for every caller of that query, and the "poisoning" account (an oversized account under that `program_id`) can be created by any unprivileged user without the program's cooperation.

### Finding Description
`get_program_accounts` fetches all accounts owned by `program_id` and then maps/encodes each one, short-circuiting on the first `Err`: [1](#0-0) 

The per-account encoder, `encode_account`, deliberately returns an `Err` (not a graceful fallback) whenever the account's data (after slicing) exceeds `MAX_BASE58_BYTES` (128 bytes) and the requested encoding is `Binary` or `Base58`: [2](#0-1) 

Critically, `Binary` is the **default** encoding for `getProgramAccounts` when the caller does not specify one: [3](#0-2) 

Any unprivileged user can create a new account (via the System Program's `CreateAccount` instruction) with an arbitrary `owner` field and a data length greater than 128 bytes — account creation does not require the owning program's approval, only that the new account key signs. Doing this once against any `program_id` permanently "poisons" that program's account set: every subsequent `getProgramAccounts(program_id)` call (using default/Binary/Base58 encoding) will hit `encode_account`'s error path for that single oversized account and the `.collect::<Result<Vec<_>>>()?` will fail the entire RPC response — for every account under that program, for every client — with no mechanism to filter out or evict the "malicious" account other than the caller explicitly switching to Base64/JsonParsed encoding (which most existing client integrations may not do, since Binary/Base58 has historically been the default assumption for many tools/scripts).

The exact same `.collect::<Result<Vec<_>>>()?` short-circuit pattern (all through `encode_account`) also exists in the `accounts` post-simulation reporting path of `simulateTransaction`: [4](#0-3) 

This mirrors the `OmoVault.totalAssets()` bug class exactly: an unprivileged actor registers/creates one bad entry in a set that a critical aggregate read function iterates over, and that single entry's failure propagates to abort the aggregate operation for everyone, with no owner/manager gate on entry creation and no recovery mechanism baked into the aggregate function itself.

### Impact Explanation
This causes a functional, request-triggerable denial of service for the `getProgramAccounts` JSON-RPC method (and by the same mechanism `getTokenAccountsByOwner`/`getTokenAccountsByDelegate`, which route through `get_filtered_program_accounts`/`get_parsed_token_accounts` and the same encode-and-collect pattern) against a specific `program_id`, whenever a caller uses the default or explicitly requested Binary/Base58 encoding. Any tooling, exchange integration, explorer, or wallet backend that queries all accounts under a given program with default encoding will receive a hard RPC error instead of the accounts list, until it changes its encoding — this is analogous to the reported vault DoS in that a single unprivileged actor can degrade service for all consumers of a shared read path.

### Likelihood Explanation
Medium: creating an oversized account under an arbitrary `owner` via `CreateAccount` requires only paying rent for the space and is completely unprivileged; no cooperation from the target program is needed. The trigger condition (default/Binary/Base58 encoding + one account >128 bytes existing under the queried program) is common because Binary is the RPC's documented default for `getProgramAccounts`.

### Recommendation
- Do not let a single account's encoding failure abort the entire `getProgramAccounts`/`getTokenAccountsBy*` response; instead, skip/omit or annotate the problematic account (similar to how `encode_bs58` already degrades gracefully to an `"error: data too large..."` string in `encode_ui_account`), rather than surfacing an `Err` that is propagated through `.collect::<Result<Vec<_>>>()?`.
- Restrict the fast-fail `encode_account` error behavior to single-account endpoints (`getAccountInfo`, `getMultipleAccounts`) where the caller controls exactly which account triggered the failure, and use the non-failing `encode_ui_account` path for all multi-account aggregation RPCs.

### Proof of Concept
1. Use any funded account to call `system_instruction::create_account` with `space = 200` (or any value > `MAX_BASE58_BYTES` = 128) and `owner = <target_program_id>` (e.g., an SPL Token program or any other well-queried program ID) — no cooperation from that program is required.
2. Call `getProgramAccounts(target_program_id)` (with no `encoding` specified, so it defaults to Binary) — the entire call fails with an `InvalidRequest` error ("Encoded binary (base 58) data should be less than 128 bytes...") because `.collect::<Result<Vec<_>>>()?` in `get_program_accounts` (`rpc/src/rpc.rs:656-666`) aborts on the single poisoned account.
3. Any other unprivileged client querying `getProgramAccounts(target_program_id)` with default encoding will receive the same failure until they explicitly pass `Base64`/`JsonParsed` encoding — demonstrating the persistent DoS caused by one unprivileged account creation.

### Citations

**File:** rpc/src/rpc.rs (L611-622)
```rust
        let RpcAccountInfoConfig {
            encoding,
            data_slice: data_slice_config,
            commitment,
            min_context_slot,
        } = config.unwrap_or_default();
        let bank = self.get_bank_with_config(RpcContextConfig {
            commitment,
            min_context_slot,
        })?;
        let encoding = encoding.unwrap_or(UiAccountEncoding::Binary);
        optimize_filters(&mut filters);
```

**File:** rpc/src/rpc.rs (L656-666)
```rust
        } else {
            keyed_accounts
                .into_iter()
                .map(|(pubkey, account)| {
                    Ok(RpcKeyedAccount {
                        pubkey: pubkey.to_string(),
                        account: encode_account(&account, &pubkey, encoding, data_slice_config)?,
                    })
                })
                .collect::<Result<Vec<_>>>()?
        };
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

**File:** rpc/src/rpc.rs (L4117-4133)
```rust
                    Some(
                        config_accounts
                            .addresses
                            .iter()
                            .map(|address_str| {
                                let pubkey = verify_pubkey(address_str)?;
                                get_encoded_account(
                                    bank,
                                    &pubkey,
                                    accounts_encoding,
                                    None,
                                    Some(&post_simulation_accounts_map),
                                )
                            })
                            .collect::<Result<Vec<_>>>()?,
                    )
                }
```
