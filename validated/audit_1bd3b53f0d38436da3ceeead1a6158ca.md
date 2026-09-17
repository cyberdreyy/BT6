Based on my investigation, this is a plausible finding: the `#[contract]`/`#[derive(Storable)]` macro system generates per-field storage-slot collision checks, but those checks are dead code that never actually runs — and the only real defense against ink!-style storage-layout clashes in this codebase is this unreachable check.

### Title
Storage slot collision detector (`__check_all_collisions`) is generated but never invoked, so overlapping precompile storage fields go undetected - (File: `crates/common/precompile-macros/src/layout.rs`)

### Summary
Base's native precompiles use a custom `#[contract]` / `#[derive(Storable)]` macro system to lay out EVM storage slots for structs, including ERC-7201 `#[namespace(...)]` roots, `#[base_slot(N)]` resets, and `#[slot(N)]` manual overrides [1](#0-0) . This is exactly the mechanism the ink! report warns is dangerous: multiple independently-declared storage regions computing raw slot numbers that can silently overlap if a developer misuses a manual/namespace override. The macro *does* generate a per-field collision-detection function, `__check_all_collisions`, intended to catch exactly this class of bug [2](#0-1) , and each field gets an individual `__check_collision_<field>` function with the overlap math [3](#0-2) . However, `__check_all_collisions` is never called anywhere in the codebase outside of its own definition — it exists purely as dead code.

### Finding Description
The collision-check function is gated behind `#[cfg(debug_assertions)]` [4](#0-3) , and `debug_assertions` is disabled by default in release builds, which is how production nodes are compiled. Even setting that aside, a grep across the whole repository shows `__check_all_collisions` has no call sites at all — it is generated once per `#[contract]`/namespaced struct but is dead code that the compiler will not even warn about being unused (it's `pub(super)`), so it silently provides zero protection in both debug and release builds. This means the only mechanism designed to catch the exact ink!-style bug class — two independently-declared storage regions (e.g., a `#[base_slot(N)]` override, a `#[slot(N)]` manual slot, or an ERC-7201 `#[namespace(...)]` root) computing overlapping raw EVM slots — is never exercised at runtime or in CI as a real safety net; it would only catch a bug if a developer manually wrote a unit test that explicitly calls the private generated function, which none of the precompile storage modules do (e.g. `B20CoreStorage`, `B20AssetStorage`, `NonceManagerStorage`, `PolicyStorage`, `ActivationStorage` all rely on `#[namespace(...)]` roots and manual packing without invoking this check) [5](#0-4) .

Concretely, if any future or existing precompile struct combines a `#[namespace(...)]` root with sibling `#[base_slot(N)]`/`#[slot(N)]` overrides whose computed absolute slot ranges intersect, the macro would compile successfully and silently produce two logically distinct fields that alias the same storage cell — precisely the "no validations between upgrades to check whether the storage layout changed" problem the ink! report describes, except here it's a same-version, same-compile-unit hazard rather than an upgrade hazard, and the intended compile/runtime guardrail (`__check_all_collisions`) is inert.

### Impact Explanation
If two storage fields of a native precompile (reachable by any transaction sender calling that precompile, e.g. a B20 token creator/holder, nonce-manager caller, or policy caller) silently alias the same slot, writes intended for one logical variable overwrite another. Depending on which fields collide this can corrupt balances/allowances (unbacked supply or fund freezing), corrupt role/policy state (unauthorized privilege), or corrupt nonce/replay-protection bookkeeping (replay of signed operations) — all High/Critical severity outcomes reachable purely by normal precompile calls, with no attacker-side special access required beyond causing (or exploiting a pre-existing) storage layout mistake in a precompile's struct definition.

### Likelihood Explanation
Likelihood is moderate rather than certain: no smoking-gun overlap was found in the currently-declared precompile storage structs I was able to inspect (`B20CoreStorage`, etc., which use sequential auto-allocation and pinned offset tests) [6](#0-5) . The risk is structural/latent: the codebase relies on manual slot arithmetic and namespace roots across many precompiles (`b20_asset`, `b20_stablecoin`, `nonce`, `policy`, `activation`), and the single automated guardrail against a mistake in any of them is non-functional. A future refactor, a new precompile field, or a merge of namespaced sub-storage into a `#[contract]` root could introduce a collision that would compile cleanly and pass all existing tests (since none of them invoke `__check_all_collisions`), and would only be discovered as node state corruption in production.

### Recommendation
- Make `gen_collision_check_fn`/`__check_all_collisions` actually run: call it unconditionally in the generated constructor (`layout::gen_constructor`) or in a `#[test]` generated by the macro for every `#[contract]`/`#[derive(Storable)]` struct, not only under `#[cfg(debug_assertions)]`.
- Alternatively (preferably additionally), turn the check into a `const`/compile-time assertion (`const _: () = assert!(...)`) so overlapping slots fail the build rather than depending on a debug-only runtime call that is never invoked.
- Add a workspace-level test that instantiates every precompile storage struct and asserts `slots::__check_all_collisions()`-equivalent logic actually executes, closing the "generated but dead" gap identified here.

### Proof of Concept
1. Add a new field to any existing `#[contract]`/`#[namespace(...)]` struct (e.g. `crates/common/precompiles/src/common/core_storage.rs`) with an explicit `#[slot(N)]` or `#[base_slot(N)]` that intentionally overlaps an existing auto-allocated field's slot range.
2. Build the workspace in release mode (`cargo build --release`) or run the existing test suite: the crate compiles successfully and all existing unit tests pass, because `__check_all_collisions` (the only code path that would `debug_assert!` on the overlap) is never called anywhere in the codebase.
3. Exercise the two colliding fields via their respective precompile calls (e.g. write field A, read field B) to observe that both fields alias the same underlying EVM storage slot, demonstrating the undetected corruption.

### Citations

**File:** crates/common/precompile-storage/README.md (L6-35)
```markdown

### Auto-allocation

Fields in a `#[contract]` struct are allocated sequentially following Solidity's right-to-left
bin-packing rules. Fields smaller than 32 bytes are packed into the same slot when they fit.

```rust,ignore
#[contract]
pub struct MyToken {
    pub name: String,       // slot 0 (full slot — dynamic)
    pub symbol: String,     // slot 1 (full slot — dynamic)
    pub decimals: u8,       // slot 2, offset 0 (1 byte)
    pub paused: bool,       // slot 2, offset 1 (packed with decimals)
    pub total_supply: U256, // slot 3 (doesn't fit with the 30 remaining bytes)
}
```

### Manual slot override

- `#[slot(N)]` — places the field at an explicit absolute slot with offset 0.
- `#[base_slot(N)]` — resets the auto-allocation chain starting from slot N.
- `#[slot("key")]` — computes `keccak256("key")` at macro expansion time.

### Namespaced layouts

- `#[namespace("id")]` — starts a `#[contract]` field at the ERC-7201 root for `id`.

Multiple fields with the same namespace use normal Solidity offsets from that root without advancing
the surrounding contract layout. `#[slot]` and `#[base_slot]` overrides cannot be combined with
`#[namespace]` on the same field.
```

**File:** crates/common/precompile-macros/src/layout.rs (L273-293)
```rust
fn gen_collision_checks(allocated_fields: &[LayoutField<'_>]) -> proc_macro2::TokenStream {
    let mut generated = proc_macro2::TokenStream::new();
    let mut check_fn_calls = Vec::new();

    for (idx, allocated) in allocated_fields.iter().enumerate() {
        let (check_fn_name, check_fn) =
            packing::gen_collision_check_fn(idx, allocated, allocated_fields);
        generated.extend(check_fn);
        check_fn_calls.push(check_fn_name);
    }

    generated.extend(quote! {
        #[cfg(debug_assertions)]
        #[inline(always)]
        pub(super) fn __check_all_collisions() {
            #(#check_fn_calls();)*
        }
    });

    generated
}
```

**File:** crates/common/precompile-macros/src/packing.rs (L391-447)
```rust
/// Generate collision detection debug assertions for a field against all other fields.
pub(crate) fn gen_collision_check_fn(
    idx: usize,
    field: &LayoutField<'_>,
    all_fields: &[LayoutField<'_>],
) -> (Ident, TokenStream) {
    fn gen_slot_count_expr(ty: &Type) -> TokenStream {
        quote! { ::alloy_primitives::U256::from_limbs([<#ty as ::base_precompile_storage::StorableType>::SLOTS as u64, 0, 0, 0]) }
    }

    let check_fn_name = format_ident!("__check_collision_{}", field.name);
    let consts = PackingConstants::new(field.name);
    let (slot_const, offset_const) = consts.into_tuple();
    let (field_name, field_ty) = (field.name, field.ty);

    let mut checks = TokenStream::new();

    for (other_idx, other_field) in all_fields.iter().enumerate() {
        if other_idx == idx {
            continue;
        }

        let other_consts = PackingConstants::new(other_field.name);
        let (other_slot_const, other_offset_const) = other_consts.into_tuple();
        let other_name = other_field.name;
        let other_ty = other_field.ty;

        let current_count_expr = gen_slot_count_expr(field.ty);
        let other_count_expr = gen_slot_count_expr(other_field.ty);

        checks.extend(quote! {
            {
                let slot = #slot_const;
                let slot_end = slot.checked_add(#current_count_expr).expect("slot range overflow");
                let other_slot = #other_slot_const;
                let other_slot_end = other_slot.checked_add(#other_count_expr).expect("slot range overflow");

                let no_overlap = if slot == other_slot {
                    let byte_end = #offset_const + <#field_ty as ::base_precompile_storage::StorableType>::BYTES;
                    let other_byte_end = #other_offset_const + <#other_ty as ::base_precompile_storage::StorableType>::BYTES;
                    byte_end <= #other_offset_const || other_byte_end <= #offset_const
                } else {
                    slot_end.le(&other_slot) || other_slot_end.le(&slot)
                };

                debug_assert!(
                    no_overlap,
                    "Storage slot collision: field `{}` (slot {:?}, offset {}) overlaps with field `{}` (slot {:?}, offset {})",
                    stringify!(#field_name),
                    slot,
                    #offset_const,
                    stringify!(#other_name),
                    other_slot,
                    #other_offset_const
                );
            }
        });
```

**File:** crates/common/precompiles/src/common/core_storage.rs (L11-98)
```rust
/// Core B-20 storage rooted at the `base.b20` ERC-7201 namespace.
#[derive(Debug, Clone, Storable)]
#[namespace("base.b20")]
pub struct B20CoreStorage {
    /// Mutable token name.
    #[accessor]
    #[mutator]
    pub name: String, // offset 0
    /// Mutable token symbol.
    #[accessor]
    #[mutator]
    pub symbol: String, // offset 1
    /// ERC-7572 contract metadata URI.
    #[accessor]
    #[mutator]
    pub contract_uri: String, // offset 2
    /// Total token supply.
    #[accessor]
    #[mutator]
    pub total_supply: U256, // offset 3
    /// Token balances by account.
    #[accessor(name = balance_of, keys(account))]
    #[mutator(name = set_balance, keys(account), value = balance)]
    pub balances: Mapping<Address, U256>, // offset 4
    /// Spending allowances by owner and spender.
    #[accessor(name = allowance, keys(owner, spender))]
    #[mutator(name = set_allowance, keys(owner, spender), value = amount)]
    pub allowances: Mapping<Address, Mapping<Address, U256>>, // offset 5
    /// Role membership flags by role and account.
    #[accessor(name = has_role, keys(role, account))]
    #[mutator(name = set_role, keys(role, account), value = enabled)]
    pub roles: Mapping<B256, Mapping<Address, bool>>, // offset 6
    /// Admin role configured for each role.
    #[accessor(name = role_admin, keys(role))]
    #[mutator(name = set_role_admin, keys(role), value = admin_role)]
    pub role_admins: Mapping<B256, B256>, // offset 7
    /// Default-admin holder count.
    #[accessor]
    #[mutator]
    pub admin_count: U256, // offset 8
    /// Transfer sender policy ID.
    #[accessor]
    #[mutator]
    pub transfer_sender_policy_id: u64, // slot 9, offset 0
    /// Transfer receiver policy ID.
    #[accessor]
    #[mutator]
    pub transfer_receiver_policy_id: u64, // slot 9, offset 8
    /// Transfer executor policy ID.
    #[accessor]
    #[mutator]
    pub transfer_executor_policy_id: u64, // slot 9, offset 16
    /// Reserved padding to close slot 9.
    pub transfer_reserved_0: u64, // slot 9, offset 24 (filler to close the slot)
    /// Mint receiver policy ID.
    #[accessor]
    #[mutator]
    pub mint_receiver_policy_id: u64, // slot 10, offset 0
    /// Reserved padding to fill the remainder of slot 10.
    pub mint_reserved: FixedBytes<24>, // slot 10, offset 8 (fills remaining 24 bytes)
    /// Paused feature bitmask.
    #[accessor]
    #[mutator]
    pub paused: U256, // offset 11
    /// Maximum total supply.
    #[accessor]
    #[mutator]
    pub supply_cap: U256, // offset 12
    /// EIP-2612 permit nonces by owner.
    #[accessor(name = nonce, keys(owner))]
    #[mutator(name = set_nonce, keys(owner), value = nonce)]
    pub nonces: Mapping<Address, U256>, // offset 13
    // The base-std mock keeps an `initialized` bootstrap flag as its last field; this impl checks
    // factory-init via deployed marker bytecode instead, so it stores no such field.
    /// Seize-exempt policy ID, consulted against `from` by the seize operations. Accounts
    /// authorized by this policy are exempt from seizure; `from` is seizable only when it is NOT
    /// in the scope. The unset always-allow default keeps seizure closed until an issuer
    /// configures it.
    #[accessor]
    #[mutator]
    pub seize_exempt_policy_id: u64, // slot 14, offset 0
    /// Seize-receiver policy ID, consulted against `to` by the seize operations.
    #[accessor]
    #[mutator]
    pub seize_receiver_policy_id: u64, // slot 14, offset 8
    /// Reserved padding to close slot 14.
    pub seize_reserved: FixedBytes<16>, // slot 14, offset 16
}
```

**File:** crates/common/precompiles/src/common/core_storage.rs (L130-177)
```rust
#[cfg(test)]
mod tests {
    use alloy_primitives::{U256, uint};
    use base_precompile_storage::StorableType;

    use super::__packing_b20_core_storage;
    use crate::B20CoreStorage;

    const B20_ROOT: U256 =
        uint!(0xc78b71fee795ddd74aff64ea9b2474194c938c3196430e10bb5f01ed48434000_U256);

    #[test]
    fn b20_namespaces_match_base_std_roots() {
        assert_eq!(<B20CoreStorage as StorableType>::STORAGE_NAMESPACE_ID, "base.b20");
        assert_eq!(<B20CoreStorage as StorableType>::STORAGE_NAMESPACE_ROOT, B20_ROOT);
    }

    #[test]
    fn b20_core_offsets_match_mock_b20_storage() {
        assert_eq!(__packing_b20_core_storage::NAME_LOC.offset_slots, 0);
        assert_eq!(__packing_b20_core_storage::SYMBOL_LOC.offset_slots, 1);
        assert_eq!(__packing_b20_core_storage::CONTRACT_URI_LOC.offset_slots, 2);
        assert_eq!(__packing_b20_core_storage::TOTAL_SUPPLY_LOC.offset_slots, 3);
        assert_eq!(__packing_b20_core_storage::BALANCES_LOC.offset_slots, 4);
        assert_eq!(__packing_b20_core_storage::ALLOWANCES_LOC.offset_slots, 5);
        assert_eq!(__packing_b20_core_storage::ROLES_LOC.offset_slots, 6);
        assert_eq!(__packing_b20_core_storage::ROLE_ADMINS_LOC.offset_slots, 7);
        assert_eq!(__packing_b20_core_storage::ADMIN_COUNT_LOC.offset_slots, 8);
        assert_eq!(__packing_b20_core_storage::TRANSFER_SENDER_POLICY_ID_LOC.offset_slots, 9);
        assert_eq!(__packing_b20_core_storage::TRANSFER_SENDER_POLICY_ID_LOC.offset_bytes, 0);
        assert_eq!(__packing_b20_core_storage::TRANSFER_RECEIVER_POLICY_ID_LOC.offset_slots, 9);
        assert_eq!(__packing_b20_core_storage::TRANSFER_RECEIVER_POLICY_ID_LOC.offset_bytes, 8);
        assert_eq!(__packing_b20_core_storage::TRANSFER_EXECUTOR_POLICY_ID_LOC.offset_slots, 9);
        assert_eq!(__packing_b20_core_storage::TRANSFER_EXECUTOR_POLICY_ID_LOC.offset_bytes, 16);
        assert_eq!(__packing_b20_core_storage::TRANSFER_RESERVED_0_LOC.offset_slots, 9);
        assert_eq!(__packing_b20_core_storage::TRANSFER_RESERVED_0_LOC.offset_bytes, 24);
        assert_eq!(__packing_b20_core_storage::MINT_RECEIVER_POLICY_ID_LOC.offset_slots, 10);
        assert_eq!(__packing_b20_core_storage::MINT_RECEIVER_POLICY_ID_LOC.offset_bytes, 0);
        assert_eq!(__packing_b20_core_storage::PAUSED_LOC.offset_slots, 11);
        assert_eq!(__packing_b20_core_storage::SUPPLY_CAP_LOC.offset_slots, 12);
        assert_eq!(__packing_b20_core_storage::NONCES_LOC.offset_slots, 13);
        assert_eq!(__packing_b20_core_storage::SEIZE_EXEMPT_POLICY_ID_LOC.offset_slots, 14);
        assert_eq!(__packing_b20_core_storage::SEIZE_EXEMPT_POLICY_ID_LOC.offset_bytes, 0);
        assert_eq!(__packing_b20_core_storage::SEIZE_RECEIVER_POLICY_ID_LOC.offset_slots, 14);
        assert_eq!(__packing_b20_core_storage::SEIZE_RECEIVER_POLICY_ID_LOC.offset_bytes, 8);
        assert_eq!(__packing_b20_core_storage::SEIZE_RESERVED_LOC.offset_slots, 14);
        assert_eq!(__packing_b20_core_storage::SEIZE_RESERVED_LOC.offset_bytes, 16);
    }
```
