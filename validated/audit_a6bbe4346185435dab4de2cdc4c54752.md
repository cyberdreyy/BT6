### Title
Use-after-free / stale-pointer memory redirect on account data growth during CPI-triggered access-violation handling - (File: transaction-context/src/transaction.rs)

### Summary
The access-violation handler that lazily grows a writable account's memory region during VM execution can redirect a `MemoryRegion` to a pointer captured from the *old* (about-to-be-invalidated) buffer after the backing account data has already been resized, and the code that is supposed to correct this ("unshare") is gated by a *second*, independent feature flag rather than by the condition that actually caused the invalidation.

### Finding Description
`TransactionContext::access_violation_handler` in [1](#0-0)  is invoked whenever a BPF program (via a syscall or CPI-mapped memory access) writes past the currently-mapped length of an account's data region. When the requested write exceeds `region.len()`, the handler does:

1. `account.resize(new_len, 0)` — this mutates the account's underlying buffer and, in the direct-mapping case, this call can reallocate/move the account's data buffer, per the code's own comment: "In the direct mapping case `account.resize` invalidates the buffer this region has been pointing at" [2](#0-1) .
2. Immediately after, it reads `data_ptr = region.host_buffer().ptr()` — i.e., the pointer stored in the **old**, now potentially-dangling `region`, not a fresh pointer from `account.data()` — and builds a new slice of the *larger* `new_len` size from it, then calls `region.redirect(new_buffer)` [3](#0-2) .
3. Only afterward does the code correct the region to point at the account's real, current buffer via `region.redirect(account.raw_mut_data_slice())` — but this "unshare" fix-up is gated by `if virtual_address_space_adjustments && account_data_direct_mapping` [4](#0-3) , i.e. by **two** feature flags, whereas the actual precondition for the dangling pointer (per the SAFETY comment) is solely `account_data_direct_mapping` being enabled.

If `account_data_direct_mapping` is active while `virtual_address_space_adjustments` is not (or is evaluated independently at any point where these flags can diverge), step 3 is skipped and the `MemoryRegion` is left redirected to a stale/freed host pointer with a length larger than what that freed allocation actually had — a textbook heap-buffer-overflow/use-after-free primitive, directly analogous to the reported WebAudio UAF (a freed buffer whose stale reference is subsequently used, causing heap corruption). Any subsequent syscall or VM store into this account's mapped VM address range would write into invalid/freed heap memory.

### Impact Explanation
If reachable, a subsequent write through the corrupted `MemoryRegion` writes attacker-influenced bytes into freed or unrelated heap memory inside the validator process handling the transaction — heap corruption that can lead to a crash (transaction-triggered node/cluster halt) or, in the worst case, further memory corruption enabling code execution within the SVM/BPF loader's host process. This maps to the "transaction-triggered cluster halt" / heap-corruption class explicitly called out as in-scope.

### Likelihood Explanation
This is the weakest part of the finding: the vulnerability depends entirely on being able to construct a runtime state where `account_data_direct_mapping` is enabled but `virtual_address_space_adjustments` is not (or vice versa in a way that skips the fix-up), and on `account.resize()` actually reallocating (moving) the backing buffer rather than growing in place. I was not able to locate the feature-gate definitions/activation dependencies for `account_data_direct_mapping` and `virtual_address_space_adjustments` in this repo snapshot (my search for these names under `feature_set` files returned no matches), so I cannot confirm whether these two flags are guaranteed to always be activated together on any real cluster, or whether `account.resize()` is guaranteed to grow the buffer in-place (e.g., always over-allocating a padding region so `Vec`/host buffer pointer never moves) in the direct-mapping case. The inline SAFETY commentary in the code strongly suggests the authors were aware resize *can* invalidate the pointer in direct-mapping mode, which is why the "unshare" step exists at all — but its gating condition looks inconsistent with the stated precondition.

### Recommendation
- Gate the "unshare" redirect fix-up (`region.redirect(account.raw_mut_data_slice())`) solely on `account_data_direct_mapping`, matching the actual precondition under which `account.resize()` can invalidate the region's buffer, rather than requiring `virtual_address_space_adjustments` as well.
- Alternatively, avoid taking `data_ptr` from the old `region.host_buffer()` after calling `account.resize()`; instead always derive the post-resize pointer from `account.data()`/`account.raw_mut_data_slice()` directly before constructing `new_buffer`, eliminating the window where a stale pointer can be embedded in the region.
- Add a debug assertion / fuzz test that specifically exercises `account_data_direct_mapping = true` with `virtual_address_space_adjustments = false` (and vice versa) to confirm no combination of these flags can leave a `MemoryRegion` pointing at deallocated memory.

### Proof of Concept
I could not construct or confirm a concrete end-to-end transaction PoC because I was unable to locate the feature-gate wiring (in this repository snapshot) that determines whether `account_data_direct_mapping` and `virtual_address_space_adjustments` can be independently true/false in a live cluster configuration, nor could I confirm the exact reallocation behavior of `account.resize()`/`AccountSharedData` in the direct-mapping path (whether it always pre-reserves padding making the pointer stable, which would fully mitigate this). Given the index size limits on this analysis (I could not fetch `feature_set.rs` contents to check gate ordering/dependencies), a background Devin session with full repository access would be needed to (a) confirm the activation relationship between these two flags on mainnet-beta/testnet, (b) confirm `AccountSharedData::resize`/`BorrowedAccount::resize`'s reallocation semantics under direct mapping, and (c) if confirmed exploitable, build a BPF test program (analogous to `TEST_CPI_ACCOUNT_UPDATE_CALLER_GROWS_CALLEE_SHRINKS`-style tests already in `programs/sbf/rust/invoke/src/lib.rs`) that triggers an out-of-bounds write growth through this exact code path with the flag combination isolated.

### Citations

**File:** transaction-context/src/transaction.rs (L518-625)
```rust
    pub fn access_violation_handler(
        &self,
        virtual_address_space_adjustments: bool,
        account_data_direct_mapping: bool,
    ) -> AccessViolationHandler {
        let accounts = Rc::clone(&self.accounts);
        Box::new(
            move |region: &mut MemoryRegion,
                  address_space_reserved_for_account: u64,
                  access_type: AccessType,
                  vm_addr: u64,
                  len: u64| {
                if access_type == AccessType::Load {
                    return;
                }
                let Some(index_in_transaction) = region.access_violation_handler_payload else {
                    // This region is not a writable account.
                    return;
                };
                let region_vm_addr_start = region.vm_addr_range().start;
                let requested_length = vm_addr
                    .saturating_add(len)
                    .saturating_sub(region_vm_addr_start)
                    as usize;
                if requested_length > address_space_reserved_for_account as usize {
                    // Requested access goes further than the account region.
                    return;
                }

                // The four calls below can't really fail. If they fail because of a bug,
                // whatever is writing will trigger an EbpfError::AccessViolation like
                // if the region was readonly, and the transaction will fail gracefully.
                let Ok(mut account) = accounts.try_borrow_mut(index_in_transaction) else {
                    debug_assert!(false);
                    return;
                };
                if accounts.touch(index_in_transaction).is_err() {
                    debug_assert!(false);
                    return;
                }

                let remaining_allowed_growth = MAX_ACCOUNT_DATA_GROWTH_PER_TRANSACTION
                    .saturating_sub(accounts.resize_delta())
                    .max(0) as usize;

                if requested_length > region.len() {
                    // Realloc immediately here to fit the requested access,
                    // then later in CPI or deserialization realloc again to the
                    // account length the program stored in AccountInfo.
                    let old_len = account.data().len();
                    let new_len = (address_space_reserved_for_account as usize)
                        .min(MAX_ACCOUNT_DATA_LEN as usize)
                        .min(old_len.saturating_add(remaining_allowed_growth));
                    // The last two min operations ensure the following:
                    debug_assert!(accounts.can_data_be_resized(old_len, new_len).is_ok());
                    if accounts
                        .update_accounts_resize_delta(old_len, new_len)
                        .is_err()
                    {
                        return;
                    }

                    account.resize(new_len, 0);
                    let data_ptr = region.host_buffer().ptr() as *mut u8;
                    let new_buffer = std::ptr::slice_from_raw_parts_mut(data_ptr, new_len);
                    unsafe {
                        // SAFETY:
                        //
                        // Contract from `MemoryRegion::redirect`: MemoryRegion must point to a
                        // valid object live for the duration of this `MemoryMapping`.
                        //
                        // Evidence: There are two distinct cases, when the account buffer is
                        // serialized and when the account buffer is directly mapped.
                        // * In the serialization case we continue pointing at the same buffer as
                        // before, and the original buffer must have satisfied the liveness
                        // condition before.
                        // * In the direct mapping case `account.resize` invalidates the buffer this
                        // region has been pointing at, but this is fixed up later in the "unshare"
                        // branch later.
                        // * In the serialization case the section of serialized buffer has the
                        // necessary padding after the account payload proper for resize. This
                        // padding is a part of the originally constructed `MemoryRegion` and is
                        // only later subsliced to not expose it before the first access to the
                        // area (which invokes this handler.)
                        //
                        // Contract from `MemoryRegion::redirect`: For `MemoryRegion`s marked
                        // writable, the host buffer must accept arbitrary bytes being overwritten
                        // without it resulting in unsoundness.
                        //
                        // Evidence: The account payloads dont have any internal soundness
                        // invariants. The buffer in the serialization case starts off and remains
                        // writable (even though the HostBuffer might have been initially created as
                        // immutable.) In the direct mapping case we redirect the region to the
                        // buffer stored in the account later on.
                        region.redirect(new_buffer);
                    }
                }

                // Potentially unshare / make the account shared data unique (CoW logic).
                if virtual_address_space_adjustments && account_data_direct_mapping {
                    unsafe {
                        // SAFETY: refer to the comment above.
                        region.redirect(account.raw_mut_data_slice());
                    }
                }
            },
        )
    }
```
