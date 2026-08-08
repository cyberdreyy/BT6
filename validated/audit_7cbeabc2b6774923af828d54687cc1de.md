[1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** rpc/src/rpc.rs (L642-666)
```rust
            } else {
                self.get_filtered_program_accounts(
                    Arc::clone(&bank),
                    program_id,
                    filters,
                    sort_results,
                )
                .await?
            }
        };
        let accounts = if is_known_spl_token_id(&program_id)
            && encoding == UiAccountEncoding::JsonParsed
        {
            get_parsed_token_accounts(Arc::clone(&bank), keyed_accounts.into_iter()).collect()
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

**File:** rpc/src/rpc.rs (L5728-5741)
```rust
    #[test]
    #[should_panic(expected = "should be less than 128 bytes")] // If ever `MAX_BASE58_BYTES` changes, the expected error message will need to be updated.
    fn test_encode_account_throws_when_data_too_large_to_base58_encode() {
        let data = vec![42; MAX_BASE58_BYTES + 1];
        let pubkey = Pubkey::new_unique();
        let account = AccountSharedData::create_from_existing_shared_data(
            42,
            Arc::new(data),
            pubkey,
            false,
            0,
        );
        let _ = encode_account(&account, &pubkey, UiAccountEncoding::Base58, None).unwrap();
    }
```
