### Title
Multisig signer list silently drops zero-valued pubkeys without adjusting `num_valid_signers`, causing decoded output to misrepresent raw account state - ([File: account-decoder/src/parse_token.rs])

### Summary
In `parse_token_v3`'s `Multisig` branch, the `signers` field is built via a `filter_map` that silently discards any signer entry equal to `Pubkey::default()`, while `num_valid_signers` is populated directly from the raw `multisig.n` field with no adjustment. If an SPL Token/Token-2022 `Multisig` account is initialized with `n` counting a zeroed signer slot, the JSON-RPC `jsonParsed` response will report a `signers` array shorter than `num_valid_signers`, misrepresenting the actual multisig configuration to any RPC consumer.

### Finding Description
`parse_token_v3` unpacks a 355-byte account as `Multisig` and constructs `UiMultisig` directly from the raw struct fields: [1](#0-0) 

`num_required_signers` and `num_valid_signers` are copied verbatim from `multisig.m` and `multisig.n`, but the `signers` vector is derived with a `filter_map` that excludes any `Pubkey::default()` entry from the `signers` array without decrementing the reported count. The SPL Token program's `InitializeMultisig` instruction (external dependency, but its effect is simply writing raw account bytes that this decoder later reads) does not forbid a signer pubkey of all zeros — it only enforces `1 <= m <= n <= MAX_SIGNERS`. An unprivileged user can therefore create (via normal, permissionless instructions against the already-deployed Token program) a `Multisig` account whose `n` field counts a slot equal to `Pubkey::default()`.

When this account is later fetched via `getAccountInfo`/`getProgramAccounts` with `encoding: jsonParsed`, the decoder returns a `UiMultisig` where `signers.len() < num_valid_signers`, with no error, warning, or field indicating the discrepancy. This breaks the invariant that the parsed representation faithfully reflects the raw account: an integrator reading only `signers` (rather than cross-checking `signers.len()` against `num_valid_signers`) will undercount the actual number of registered signer slots and may draw incorrect trust/authorization conclusions from the RPC response.

### Impact Explanation
This is a decoder misreporting bug in `account-decoder`, reachable by any client issuing a single `getAccountInfo` (or `getProgramAccounts`) call with `jsonParsed` encoding against an attacker-controlled account. It causes wrong/inconsistent account data to be returned from an RPC query — the parsed `signers` list does not match the declared `num_valid_signers`, which can mislead downstream integrators about a multisig's configuration. This falls under the "decoder panic or misreporting" / "wrong-slot/fork/account data returned" category accepted by the audit scope.

### Likelihood Explanation
Feasible and fully repeatable: the attacker needs only to invoke the standard, already-deployed SPL Token program's `InitializeMultisig` instruction (a normal permissionless operation) supplying `Pubkey::default()` as one of the `n` counted signer accounts — no validator, leader, or privileged access is required. Once the account exists on-chain, every subsequent `jsonParsed` `getAccountInfo` call by any client will reproduce the mismatch.

### Recommendation
In `parse_token_v3`'s Multisig branch, do not silently drop `Pubkey::default()` entries. Either (a) include all `n` signer entries verbatim in the `signers` array (even if `Pubkey::default()`), preserving `signers.len() == num_valid_signers`, or (b) if zero entries are intentionally filtered, recompute `num_valid_signers` as the length of the filtered `signers` vector so the two fields stay consistent.

### Proof of Concept
```rust
// account-decoder/src/parse_token.rs (test module)
use spl_token_2022_interface::state::Multisig;
use solana_pubkey::Pubkey;
use solana_program_pack::Pack;

#[test]
fn test_parse_multisig_with_default_signer_misreports_count() {
    let mut signers = [Pubkey::default(); 11];
    signers[0] = Pubkey::default();          // zeroed slot, but counted in n
    signers[1] = Pubkey::new_unique();       // real signer

    let multisig = Multisig {
        m: 1,
        n: 2,                                // declares 2 valid signers
        is_initialized: true,
        signers,
    };

    let mut data = vec![0u8; Multisig::get_packed_len()];
    Multisig::pack(multisig, &mut data).unwrap();

    let parsed = parse_token_v3(&data, None).unwrap();
    if let TokenAccountType::Multisig(ui_multisig) = parsed {
        assert_eq!(ui_multisig.num_valid_signers, 2);
        // BUG: only 1 signer is actually returned, mismatching num_valid_signers
        assert_ne!(ui_multisig.signers.len(), ui_multisig.num_valid_signers as usize);
    } else {
        panic!("expected Multisig variant");
    }
}
```
Expected (fixed) behavior: `ui_multisig.signers.len() == ui_multisig.num_valid_signers as usize`, either by preserving the zero entry or by decrementing `num_valid_signers` to match the filtered list.

### Citations

**File:** account-decoder/src/parse_token.rs (L91-109)
```rust
    if data.len() == Multisig::get_packed_len() {
        let multisig = Multisig::unpack(data)
            .map_err(|_| ParseAccountError::AccountNotParsable(ParsableAccount::SplToken))?;
        Ok(TokenAccountType::Multisig(UiMultisig {
            num_required_signers: multisig.m,
            num_valid_signers: multisig.n,
            is_initialized: multisig.is_initialized,
            signers: multisig
                .signers
                .iter()
                .filter_map(|pubkey| {
                    if pubkey != &Pubkey::default() {
                        Some(pubkey.to_string())
                    } else {
                        None
                    }
                })
                .collect(),
        }))
```
