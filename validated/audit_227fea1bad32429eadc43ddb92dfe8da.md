Now I have all the code needed to analyze this claim. Let me trace the exact path.

**Key facts established:**

1. `LegacyDisplay<T>` (`sui::display::Display<T>`) has `key, store`: [1](#0-0) 

2. Because it has `store`, it is freely transferable via `transfer::public_transfer`.

3. `migrate_v1_to_v2` takes a `LegacyDisplay<T>` by value and calls `new_display<T>` with **no publisher ownership check**: [2](#0-1) 

4. `new_display` claims the canonical derived-object slot for `DisplayKey<T>` and mints a fresh `DisplayCap<T>` — to whoever called it: [3](#0-2) 

5. By contrast, `new_with_publisher` explicitly asserts `publisher.from_package<T>()`: [4](#0-3) 

---

**Attack path analysis:**

- Step 1: Publisher calls `display::new<T>(&publisher, ctx)` — requires `Publisher`, produces a `LegacyDisplay<T>`.
- Step 2: Publisher calls `transfer::public_transfer(legacy_display, attacker_address)` — valid because `LegacyDisplay<T>` has `store`.
- Step 3: Attacker calls `migrate_v1_to_v2(registry, attacker_legacy, ctx)`.
- Step 4: `new_display<T>` runs with no publisher check, claims `DisplayKey<T>` in the registry, mints `DisplayCap<T>` to the attacker.
- Step 5: Attacker calls `display.share()` and holds the only `DisplayCap<T>` for type T permanently.

The legitimate publisher can never reclaim the slot — `new_display` aborts with `EDisplayAlreadyExists` on any subsequent attempt. [5](#0-4) 

---

**Guard analysis — does anything block this?**

`migrate_v1_to_v2` has exactly one guard: `new_display` checks that no `Display<T>` slot exists yet. There is no check that the `LegacyDisplay<T>` was created by the publisher of T, no check on `ctx.sender()`, and no `Publisher` argument. The `store` ability on `LegacyDisplay<T>` means possession is fully decoupled from authorship.

---

**Impact:**

- The canonical `Display<T>` derived-object slot is permanently registered under attacker control.
- The attacker's `DisplayCap<T>` is the sole authority to call `set`, `unset`, and `clear` on the shared `Display<T>`.
- The legitimate publisher is permanently locked out with no revocation path.
- Display metadata (name, image_url, link, description) for all objects of type T can be set to attacker-controlled values — enabling phishing, NFT metadata spoofing, and misleading wallet/explorer rendering.

This is state corruption of the canonical registry slot for T, matching the "unauthorized object creation / state corruption" Critical impact class.

---

### Title
Missing Publisher Authorization in `migrate_v1_to_v2` Allows Any `LegacyDisplay<T>` Holder to Permanently Seize the Canonical V2 Display Slot — (`crates/sui-framework/packages/sui-framework/sources/registries/display_registry.move`)

### Summary
`migrate_v1_to_v2` accepts any `LegacyDisplay<T>` by value and unconditionally claims the canonical `DisplayKey<T>` derived-object slot in `DisplayRegistry`, minting a fresh `DisplayCap<T>` to the caller. Because `LegacyDisplay<T>` carries `store` and is freely transferable, any address that holds one — regardless of whether they are the type's publisher — can pre-empt the legitimate publisher and permanently own the canonical display and its capability.

### Finding Description
`migrate_v1_to_v2` delegates to `new_display<T>`, which:
1. Asserts the slot does not yet exist (`EDisplayAlreadyExists`).
2. Claims `derived_object::claim(&mut registry.id, DisplayKey<T>())`.
3. Mints `DisplayCap<T>` and sets `cap_id` to the new cap's ID.

No step verifies that the caller or the supplied `LegacyDisplay<T>` originates from the package that defines T. The parallel entry point `new_with_publisher` enforces `publisher.from_package<T>()`, but `migrate_v1_to_v2` has no equivalent guard. Since `sui::display::Display<T>` has `key, store`, it can be transferred to any address with `transfer::public_transfer`, fully decoupling possession from authorship.

### Impact Explanation
An attacker who receives (or otherwise acquires) any `LegacyDisplay<T>` for a type they do not own can:
- Register the canonical V2 `Display<T>` for that type before the legitimate publisher.
- Retain the sole `DisplayCap<T>`, permanently controlling `set`, `unset`, and `clear` on the shared display object.
- Set arbitrary display fields (name, image_url, link, description) for all objects of type T, enabling NFT metadata spoofing and phishing.
- Permanently lock the legitimate publisher out — `EDisplayAlreadyExists` prevents any subsequent registration.

### Likelihood Explanation
The prerequisite is holding a `LegacyDisplay<T>`. This requires the publisher to have created one (gated by `Publisher`) and transferred it. In V1, publishers routinely created and transferred display objects to display-management contracts or co-owners. Any such transfer creates the attack surface. The window is open until either the publisher or the system migration script claims the slot first.

### Recommendation
Add a `Publisher` argument to `migrate_v1_to_v2` and assert `publisher.from_package<T>()`, mirroring `new_with_publisher`:

```move
public fun migrate_v1_to_v2<T: key>(
    registry: &mut DisplayRegistry,
    publisher: &Publisher,          // add this
    legacy: LegacyDisplay<T>,
    ctx: &mut TxContext,
): (Display<T>, DisplayCap<T>) {
    assert!(publisher.from_package<T>(), ENotValidPublisher);  // add this
    let (mut display, cap) = new_display<T>(registry, ctx);
    display.fields = *legacy.fields();
    legacy.destroy();
    (display, cap)
}
```

### Proof of Concept
```move
// 1. Publisher package: defines MyNFT, creates and transfers a LegacyDisplay to attacker
module publisher::nft {
    use sui::display;
    use sui::package::Publisher;

    public struct MyNFT has key { id: UID }

    public fun give_display_to_attacker(
        pub: &Publisher,
        attacker: address,
        ctx: &mut TxContext,
    ) {
        let legacy = display::new<MyNFT>(pub, ctx);
        transfer::public_transfer(legacy, attacker);
    }
}

// 2. Attacker transaction (PTB):
//    - Input: DisplayRegistry (shared), LegacyDisplay<MyNFT> (owned by attacker)
//    - Call: display_registry::migrate_v1_to_v2<MyNFT>(registry, legacy_display, ctx)
//    - Returns: (Display<MyNFT>, DisplayCap<MyNFT>)
//    - Call: display_registry::share(display)
//    - Transfer DisplayCap<MyNFT> to attacker address
//
// 3. Assert: attacker's DisplayCap<MyNFT> successfully calls
//    display_registry::set(&mut display, &cap, b"image_url", b"https://attacker.com/phish")
//    => succeeds, legitimate publisher is permanently locked out.
```

### Citations

**File:** crates/sui-framework/packages/sui-framework/sources/display.move (L48-48)
```text
public struct Display<phantom T: key> has key, store {
```

**File:** crates/sui-framework/packages/sui-framework/sources/registries/display_registry.move (L64-72)
```text
public fun new_with_publisher<T>(
    registry: &mut DisplayRegistry,
    publisher: &mut Publisher,
    ctx: &mut TxContext,
): (Display<T>, DisplayCap<T>) {
    assert!(publisher.from_package<T>(), ENotValidPublisher);
    let (display, cap) = new_display<T>(registry, ctx);
    (display, cap)
}
```

**File:** crates/sui-framework/packages/sui-framework/sources/registries/display_registry.move (L149-159)
```text
public fun migrate_v1_to_v2<T: key>(
    registry: &mut DisplayRegistry,
    legacy: LegacyDisplay<T>,
    ctx: &mut TxContext,
): (Display<T>, DisplayCap<T>) {
    let (mut display, cap) = new_display<T>(registry, ctx);
    display.fields = *legacy.fields();
    legacy.destroy();

    (display, cap)
}
```

**File:** crates/sui-framework/packages/sui-framework/sources/registries/display_registry.move (L191-204)
```text
fun new_display<T>(
    registry: &mut DisplayRegistry,
    ctx: &mut TxContext,
): (Display<T>, DisplayCap<T>) {
    let key = DisplayKey<T>();
    assert!(!derived_object::exists(&registry.id, key), EDisplayAlreadyExists);
    let cap = DisplayCap<T> { id: object::new(ctx) };
    let display = Display<T> {
        id: derived_object::claim(&mut registry.id, key),
        fields: vec_map::empty(),
        cap_id: option::some(cap.id.to_inner()),
    };
    (display, cap)
}
```
