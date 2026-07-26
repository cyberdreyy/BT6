### Title
Excess SUI Payment Not Returned to Buyer in `purchase_with_cap()` — (`File: crates/sui-framework/packages/sui-framework/sources/kiosk/kiosk.move`)

---

### Summary

`purchase_with_cap` in the Sui Kiosk framework accepts a `Coin<SUI>` payment that must be **at least** `min_price`, but deposits the **entire coin** into the kiosk's profits without refunding the excess. Any SUI above `min_price` is permanently transferred to the kiosk owner's profit balance, causing a direct, irreversible fund loss for the buyer.

---

### Finding Description

The Sui Kiosk module exposes two purchase paths:

**`purchase` (standard listing)** — enforces an exact-match check:

```move
assert!(price == payment.value(), EIncorrectAmount);
coin::put(&mut self.profits, payment);
``` [1](#0-0) 

**`purchase_with_cap` (exclusive listing)** — enforces only a minimum:

```move
let paid = payment.value();
assert!(paid >= min_price, EIncorrectAmount);   // ← allows overpayment
...
coin::put(&mut self.profits, payment);           // ← entire coin deposited
``` [2](#0-1) 

The `PurchaseCap` struct records only `min_price` — the minimum acceptable price — not a fixed price:

```move
/// A capability which locks an item and gives a permission to
/// purchase it from a `Kiosk` for any price no less than `min_price`.
``` [3](#0-2) 

When a buyer passes a `Coin<SUI>` whose value exceeds `min_price`, the function:
1. Reads `paid = payment.value()` (the full coin value).
2. Asserts `paid >= min_price` — passes even with excess.
3. Calls `coin::put(&mut self.profits, payment)` — deposits the **whole** coin, not just `min_price`.
4. Returns `(item, TransferRequest)` — no change coin, no excess refund.

The excess `paid - min_price` MIST is silently credited to the kiosk owner's `profits` balance and is withdrawable by the owner at any time via `withdraw`. [4](#0-3) 

---

### Impact Explanation

**Classification: Medium — harmful smart-contract behavior / permanent fund loss for the buyer.**

Any SUI sent above `min_price` is irrecoverably transferred to the kiosk owner's profit balance. The buyer receives no change coin and has no mechanism to reclaim the overpayment after the transaction settles. Because `Coin<SUI>` is a first-class asset, this constitutes a direct, permanent fund loss for an ordinary SUI holder interacting with a public framework function.

The `profits` balance is exclusively withdrawable by the kiosk owner:

```move
public fun withdraw(self: &mut Kiosk, cap: &KioskOwnerCap, amount: Option<u64>, ctx: &mut TxContext): Coin<SUI> {
    assert!(self.has_access(cap), ENotOwner);
    ...
    coin::take(&mut self.profits, amount, ctx)
}
``` [5](#0-4) 

---

### Likelihood Explanation

`purchase_with_cap` is a public framework function callable by any address. The `PurchaseCap` is designed to be transferred to third-party applications (e.g., auction contracts, OTC desks) that compute and pass a payment coin. Any application that:

- Constructs a payment coin from a larger balance without splitting to the exact amount, or
- Passes the gas coin directly as payment when its value exceeds `min_price`, or
- Operates in a context where the exact price is not known at coin-construction time

will silently overpay. The kiosk module's own documentation notes that `PurchaseCap` is intended for use in "trusted applications," but the function itself imposes no on-chain guard against overpayment.

---

### Recommendation

Split the payment coin to exactly `min_price` before depositing, and return the remainder to the caller:

```move
public fun purchase_with_cap<T: key + store>(
    self: &mut Kiosk,
    purchase_cap: PurchaseCap<T>,
    payment: Coin<SUI>,
    ctx: &mut TxContext,
): (T, TransferRequest<T>, Coin<SUI>) {          // ← return excess coin
    let PurchaseCap { id, item_id, kiosk_id, min_price } = purchase_cap;
    id.delete();

    let id = item_id;
    let paid = payment.value();
    assert!(paid >= min_price, EIncorrectAmount);
    assert!(object::id(self) == kiosk_id, EWrongKiosk);

    df::remove<Listing, u64>(&mut self.id, Listing { id, is_exclusive: true });

    // Split exact price; return excess to caller
    let mut payment = payment;
    let exact = payment.split(min_price, ctx);
    coin::put(&mut self.profits, exact);

    self.item_count = self.item_count - 1;
    df::remove_opt<Lock, bool>(&mut self.id, Lock { id });
    let item = dof::remove<Item, T>(&mut self.id, Item { id });

    (item, transfer_policy::new_request(id, min_price, object::id(self)), payment)
}
```

Alternatively, enforce an exact-match check (`paid == min_price`) consistent with the `purchase` function, forcing callers to split before calling.

---

### Proof of Concept

```move
// Attacker: ordinary SUI holder with a Coin<SUI> of value 1_000 MIST
// Victim kiosk: item listed_exclusively with min_price = 100 MIST

// Step 1 – kiosk owner lists item exclusively
let cap = kiosk.list_with_purchase_cap<Item>(&owner_cap, item_id, 100, ctx);

// Step 2 – cap is transferred to buyer (e.g., via an OTC flow)
transfer::transfer(cap, buyer_address);

// Step 3 – buyer calls purchase_with_cap with a coin of value 1_000
// (e.g., they did not split first, or the coin was auto-selected)
let (item, req) = kiosk.purchase_with_cap<Item>(cap, coin_of_1000_mist);

// Result:
//   kiosk.profits += 1_000 MIST   (entire coin)
//   buyer receives item + TransferRequest
//   buyer loses 900 MIST (1_000 - 100) permanently to kiosk owner
//   no excess coin is returned
```

The `TransferRequest` records `paid = 1_000` (the full coin value), not `min_price = 100`:

```move
(item, transfer_policy::new_request(id, paid, object::id(self)))
//                                       ^^^^ = 1_000, not min_price
``` [6](#0-5) 

This means the `TransferRequest` also carries an inflated `paid` amount, which may affect royalty calculations in downstream `TransferPolicy` rules that use `paid` to compute fees — a secondary correctness issue compounding the primary fund-loss bug.

### Citations

**File:** crates/sui-framework/packages/sui-framework/sources/kiosk/kiosk.move (L157-160)
```text
/// A capability which locks an item and gives a permission to
/// purchase it from a `Kiosk` for any price no less than `min_price`.
///
/// Allows exclusive listing: only bearer of the `PurchaseCap` can
```

**File:** crates/sui-framework/packages/sui-framework/sources/kiosk/kiosk.move (L381-383)
```text
    assert!(price == payment.value(), EIncorrectAmount);
    df::remove_opt<Lock, bool>(&mut self.id, Lock { id });
    coin::put(&mut self.profits, payment);
```

**File:** crates/sui-framework/packages/sui-framework/sources/kiosk/kiosk.move (L417-438)
```text
public fun purchase_with_cap<T: key + store>(
    self: &mut Kiosk,
    purchase_cap: PurchaseCap<T>,
    payment: Coin<SUI>,
): (T, TransferRequest<T>) {
    let PurchaseCap { id, item_id, kiosk_id, min_price } = purchase_cap;
    id.delete();

    let id = item_id;
    let paid = payment.value();
    assert!(paid >= min_price, EIncorrectAmount);
    assert!(object::id(self) == kiosk_id, EWrongKiosk);

    df::remove<Listing, u64>(&mut self.id, Listing { id, is_exclusive: true });

    coin::put(&mut self.profits, payment);
    self.item_count = self.item_count - 1;
    df::remove_opt<Lock, bool>(&mut self.id, Lock { id });
    let item = dof::remove<Item, T>(&mut self.id, Item { id });

    (item, transfer_policy::new_request(id, paid, object::id(self)))
}
```

**File:** crates/sui-framework/packages/sui-framework/sources/kiosk/kiosk.move (L451-468)
```text
public fun withdraw(
    self: &mut Kiosk,
    cap: &KioskOwnerCap,
    amount: Option<u64>,
    ctx: &mut TxContext,
): Coin<SUI> {
    assert!(self.has_access(cap), ENotOwner);

    let amount = if (amount.is_some()) {
        let amt = amount.destroy_some();
        assert!(amt <= self.profits.value(), ENotEnough);
        amt
    } else {
        self.profits.value()
    };

    coin::take(&mut self.profits, amount, ctx)
}
```
