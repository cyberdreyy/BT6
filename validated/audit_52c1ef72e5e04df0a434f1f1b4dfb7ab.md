## Analysis: Order tag reuse — analog of the OrderNFT ring-buffer index reuse bug

The Clober bug class is: an entity (NFT owner/approval) retains control tied to an *index* that can later be silently recycled to reference a completely different, unrelated object, because the index space is finite and reused without invalidating stale references. The closest reachable analog in marginfi-v2 is the `Order`/`Balance` **tag** mechanism, which plays the same structural role as the ring-buffer `orderIndex`.

### Root cause

Each `Balance` slot carries a `tag: u16` used purely to let an `Order` reference "its" two balances (asset + liability) without storing bank pubkeys directly [1](#0-0) . Tags are drawn from a single 16-bit space per account and allocated by `reserve_n_tags`, which walks forward from `last_tag_used` and skips only tags currently held by an **active balance**: [2](#0-1) 

Critically, the "used" set is computed only from `lending_account.balances` — it does **not** consult any live `Order` accounts' `tags` field. An `Order` can become "orphaned" from its balance while remaining active (`active_orders` not decremented) in at least one documented path: `SetKeeperCloseFlags`, which zeroes a balance's `tag` while the `Order` itself stays open with `active_orders == 1` and `order.tags` still containing the now-unused value: [3](#0-2) 

The same "balance's tag zeroed but Order stays alive with a stale tag" state also arises whenever a balance is fully closed via `withdraw_all` (`Balance::close()` resets the whole struct including `tag` to 0), as explicitly acknowledged in the project's own docs: [4](#0-3) 

Once a tag value is free (no active balance holds it), it becomes eligible for reassignment to an entirely unrelated balance the next time `reserve_n_tags` cycles around to it (the `u16` counter wraps via `wrapping_add`). At that point, a **stale `Order` whose `order.tags` still contains that value will start matching an unrelated `Balance`** it was never created against — because every consumer resolves the relationship purely by tag equality:

- `get_tagged_account_health_components` matches `balance.tag` against `order.tags` to compute the order's net health at `StartExecuteOrder` [5](#0-4) , called from [6](#0-5) 
- `ExecuteOrderRecord::initialize` **excludes** any balance whose tag is in `order_tags` from the "must remain unchanged" invariant set that protects all of the user's *other* positions during Keeper execution [7](#0-6) 

This second point is the financially meaningful consequence: `ExecuteOrderRecord` is the mechanism that guarantees a Keeper, who is granted temporary authority to withdraw/repay on the user's behalf during `StartExecuteOrder`/`EndExecuteOrder` [8](#0-7) , can only touch the two balances belonging to the order being executed — every other balance is recorded and later checked to be byte-for-byte unchanged. If a stale, orphaned `Order`'s tag gets silently reassigned (via tag-space wraparound) to a completely unrelated balance, that unrelated balance is wrongly treated as "belongs to this order" and is **excluded from the unchanged-invariant check**, letting a Keeper interaction touch/drain it without the safety net catching the deviation.

### Why this differs from the Spearbit-fixed scenario already covered by tests

The project's regression test `limit_orders_overlap_ab_close_a_reopen_a_ad_fails` only verifies that a **freshly reopened same-bank balance** doesn't reuse the *immediately preceding* order's tag [9](#0-8) . This test does not (and cannot easily) cover the true ring-buffer analog: tag-space wraparound reassigning a stale tag to a *different, unrelated bank's* balance while the original `Order` remains active and un-closed. `reserve_n_tags`'s "used" check has no knowledge of live `Order` accounts at all, so nothing prevents this at allocation time.

### Likelihood

Exploiting this requires driving `last_tag_used` all the way around the 16-bit space (up to ~65,535 `PlaceOrder` calls that consume a fresh tag) to land back on the specific orphaned tag value, which is expensive but fully permissionless, deterministic, and entirely controllable by the account owner (no privileged access, no oracle manipulation, no third-party cooperation required beyond a colluding/careless Keeper). This is a probabilistically low-frequency but concretely reachable and repeatable griefing/theft primitive purely from unprivileged user instructions (`PlaceOrder`, `SetKeeperCloseFlags`, `CloseOrder`/withdraw_all cycles), matching the "unprivileged-user" scope requirement.

### Recommendation

- When zeroing a balance's tag (in `SetKeeperCloseFlags`, `Balance::close`, or any other path), also invalidate/close any `Order` still referencing that tag, or refuse the zeroing while `active_orders` referencing it exist.
- Have `reserve_n_tags` also exclude tags referenced by any live `Order` account (not just active `Balance.tag` values), analogous to the Clober fix of tracking "not yet minted / already burned" indices as invalid rather than relying solely on the ring-buffer's current occupancy.
- Alternatively, widen the tag identifier (e.g., to a 64-bit monotonically increasing global/per-account counter that never wraps in practice) so recycling within the account's realistic lifetime is infeasible.

### Proof of Concept (conceptual)

1. User creates `Order` on banks `(A, D)`; `PlaceOrder` reserves tag `X` for balance A, `Y` for balance D [10](#0-9) .
2. User calls `SetKeeperCloseFlags` on bank `A`, zeroing balance A's tag to `0` while the `Order` (tags `[X, Y]`) remains active [3](#0-2) .
3. User repeatedly opens and tag-consumes/frees other orders (place + close cycles) until `last_tag_used` wraps around the `u16` space back to `X`, at which point `reserve_n_tags` assigns `X` to an unrelated balance `E` on a fresh `PlaceOrder(E, F)` call, since nothing currently holds tag `X` [11](#0-10) .
4. The stale `Order(A,D)` (still open, tags `[X,Y]`) now spuriously matches balance `E` by tag. During a Keeper's `StartExecuteOrder`/`EndExecuteOrder` for the *new* `Order(E,F)`, `ExecuteOrderRecord::initialize` will skip balance `E` from the unchanged-invariant set purely because `balance.tag == X` is in `order_tags`, even though `E` has no logical relation to the stale order — enabling drift/inconsistency in the invariant that is supposed to guarantee uninvolved positions are untouched during Keeper-driven execution.

### Citations

**File:** type-crate/src/types/user_account.rs (L282-306)
```rust
pub struct Balance {
    /// Whether this balance slot is in use (nonzero = active)
    pub active: u8,
    /// The bank this balance corresponds to
    pub bank_pk: Pubkey,
    /// Inherited from the bank when the position is first created and CANNOT BE CHANGED after that.
    /// Note that all balances created before the addition of this feature use `ASSET_TAG_DEFAULT`
    pub bank_asset_tag: u8,
    /// Tag used by orders to reference this balance (0 means unused/unassigned).
    /// A tag may also have a non-zero value while having no orders.
    pub tag: u16,
    pub _pad0: [u8; 4],
    /// The user's asset (deposit) shares in the bank. Multiply by `bank.asset_share_value` for
    /// the token amount.
    pub asset_shares: WrappedI80F48,
    /// The user's liability (borrow) shares in the bank. Multiply by `bank.liability_share_value`
    /// for the token amount.
    pub liability_shares: WrappedI80F48,
    /// Unclaimed emissions rewards for this position
    pub emissions_outstanding: WrappedI80F48,
    /// Unix timestamp (u64) of the last emissions calculation for this position
    pub last_update: u64,
    /// Reserved for future use
    pub _padding: [u64; 1],
}
```

**File:** programs/marginfi/src/state/marginfi_account.rs (L838-850)
```rust
        check_eq!(
            balance.bank_pk,
            *bank_ai.key,
            MarginfiError::InvalidBankAccount
        );

        let num_accounts = get_remaining_accounts_per_bank(&bank)?;

        if !balance_tags.contains(&balance.tag) {
            account_index += num_accounts;
            heap_restore(heap_checkpoint);
            continue;
        }
```

**File:** programs/marginfi/src/state/marginfi_account.rs (L1409-1446)
```rust
    /// Finds n free tags for new orders, starting with newer ones first
    /// n is expected to be <= [`ORDER_ACTIVE_TAGS`].
    /// It fills only the first n, leaving the rest as 0.
    fn reserve_n_tags(&mut self, n: usize) -> [u16; ORDER_ACTIVE_TAGS] {
        assert!(n <= ORDER_ACTIVE_TAGS, "Invalid tag count");

        let used: BTreeSet<u16> = self
            .balances
            .iter()
            .filter(|b| b.is_active() && b.tag != 0)
            .map(|b| b.tag)
            .collect();

        let mut tags = [0u16; ORDER_ACTIVE_TAGS];

        let mut next = self.last_tag_used.wrapping_add(1);

        let mut filled = 0;

        while filled < n {
            if next == 0 {
                next = 1;
            }

            if !used.contains(&next) {
                tags[filled] = next;
                filled += 1;
            }

            next = next.wrapping_add(1);
        }

        if n > 0 {
            self.last_tag_used = tags[n - 1];
        }

        tags
    }
```

**File:** programs/marginfi/tests/user_actions/order.rs (L1577-1598)
```rust
    borrower_mfi_account_f
        .try_set_keeper_close_flags(Some(vec![flagged_bank_f.key]))
        .await?;

    // Verify the flagged balance's tag is now zero
    let marginfi_account_after = borrower_mfi_account_f.load().await;
    let flagged_balance = marginfi_account_after
        .lending_account
        .balances
        .iter()
        .find(|b| b.is_active() && b.bank_pk == flagged_bank_f.key);

    assert!(
        flagged_balance.is_some(),
        "flagged balance should still exist"
    );
    assert_eq!(
        flagged_balance.unwrap().tag,
        0,
        "flagged balance tag should be zeroed after set_liquidator_close_flags"
    );
    assert_active_orders(&borrower_mfi_account_f, 1).await;
```

**File:** guides/USER/ORDERS.md (L80-84)
```markdown
(F2) The lending position can be withdrawn down to $0, but must remain open. If the Balance is closed
by the user (e.g. by withdraw_all), and the same asset is deposited later to re-open it, Orders
created prior to the Balance being closed **will not work**. This means users are able to modify
their accounts such that active Orders are orphaned and can no longer execute, it's up to users to make
sure they do not close out positions involved with their Orders without updating the Orders too.
```

**File:** programs/marginfi/src/instructions/marginfi_account/order.rs (L78-101)
```rust
    // Reserve tags for the balances if necessary
    let balance_1_needs_tag = lending_account.balances[balance_index_1].tag == 0;
    let balance_2_needs_tag = lending_account.balances[balance_index_2].tag == 0;

    let empty_tag_count = balance_1_needs_tag as usize + balance_2_needs_tag as usize;

    if empty_tag_count > 0 {
        let new_tags = lending_account.reserve_n_tags(empty_tag_count);
        let mut tag_index = 0;

        if balance_1_needs_tag {
            lending_account.balances[balance_index_1].tag = new_tags[tag_index];
            tag_index += 1;
        }

        if balance_2_needs_tag {
            lending_account.balances[balance_index_2].tag = new_tags[tag_index];
        }
    }

    let tags = [
        lending_account.balances[balance_index_1].tag,
        lending_account.balances[balance_index_2].tag,
    ];
```

**File:** programs/marginfi/src/instructions/marginfi_account/order.rs (L287-297)
```rust
    let (order_assets_in_equity, order_liabs_in_equity, order_asset_count, order_liab_count) =
        get_tagged_account_health_components(
            &marginfi_account,
            ctx.remaining_accounts,
            &order.tags,
        )?;

    check!(
        order_asset_count + order_liab_count == ORDER_ACTIVE_TAGS,
        MarginfiError::LendingAccountBalanceNotFound
    );
```

**File:** programs/marginfi/src/instructions/marginfi_account/order.rs (L685-689)
```rust
    /// This account will have the authority to withdraw/repay as if they are the user authority
    /// until the end of the tx.
    ///
    /// CHECK: no checks whatsoever, executor decides this without restriction
    pub executor: UncheckedAccount<'info>,
```

**File:** programs/marginfi/src/state/order.rs (L140-143)
```rust
            // Skip balances that belong to this order, they can be changed by the keeper
            if balance.tag != 0 && order_tags.contains(&balance.tag) {
                continue;
            }
```

**File:** programs/marginfi/tests/user_actions/limit_orders_multi.rs (L437-456)
```rust
    // Reopen SOL with a new deposit (new tag, old order tag is now orphaned).
    test_f.refresh_blockhash().await;
    let sol_deposit = sol_bank.mint.create_token_account_and_mint_to(1.0).await;
    borrower
        .try_bank_deposit(sol_deposit.key, sol_bank, 1.0, None)
        .await?;

    let order_ad_after = borrower.load_order(order_ad).await;
    let mfi_after = borrower.load().await;
    assert_eq!(mfi_after.active_orders, 1);
    let sol_balance = mfi_after
        .lending_account
        .balances
        .iter()
        .find(|b| b.bank_pk == sol_bank.key)
        .unwrap();
    assert_ne!(
        sol_balance.tag, order_ad_after.tags[0],
        "reopened SOL balance should not reuse the old order tag"
    );
```
