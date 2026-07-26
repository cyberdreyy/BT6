### Title
Excess SUI Payment Not Returned to Buyer in `purchase_with_cap` — (File: `crates/sui-framework/packages/sui-framework/sources/kiosk/kiosk.move`)

---

### Summary

`purchase_with_cap` in the Sui Kiosk framework accepts a `Coin<SUI>` payment that must be `>= min_price`, but deposits the **entire** coin into the kiosk's profits balance without returning any excess to the buyer. Any SUI above `min_price` is permanently transferred to the kiosk owner.

---

### Finding Description

`purchase_with_cap` enforces only a lower-bound check on the payment amount and then unconditionally deposits the whole coin:

```move
let paid = payment.value();
assert!(paid >= min_price, EIncorrectAmount);   // lower-bound only
...
coin::put(&mut self.profits, payment);           // entire coin deposited, no refund
``` [1](#0-0) 

The sibling `purchase` function enforces strict equality instead:

```move
assert!(price == payment.value(), EIncorrectAmount);
``` [2](#0-1) 

This asymmetry means any buyer who passes a coin larger than `min_price` to `purchase_with_cap` silently loses the difference to the kiosk owner. The `PurchaseCap` struct records only a `min_price` floor, not a fixed price, so the framework explicitly permits overpayment while providing no mechanism to recover the excess. [3](#0-2) 

The test suite in `kiosk_extension_tests.move` reveals that callers must manually guard against this: the marketplace example adds its own equality assertion before calling `purchase_with_cap`, confirming the framework itself does not protect the buyer:

```move
assert!(payment.value() == kiosk::purchase_cap_min_price(&purchase_cap), EIncorrectAmount);
``` [4](#0-3) 

---

### Impact Explanation

A buyer who calls `purchase_with_cap` with a coin whose value exceeds `min_price` permanently loses the excess SUI to the kiosk owner's `profits` balance. Once `coin::put` executes, the funds are inside the kiosk's `Balance<SUI>` and only the kiosk owner can withdraw them via `withdraw`. The loss is irreversible within the same transaction and there is no on-chain recourse for the buyer.

This matches the **Medium** impact gate: harmful smart-contract behavior causing unintended fund loss for an ordinary SUI holder triggered by public input.

---

### Likelihood Explanation

The trigger requires only:
1. A `PurchaseCap` in the buyer's possession (standard output of any marketplace or kiosk extension that uses `list_with_purchase_cap`).
2. A call to `purchase_with_cap` with a coin that has not been pre-split to exactly `min_price`.

This is a realistic scenario in PTB-based marketplace flows where a buyer splits a larger coin and passes the unsplit remainder, or where a frontend miscalculates the exact split amount. The attacker model is an ordinary SUI holder; no privileged role is required. [5](#0-4) 

---

### Recommendation

After depositing exactly `min_price` into profits, return the remainder to the caller:

```move
public fun purchase_with_cap<T: key + store>(
    self: &mut Kiosk,
    purchase_cap: PurchaseCap<T>,
    mut payment: Coin<SUI>,
    ctx: &mut TxContext,
): (T, TransferRequest<T>) {
    let PurchaseCap { id, item_id, kiosk_id, min_price } = purchase_cap;
    id.delete();
    let id = item_id;
    let paid = payment.value();
    assert!(paid >= min_price, EIncorrectAmount);
    assert!(object::id(self) == kiosk_id, EWrongKiosk);
    df::remove<Listing, u64>(&mut self.id, Listing { id, is_exclusive: true });

    // Deposit only min_price; return excess to buyer
    let exact = coin::split(&mut payment, min_price, ctx);
    coin::put(&mut self.profits, exact);
    if (payment.value() > 0) {
        transfer::public_transfer(payment, ctx.sender());
    } else {
        coin::destroy_zero(payment);
    };

    self.item_count = self.item_count - 1;
    df::remove_opt<Lock, bool>(&mut self.id, Lock { id });
    let item = dof::remove<Item, T>(&mut self.id, Item { id });
    (item, transfer_policy::new_request(id, min_price, object::id(self)))
}
```

Alternatively, enforce exact payment as `purchase` does: `assert!(paid == min_price, EIncorrectAmount)`.

---

### Proof of Concept

1. Kiosk owner calls `list_with_purchase_cap` with `min_price = 10_000_000` MIST (0.01 SUI) and transfers the resulting `PurchaseCap<T>` to a buyer.
2. Buyer holds a single `Coin<SUI>` worth 1 SUI (1_000_000_000 MIST) and calls:
   ```
   purchase_with_cap(kiosk, purchase_cap, coin_of_1_SUI)
   ```
3. `assert!(paid >= min_price)` passes (1_000_000_000 ≥ 10_000_000). [6](#0-5) 
4. `coin::put(&mut self.profits, payment)` deposits the full 1 SUI into the kiosk's profits. [7](#0-6) 
5. Buyer receives the item and a `TransferRequest` but has lost 0.99 SUI (990_000_000 MIST) to the kiosk owner.
6. Kiosk owner calls `withdraw(kiosk, cap, option::none(), ctx)` and collects the full 1 SUI. [8](#0-7)

### Citations

**File:** crates/sui-framework/packages/sui-framework/sources/kiosk/kiosk.move (L166-174)
```text
public struct PurchaseCap<phantom T: key + store> has key, store {
    id: UID,
    /// ID of the `Kiosk` the cap belongs to.
    kiosk_id: ID,
    /// ID of the listed item.
    item_id: ID,
    /// Minimum price for which the item can be purchased.
    min_price: u64,
}
```

**File:** crates/sui-framework/packages/sui-framework/sources/kiosk/kiosk.move (L381-381)
```text
    assert!(price == payment.value(), EIncorrectAmount);
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

**File:** crates/sui-framework/packages/sui-framework/sources/kiosk/kiosk.move (L451-467)
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
```

**File:** crates/sui-framework/packages/sui-framework/tests/kiosk/kiosk_extension_tests.move (L99-101)
```text
        assert!(payment.value() == kiosk::purchase_cap_min_price(&purchase_cap), EIncorrectAmount);
        let market_request = policy::new_request(item_id, payment.value(), object::id(kiosk));
        let (item, request) = kiosk.purchase_with_cap(purchase_cap, payment);
```
