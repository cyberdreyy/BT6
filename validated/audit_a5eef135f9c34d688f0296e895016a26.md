### Title
`parse_token_v3` Multisig branch drops legitimate signer pubkeys equal to `Pubkey::default()`, causing `num_valid_signers != signers.len()` - (File: `account-decoder/src/parse_token.rs`)

### Summary
In the `Multisig` branch of `parse_token_v3`, the `signers` vector is built by filtering the fixed 11-slot `Multisig.signers` array with `pubkey != &Pubkey::default()`, rather than slicing the first `n` entries. If any of the `n` "valid" signer slots legitimately holds the all-zero pubkey, it is silently dropped from the returned list while `num_valid_signers` (`multisig.n`) still reports the original count, producing an internally inconsistent `UiMultisig`.

### Finding Description
The relevant code is: [1](#0-0) 

The SPL Multisig account layout stores `n` valid signer pubkeys in the first `n` slots of a fixed 11-element array, with the remaining `11 - n` slots zero-padded by convention. `parse_token_v3` should therefore only take `signers[0..n]`, but instead it filters the *entire* 11-slot array on `pubkey != Pubkey::default()`. This conflates "unused padding slot" with "a real signer whose recorded pubkey happens to be the zero pubkey."

The precondition — a real signer slot holding `Pubkey::default()` — is reachable by an unprivileged actor: `spl-token`/`spl-token-2022`'s `InitializeMultisig` instruction processing copies the `key` of each supplied signer `AccountInfo` into `multisig.signers[i]` without validating that the pubkey is non-zero, and without requiring that account to be a transaction signer at multisig-creation time. `Pubkey::default()` corresponds to the well-known System Program address (`11111111111111111111111111111111`), which any client can list as an account in the `InitializeMultisig` instruction's signer-pubkey list. This lets an attacker create a multisig with, e.g., `n = 3`, where one of the three recorded signer entries is `Pubkey::default()`.

When this account is later fetched via `getAccountInfo?encoding=jsonParsed` (or any RPC path that invokes `parse_token_v3`), the returned `UiMultisig.signers` will contain only 2 entries while `num_valid_signers` still reports 3, hiding the legitimately configured (zero) signer and violating the implicit invariant that `num_valid_signers == signers.len()`.

### Impact Explanation
This is an Agave-owned decoding bug (`account-decoder` crate) that returns **wrong/inconsistent account data** to RPC clients for a query that requires no special privilege — matching the "wrong ... account data returned" category permitted by the Validate section. Any integrator or wallet that trusts `numValidSigners == signers.length` to reconstruct multisig membership will undercount signers and misreport multisig configuration, which is a correctness/decoder-misreporting issue rather than a consensus or crash bug.

### Likelihood Explanation
Feasibility is high and fully attacker-controlled: any client can call `InitializeMultisig` once, including the System Program's address (`Pubkey::default()`) as one of the signer accounts, since SPL Token does not reject the zero pubkey and does not require the listed signer accounts to co-sign the initialization transaction. The subsequent `getAccountInfo` call is a single, ordinary, rate-compliant JSON-RPC request. The bug is deterministic and 100% repeatable given this on-chain state.

### Recommendation
Fix `parse_token_v3` to only take the first `multisig.n` entries of the `signers` array instead of filtering the whole fixed-size array by zero-pubkey exclusion, e.g.:
```rust
signers: multisig
    .signers
    .iter()
    .take(multisig.n as usize)
    .map(|pubkey| pubkey.to_string())
    .collect(),
```
This preserves the intended semantics (first `n` slots are valid signers, remainder is padding) and guarantees `signers.len() == num_valid_signers` regardless of the actual pubkey values recorded.

### Proof of Concept
```rust
#[test]
fn test_parse_token_multisig_zero_signer_inconsistency() {
    let signer1 = Pubkey::new_from_array([1; 32]);
    let signer2 = Pubkey::default(); // legitimately recorded zero pubkey
    let signer3 = Pubkey::new_from_array([3; 32]);
    let mut multisig_data = vec![0; Multisig::get_packed_len()];
    let mut signers = [Pubkey::default(); 11];
    signers[0] = signer1;
    signers[1] = signer2; // real slot, but zero value
    signers[2] = signer3;
    let mut multisig = Multisig::unpack_unchecked(&multisig_data).unwrap();
    multisig.m = 2;
    multisig.n = 3; // 3 valid signers, one of which is the zero pubkey
    multisig.is_initialized = true;
    multisig.signers = signers;
    Multisig::pack(multisig, &mut multisig_data).unwrap();

    let parsed = parse_token_v3(&multisig_data, None).unwrap();
    if let TokenAccountType::Multisig(ui_multisig) = parsed {
        // BUG: signers.len() == 2 while num_valid_signers == 3
        assert_eq!(ui_multisig.num_valid_signers, 3);
        assert_ne!(ui_multisig.signers.len(), ui_multisig.num_valid_signers as usize);
    } else {
        panic!("expected Multisig variant");
    }
}
```
This test packs a `Multisig` with `n = 3` where one valid slot is `Pubkey::default()`, and demonstrates that `parse_token_v3` returns `signers.len() == 2 != num_valid_signers == 3`, confirming the inconsistency.

### Citations

**File:** account-decoder/src/parse_token.rs (L94-109)
```rust
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
