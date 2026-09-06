### Title
PoX reward-set parsing silently substitutes an all-zero signing key for malformed `signer` buffers, seeding a phantom, unreachable signer weight into the Nakamoto signer set - (File: stackslib/src/chainstate/nakamoto/signer_set.rs)

### Summary
`RawRewardSetEntry::from_pox_4_tuple` and `StakeEntryIteratorPox5::fallible_next` both silently substitute `[0u8; SIGNERS_PK_LEN]` (an all-zero 33-byte "signing key") whenever the `signer` buffer returned by the PoX contract is not exactly 33 bytes, instead of rejecting or skipping the entry. This is the direct analog of the "hat owned by `address(0)`" bug class: a degenerate sentinel identity is silently accepted into a security-critical set (the Nakamoto signer set) rather than being rejected, and that identity can never actually produce a valid signature, because `[0;33]` is not a valid compressed secp256k1 point.

### Finding Description
In the PoX-4 path: [1](#0-0) [2](#0-1) 

```rust
// (buff 33) only enforces max size, not min size, so we need to do a len check
let pk_bytes = if signer.len() == SIGNERS_PK_LEN {
    let mut bytes = [0; SIGNERS_PK_LEN];
    bytes.copy_from_slice(signer.as_slice());
    bytes
} else {
    [0; SIGNERS_PK_LEN]
};
```

The comment itself acknowledges that Clarity's `(buff 33)` type only bounds the *maximum* length, not the minimum, so a stacker can supply a `signer` buffer shorter than 33 bytes at `stack-stx`/`stack-extend` time. Rather than rejecting such a malformed entry, the code coerces it into an all-zero 33-byte key, which is then wrapped in `Ok(Self { signer: Some(pk_bytes), .. })` and flows straight into `StacksChainState::make_reward_set` as a legitimate `NakamotoSignerEntry`.

The identical pattern exists in the PoX-5 walking iterator: [3](#0-2) 

```rust
.expect_buff(SIGNERS_PK_LEN)
.map_err(|_| {
    PoxEntryParsingError::Skip(format!(
        "get-signer-info value should be (buff {SIGNERS_PK_LEN})"
    ))
})?;
let signer_key: [u8; SIGNERS_PK_LEN] =
    signer_key_buff.try_into().unwrap_or([0; SIGNERS_PK_LEN]);
```

Here `expect_buff` only errors on buffers *longer* than 33 bytes; a shorter buffer passes the Clarity type check, and the subsequent `try_into().unwrap_or([0; SIGNERS_PK_LEN])` again defaults to the all-zero key on any length mismatch instead of routing through the `Skip` error path that the surrounding code otherwise carefully uses for malformed entries.

This zero-key entry is subsequently treated as a normal signer:
- In `pox_5_make_signer_set`, entries are grouped `signer_set.entry(entry.signer_key)...`, so every stacker who (deliberately or accidentally) supplies a malformed `signer` buffer is merged into the *same* zero-key bucket, accumulating stake and being assigned real weight via the largest-remainder apportionment ( [4](#0-3) , [5](#0-4) ).
- The resulting `NakamotoSignerEntry { signing_key: [0;33], weight, .. }` is written into the `.signers` contract and becomes part of the `reward_set` used for `verify_signer_signatures`.

Critically, `[0u8;33]` is **not** a valid compressed secp256k1 public key (valid compressed points must start with `0x02` or `0x03`). `Secp256k1PublicKey::recover_to_pubkey_without_validating_low_s`, used both in block signature verification ( [6](#0-5) ) and in `stacks-signer`'s weight accounting ( [7](#0-6) ), always recovers a valid curve point on success, so no signature will ever recover to `[0;33]`. The weight assigned to this phantom entry can therefore never be "signed for" by anyone — it is permanently unreachable.

### Impact Explanation
This breaks the safety/liveness equality the threshold computation relies on: `total_weight` in `NakamotoBlockHeader::compute_voting_weight_threshold` ( [8](#0-7) ) includes weight that is provably unattainable, because it was allocated to a key nobody can control. If enough malformed-`signer` stackers accumulate (individually or via merging into the shared zero bucket) to exceed the blocking-minority margin (`total_weight - threshold`, i.e. >30% of total weight), then no coalition of legitimate signers can ever reach the 70% signing threshold, and Nakamoto block production for that reward cycle is permanently wedged — no valid block can be finalized. This matches the High-impact category: "a signer wedged into never signing valid blocks, acting on a stale reward set/threshold," except here it is the entire signer set (not just one signer) that is wedged, due to an unreachable threshold baked into the on-chain `.signers` state for the cycle.

### Likelihood Explanation
Triggering this requires only a single PoX stacker (not a majority of signers) to submit a `signer` buffer whose length is anything other than exactly 33 bytes while otherwise satisfying the PoX-4/PoX-5 authorization checks (which, per `verify-signer-key-sig`'s `signer-sig-opt = none` branch, do not require the `signer-key` value itself to be validated as a real secp256k1 point — only that a matching pre-registered authorization exists). This is directly analogous to "an admin accidentally/intentionally minting to address(0)" in the original report: a single, non-majority actor can plant a degenerate identity that the downstream weight/threshold logic blindly trusts. The severity scales with how much stake ends up misrouted to the zero-key bucket, but even a moderate accumulation meaningfully raises the effective bar for the honest majority.

### Recommendation
- In `RawRewardSetEntry::from_pox_4_tuple`, reject (or explicitly skip via a `PoxEntryParsingError`-style mechanism) any `signer` buffer whose length is not exactly `SIGNERS_PK_LEN` (33), instead of defaulting to `[0; SIGNERS_PK_LEN]`.
- In `StakeEntryIteratorPox5::fallible_next`, replace `signer_key_buff.try_into().unwrap_or([0; SIGNERS_PK_LEN])` with a proper `Skip`-error path (consistent with the rest of the function's error handling) when the length check fails.
- Additionally, validate that any accepted `signing_key` bytes decode to a valid compressed secp256k1 public key (prefix `0x02`/`0x03` and on-curve) before admitting the entry into the reward set, so that no entry with unattainable/uncontrollable weight can ever be written to the `.signers` contract or consumed by `verify_signer_signatures`.

### Proof of Concept
1. As a PoX-4/PoX-5 stacker, call `stack-stx` (or the delegated equivalent) supplying a `signer-key` buffer of length ≠ 33 (Clarity's `(buff 33)` type only bounds the max length, so e.g. a 1-byte buffer type-checks), paired with a `signer-sig-opt` of `none` and a previously self-registered `signer-key-authorizations` entry for that same short buffer (see `verify-signer-key-sig`'s no-signature branch in `stackslib/src/chainstate/stacks/boot/pox-4.clar` lines 749-761).
2. At the next prepare-phase boundary, `NakamotoSigners::pox_4_compute_and_update_signers` walks the reward slots via `get_pox_4_reward_slots` → `RawRewardSetEntry::from_pox_4_tuple`, which coerces the short buffer to `[0;33]` and includes it (with real weight based on the stacker's `total-ustx`) in `signers_list`, which is written into the `.signers` boot contract and the Rust-side `RewardSet`.
3. Repeat with enough stacked STX (or have multiple stackers do the same, all merging into the shared zero-key bucket in the PoX-5 path) so that the resulting phantom weight exceeds `total_weight - compute_voting_weight_threshold(total_weight)`.
4. No signature can ever be produced that `Secp256k1PublicKey::recover_to_pubkey_without_validating_low_s` maps to `[0;33]` (invalid curve point), so `verify_signer_signatures` (`stackslib/src/chainstate/nakamoto/mod.rs`) can never accumulate enough real weight to clear the threshold for that reward cycle — block production wedges.

### Citations

**File:** stackslib/src/chainstate/nakamoto/signer_set.rs (L297-317)
```rust
        let signer = tuple_data
            .remove("signer")
            .ok_or_else(|| {
                ChainstateError::Expects(
                    "no 'signer' in return value from (pox-4.get-reward-set-pox-address)".into(),
                )
            })?
            .expect_buff(SIGNERS_PK_LEN).map_err(|_| {
                ChainstateError::Expects(
                    format!("'signer' in return value from (pox-4.get-reward-set-pox-address) is not a buff of length {SIGNERS_PK_LEN}"),
                )
            })?;

        // (buff 33) only enforces max size, not min size, so we need to do a len check
        let pk_bytes = if signer.len() == SIGNERS_PK_LEN {
            let mut bytes = [0; SIGNERS_PK_LEN];
            bytes.copy_from_slice(signer.as_slice());
            bytes
        } else {
            [0; SIGNERS_PK_LEN]
        };
```

**File:** stackslib/src/chainstate/nakamoto/signer_set.rs (L415-422)
```rust
            .expect_buff(SIGNERS_PK_LEN)
            .map_err(|_| {
                PoxEntryParsingError::Skip(format!(
                    "get-signer-info value should be (buff {SIGNERS_PK_LEN})"
                ))
            })?;
        let signer_key: [u8; SIGNERS_PK_LEN] =
            signer_key_buff.try_into().unwrap_or([0; SIGNERS_PK_LEN]);
```

**File:** stackslib/src/chainstate/nakamoto/signer_set.rs (L850-856)
```rust
            total_ustx_locked += entry.amount_ustx;

            signer_set
                .entry(entry.signer_key)
                .and_modify(|existing_entry| *existing_entry += entry.amount_ustx)
                .or_insert_with(|| entry.amount_ustx);
        }
```

**File:** stackslib/src/chainstate/nakamoto/signer_set.rs (L913-927)
```rust
        let mut signer_set: Vec<_> = apportioned
            .into_iter()
            .filter_map(|entry| {
                if entry.weight == 0 {
                    return None;
                }
                let weight = u32::try_from(entry.weight)
                    .expect("CORRUPTION: Stacker claimed > u32::max() reward slots");
                Some(NakamotoSignerEntry {
                    signing_key: entry.signing_key,
                    stacked_amt: entry.stacked_amt,
                    weight,
                })
            })
            .collect();
```

**File:** stackslib/src/chainstate/nakamoto/mod.rs (L1120-1124)
```rust
        let strict_order = epoch_id.enforces_strict_signature_order();

        let total_weight = reward_set
            .total_signing_weight()
            .map_err(|_| ChainstateError::NoRegisteredSigners(0))?;
```

**File:** stackslib/src/chainstate/nakamoto/mod.rs (L1133-1143)
```rust
        for signature in self.signer_signature.iter() {
            let public_key = Secp256k1PublicKey::recover_to_pubkey_without_validating_low_s(
                message.bits(),
                signature,
            )
            .map_err(|_| {
                ChainstateError::InvalidStacksBlock(format!(
                    "Unable to recover public key from signature {}",
                    signature.to_hex()
                ))
            })?;
```

**File:** stacks-signer/src/v0/signer.rs (L2483-2492)
```rust
                let Ok(public_key) = Secp256k1PublicKey::recover_to_pubkey_without_validating_low_s(
                    block_hash.bits(),
                    &sig,
                ) else {
                    return None;
                };
                let addr = StacksAddress::p2pkh(self.mainnet, &public_key);
                Some((addr, sig))
            })
            .collect();
```
