### Title
`get_mint_owner_and_additional_data` unpacks mint data without verifying the mint account's owner, allowing spoofed `decimals` in `getTokenAccountBalance` - ([File: rpc/src/parsed_token_accounts.rs])

### Summary
`get_mint_owner_and_additional_data` in `rpc/src/parsed_token_accounts.rs` fetches the account referenced by a token account's `mint` field and unpacks its raw bytes as an SPL `Mint` without ever checking that this account is owned by `spl_token_interface::id()` or `spl_token_2022_interface::id()`. Because `StateWithExtensions::<Mint>::unpack` only validates byte length/layout and not program ownership, any account whose data happens to be a well-formed 82-byte (or extension-TLV) buffer will be accepted as a "mint," letting an attacker fully control the reported `decimals` value returned by `getTokenAccountBalance`.

### Finding Description
The call chain is: `getTokenAccountBalance` RPC handler in `rpc/src/rpc.rs` → reads the token account, extracts the embedded `mint` pubkey via `get_token_account_mint` → calls `get_mint_owner_and_additional_data(bank, mint_pubkey)` (used directly, and the analogous path is also exercised in `get_parsed_token_accounts`/`get_parsed_token_account` in `rpc/src/parsed_token_accounts.rs`) [1](#0-0) .

`get_mint_owner_and_additional_data` does:
```
let mint_account = bank.get_account(mint).ok_or_else(...)?;
let mint_data = get_additional_mint_data(bank, mint_account.data())?;
Ok((*mint_account.owner(), mint_data))
``` [2](#0-1) 

It fetches whatever account lives at the `mint` address and immediately hands its raw `data()` to `get_additional_mint_data`, which does:
```
StateWithExtensions::<Mint>::unpack(data)
    .map_err(|_| Error::invalid_params(...))
    .map(|mint| SplTokenAdditionalDataV2 { decimals: mint.base.decimals, ... })
``` [3](#0-2) 

`StateWithExtensions::<Mint>::unpack` only validates that the byte buffer has the correct length/TLV layout for a `Mint`; it performs no check that the account is actually owned by a legitimate SPL Token program. The function *does* return `*mint_account.owner()` alongside the decoded data, but nothing in `get_mint_owner_and_additional_data` or its callers rejects the result when that owner is not `spl_token_interface::id()`/`spl_token_2022_interface::id()` before the `decimals` field is used to build `token_amount_to_ui_amount_v3` output in `rpc/src/rpc.rs`. Consequently, the `decimals`/`uiAmount` reported by `getTokenAccountBalance` can be sourced from an account that is not a genuine SPL mint at all.

An attacker can supply arbitrary bytes at that address without needing to compromise validator/leader/peer state: this is achievable by deploying their own unprivileged BPF program (an ordinary user action) that writes an 82-byte (or extension-TLV) buffer of the attacker's choosing into an account it owns. That account address is then referenced as the `mint` field of an spl-token/spl-token-2022-owned token account the attacker also creates. Both actions are on-chain writes by a single unprivileged client, matching the allowed threat model ("writing on-chain data that is later returned through those APIs").

### Impact Explanation
Calling `getTokenAccountBalance` on the crafted token account returns a `decimals` (and thus `uiAmount`/`uiAmountString`) value chosen entirely by the attacker instead of one derived from a real, program-validated SPL mint. This is a misreporting bug: RPC consumers/integrators that trust `getTokenAccountBalance`'s decimals field to represent a genuine mint's decimal scale can be misled about the token's value scale, matching the "wrong-slot/fork/account data returned" / decoder-misreporting category rather than any consensus-affecting bug — impact is scoped strictly to reported RPC data being inconsistent with any real mint.

### Likelihood Explanation
Feasible and repeatable by a single unprivileged actor: it requires (1) deploying a program that can write an arbitrary byte layout into an account it owns (ordinary permissionless action), (2) creating one spl-token/spl-token-2022 token account whose `mint` field points at that account, and (3) issuing a single `getTokenAccountBalance` RPC call. No validator, leader, or staked-node control, no leaked keys, and no more than one RPC call is needed, so it fits within the allowed unprivileged-attacker model.

### Recommendation
In `get_mint_owner_and_additional_data` (`rpc/src/parsed_token_accounts.rs`), validate `mint_account.owner()` against `spl_token_interface::id()` and `spl_token_2022_interface::id()` before calling `get_additional_mint_data`, and return `Error::invalid_params` ("Invalid param: mint account not owned by a supported Token program") if the owner does not match a recognized SPL Token program id, mirroring the existing "could not find mint" error path.

### Proof of Concept
Rust integration test sketch (using the local test-validator/bank harness used elsewhere in `rpc/src/rpc.rs` tests):
1. Create account `fake_mint` owned by a locally-deployed no-op attacker program; write into it an 82-byte buffer matching `spl_token_interface::state::Mint`'s packed layout with `decimals = 9` (or any attacker-chosen value) but leave the account owner as the attacker program (not `spl_token::id()`/`spl_token_2022::id()`).
2. Create a token account owned by `spl_token::id()` whose first 32 bytes (`mint` field) equal `fake_mint`'s pubkey, and populate remaining fields to look like a valid token account (owner pubkey, amount, state=Initialized).
3. Call `get_mint_owner_and_additional_data(&bank, &fake_mint_pubkey)` (or the full RPC path `get_token_account_balance`/`get_parsed_token_account`).
4. Assert expectation of a fix: the call should return `Err(Error::invalid_params(...))` because `fake_mint`'s owner is not a supported Token program.
5. Current-code assertion (demonstrating the bug): the call instead returns `Ok((attacker_program_id, SplTokenAdditionalDataV2 { decimals: 9, .. }))`, and `getTokenAccountBalance` reports `decimals: 9` even though `fake_mint` is not owned by any real SPL Token program.

### Citations

**File:** rpc/src/parsed_token_accounts.rs (L61-70)
```rust
        let additional_data = get_token_account_mint(account.data()).and_then(|mint_pubkey| {
            mint_data.get(&mint_pubkey).cloned().or_else(|| {
                let (_, data) = get_mint_owner_and_additional_data(&bank, &mint_pubkey).ok()?;
                let data = AccountAdditionalDataV3 {
                    spl_token_additional_data: Some(data),
                };
                mint_data.insert(mint_pubkey, data);
                Some(data)
            })
        });
```

**File:** rpc/src/parsed_token_accounts.rs (L101-107)
```rust
    } else {
        let mint_account = bank.get_account(mint).ok_or_else(|| {
            Error::invalid_params("Invalid param: could not find mint".to_string())
        })?;
        let mint_data = get_additional_mint_data(bank, mint_account.data())?;
        Ok((*mint_account.owner(), mint_data))
    }
```

**File:** rpc/src/parsed_token_accounts.rs (L110-129)
```rust
fn get_additional_mint_data(bank: &Bank, data: &[u8]) -> Result<SplTokenAdditionalDataV2> {
    StateWithExtensions::<Mint>::unpack(data)
        .map_err(|_| {
            Error::invalid_params("Invalid param: Token mint could not be unpacked".to_string())
        })
        .map(|mint| {
            let interest_bearing_config = mint
                .get_extension::<InterestBearingConfig>()
                .map(|x| (*x, bank.clock().unix_timestamp))
                .ok();
            let scaled_ui_amount_config = mint
                .get_extension::<ScaledUiAmountConfig>()
                .map(|x| (*x, bank.clock().unix_timestamp))
                .ok();
            SplTokenAdditionalDataV2 {
                decimals: mint.base.decimals,
                interest_bearing_config,
                scaled_ui_amount_config,
            }
        })
```
