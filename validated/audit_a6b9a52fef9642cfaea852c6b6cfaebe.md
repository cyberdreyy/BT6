### Title
Silent dropping of zero-valued `Multisig.signers` entries causes `UiMultisig.signers` to misrepresent raw account state - ([File: account-decoder/src/parse_token.rs])

### Finding Description
`parse_token_v3`'s Multisig branch builds `UiMultisig.signers` by filtering the raw 11-element `Multisig.signers` array with `filter_map(|pubkey| if pubkey != &Pubkey::default() { Some(...) } else { None })`, unconditionally dropping every slot whose value equals `Pubkey::default()`, while independently reporting the untouched `num_valid_signers: multisig.n` field. [1](#0-0) 

The filter is index-blind: it treats a default-valued entry inside the first `n` "valid" slots identically to a default-valued padding entry beyond `n`. `Pubkey::default()` is the all-zero pubkey, which is the same value as the on-chain System Program address (`11111111111111111111111111111111`), a syntactically valid account pubkey that any unprivileged client can legally place in an instruction's account list. The SPL Token/Token-2022 `InitializeMultisig`/`InitializeMultisig2` instructions copy whatever account pubkeys are supplied as signer accounts directly into the `Multisig.signers` array without rejecting the zero pubkey; the program only validates `m <= n <= MAX_SIGNERS` and that `n` matches the number of signer accounts provided (this validation lives in the external `spl-token`/`spl-token-2022` program, out of scope for this audit, but its normal operation is sufficient to produce the on-chain state described here).

Consequently, an attacker can initialize a multisig with `n = 3` and pass `[signer0, SystemProgram::ID, signer2]` as the three "signer" accounts. The resulting on-chain `Multisig` account has three genuinely configured slots, one of which happens to equal `Pubkey::default()`. When any client later fetches this account via `getAccountInfo` with `jsonParsed` encoding, `parse_token_v3` silently drops the middle entry, returning `signers: [signer0, signer2]` (length 2) alongside `num_valid_signers: 3` — an internally inconsistent, misleading representation of the account's actual configured signer set. No error or fidelity flag is surfaced to indicate that a configured signer was omitted.

### Impact Explanation
This is a decoder-fidelity bug matching Agave's "misreported instruction/token parsed data" bounty category: the JSON-RPC `jsonParsed` view of a multisig account no longer faithfully reflects the raw account bytes, and the mismatch between `signers.len()` and `num_valid_signers` is silently hidden rather than surfaced. Any off-chain consumer (wallets, explorers, custody tooling) relying on `UiMultisig.signers` to determine "who are the configured signers" for this account will get an incomplete/wrong list, potentially miscounting or misidentifying signer authority for that multisig.

### Likelihood Explanation
This requires only two unprivileged, single-call actions: (1) submit one `InitializeMultisig`/`InitializeMultisig2` transaction naming the System Program address as one of the signer accounts (no elevated privilege, staking, or validator control needed), and (2) issue a single `getAccountInfo` RPC call with `jsonParsed` encoding on that account. Both are ordinary client operations well within the stated attacker capability (one write + one read, no gossip/validator control), making this fully feasible and repeatable.

### Recommendation
Do not filter `Multisig.signers` by value. Instead, truncate strictly by count using `multisig.n` (`signers[..multisig.n as usize]`), and report each of the first `n` entries verbatim (including any that happen to equal `Pubkey::default()`), so `UiMultisig.signers.len()` always equals `num_valid_signers` and the parsed output is a faithful positional representation of the raw account data.

### Proof of Concept
```rust
// account-decoder/src/parse_token.rs (test module)
#[test]
fn test_parse_multisig_with_default_signer_interleaved() {
    let signer0 = Pubkey::new_from_array([1; 32]);
    let signer2 = Pubkey::new_from_array([3; 32]);
    let mut signers = [Pubkey::default(); 11];
    signers[0] = signer0;
    // signers[1] left as Pubkey::default() -- simulates SystemProgram::ID
    // deliberately used as the 2nd "real" signer account passed to InitializeMultisig
    signers[2] = signer2;

    let mut multisig_data = vec![0; Multisig::get_packed_len()];
    let mut multisig = Multisig::unpack_unchecked(&multisig_data).unwrap();
    multisig.m = 2;
    multisig.n = 3; // three slots are "valid" per on-chain config
    multisig.is_initialized = true;
    multisig.signers = signers;
    Multisig::pack(multisig, &mut multisig_data).unwrap();

    let parsed = parse_token_v3(&multisig_data, None).unwrap();
    if let TokenAccountType::Multisig(ui) = parsed {
        assert_eq!(ui.num_valid_signers, 3);
        // BUG: signers.len() is 2, not 3 -- the configured default-valued
        // signer slot was silently dropped instead of being preserved or
        // the inconsistency being surfaced.
        assert_eq!(ui.signers.len(), ui.num_valid_signers as usize,
            "UiMultisig.signers length does not match num_valid_signers: {:?}", ui.signers);
    } else {
        panic!("expected Multisig variant");
    }
}
```
This test currently fails (`ui.signers.len() == 2` vs expected `3`), demonstrating that the parser silently produces a `signers` list inconsistent with `num_valid_signers` for a legitimately-configured multisig containing a zero-valued signer slot.

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
