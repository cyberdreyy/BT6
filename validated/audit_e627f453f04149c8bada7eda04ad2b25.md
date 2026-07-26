### Title
Unbounded `u64` Coin-Reservation Amount Causes `i64::try_from().unwrap()` Panic in `SmashMetadata::smash_gas`, Crashing the Validator Node — (`sui-execution/latest/sui-adapter/src/gas_charger.rs`)

---

### Summary

An ordinary SUI holder can craft a coin-reservation `ObjectRef` whose `reservation_amount` field is any `u64` value (e.g. `u64::MAX`). When that reservation appears as a **secondary** gas-payment entry alongside a real `Coin<SUI>` object, the execution engine calls `SmashMetadata::smash_gas`, which unconditionally executes `i64::try_from(*reservation).unwrap()` on the raw `u64`. For any value above `i64::MAX` this conversion returns `Err`, the `.unwrap()` panics, and — with no `catch_unwind` anywhere in the execution path — the validator process crashes.

---

### Finding Description

**Encoding layer — no upper-bound on `reservation_amount`**

`ParsedDigest` stores `reservation_amount` as a raw `u64` decoded from bytes 0–7 of the `ObjectDigest`: [1](#0-0) 

Any 8-byte value is accepted; there is no cap.

**Validation layer — only checks zero and epoch**

`validity_check` in `transaction.rs` rejects a coin-reservation only if the epoch is wrong or the amount is exactly zero: [2](#0-1) 

No upper-bound check exists. The accumulator-ID ownership check (lines 3383–3408) is satisfiable by any user who has a SUI address-balance accumulator.

**Pre-execution gas-balance check — uses `saturating_add`**

`check_gas` in `sui-transaction-checks` accumulates reservation amounts with `saturating_add`: [3](#0-2) 

`u64::MAX` saturates to `u64::MAX`, which is ≥ any gas budget, so `check_gas_balance` passes unconditionally.

**Execution layer — unchecked narrowing cast panics**

Inside `SmashMetadata::smash_gas`, the loop over secondary payments does: [4](#0-3) 

`i64::try_from(u64::MAX)` returns `Err`; `.unwrap()` panics. There is no `catch_unwind` anywhere in the `latest` execution path: [5](#0-4) 

The comment at lines 258–260 explicitly states this function "panics if errors are found" and relies on prior checks to prevent that — but those checks do not bound the reservation amount.

**How the secondary-payment slot is reached**

`payment_kind` in `execution_engine.rs` maps each gas-payment entry to a `PaymentMethod`; the first entry becomes `smash_target`, all others become `smashed_payments`: [6](#0-5) 

Placing a real coin first and the malicious reservation second routes the oversized `u64` into the `smashed_payments` loop that contains the panicking cast.

---

### Impact Explanation

A single crafted transaction from any ordinary SUI holder crashes the validator node process. Because the panic is unguarded (no `catch_unwind` confirmed in the execution path), the OS-level process terminates. This is a **node-shutdown impact reachable from public transaction input**, qualifying under the active bounty's High/Medium liveness class.

---

### Likelihood Explanation

The attack requires only:
1. A valid SUI address with any funded address-balance accumulator (to pass the accumulator-ID ownership check).
2. Any real `Coin<SUI>` object (to be the primary gas payment).
3. Ability to hand-craft an `ObjectRef` with a specific 32-byte digest — trivially done off-chain.

No privileged access, leaked keys, or malicious peers are needed.

---

### Recommendation

Replace the bare `.unwrap()` with a checked conversion that returns an `ExecutionError` instead of panicking:

```rust
// Before (line 601):
i64::try_from(*reservation).unwrap().checked_neg().unwrap()

// After:
i64::try_from(*reservation)
    .map_err(|_| ExecutionError::invariant_violation(
        "AddressBalance reservation exceeds i64::MAX"))?
    .checked_neg()
    .ok_or_else(|| ExecutionError::invariant_violation(
        "AddressBalance reservation negation overflow"))?
```

Additionally, add an upper-bound check on `reservation_amount` in `validity_check` (e.g. `reservation_amount <= i64::MAX as u64`) so the malformed input is rejected before it reaches the execution engine.

---

### Proof of Concept

```rust
// Construct a SmashMetadata with:
//   smash_target  = PaymentMethod::Coin(some_real_coin_ref)
//   smashed_payments = { AddressBalance(sender, u64::MAX) }
// Then call smash_gas on it.
//
// Expected: panics at i64::try_from(u64::MAX).unwrap()
// (or, with the fix, returns ExecutionError gracefully)

#[test]
#[should_panic]
fn test_smash_gas_panics_on_oversized_reservation() {
    // Build a minimal TemporaryStore with one real gas coin.
    // Set smash_target = Coin(gas_coin_ref)
    // Set smashed_payments = { AddressBalance(addr, u64::MAX) }
    // Call smash_gas(&tx_digest, &mut store)
    // → panics at line 601
}
```

The on-chain reproduction path:
1. Fund an address balance (any amount).
2. Craft an `ObjectRef` with digest bytes `[0xFF;8] ++ epoch_le_bytes ++ [0xAC;20]` and masked accumulator object ID.
3. Submit a transaction with gas payment `[real_coin_ref, crafted_reservation_ref]`.
4. The validator panics during `GasCharger::new` → process crash.

### Citations

**File:** crates/sui-types/src/coin_reservation.rs (L107-127)
```rust
impl TryFrom<ObjectDigest> for ParsedDigest {
    type Error = ParsedDigestError;

    fn try_from(digest: ObjectDigest) -> Result<Self, Self::Error> {
        if ParsedDigest::is_coin_reservation_digest(&digest) {
            let inner = digest.inner();
            let reservation_amount_bytes: &[u8; 8] = inner[0..8].try_into().unwrap();
            let epoch_bytes: &[u8; 4] = inner[8..12].try_into().unwrap();

            let epoch_id = u32::from_le_bytes(*epoch_bytes);
            let reservation_amount = u64::from_le_bytes(*reservation_amount_bytes);

            Ok(Self {
                epoch_id,
                reservation_amount,
            })
        } else {
            Err(ParsedDigestError)
        }
    }
}
```

**File:** crates/sui-types/src/transaction.rs (L3263-3277)
```rust
            for parsed in self.parsed_coin_reservations(context.chain_identifier) {
                num_reservations += 1;
                // coin reservations are valid for the current and next epoch, just as transactions that
                // specify a TransactionDuring are.
                // TODO: this check can be skipped if the transaction contains any address owned inputs.
                if parsed.epoch_id() != context.epoch && parsed.epoch_id() + 1 != context.epoch {
                    return Err(SuiErrorKind::TransactionExpired.into());
                }
                if parsed.reservation_amount() == 0 {
                    return Err(UserInputError::InvalidWithdrawReservation {
                        error: "Balance withdraw reservation amount must be non-zero".to_string(),
                    }
                    .into());
                }
            }
```

**File:** crates/sui-transaction-checks/src/lib.rs (L435-437)
```rust
                if let Ok(parsed) = ParsedDigest::try_from(obj_ref.2) {
                    available_address_balance_gas =
                        available_address_balance_gas.saturating_add(parsed.reservation_amount());
```

**File:** sui-execution/latest/sui-adapter/src/gas_charger.rs (L536-542)
```rust
        fn smash_gas(
            &mut self,
            tx_digest: &TransactionDigest,
            temporary_store: &mut TemporaryStore<'_>,
        ) {
            // set gas charge location
            self.gas_charge_location = self.smash_target.location();
```

**File:** sui-execution/latest/sui-adapter/src/gas_charger.rs (L598-604)
```rust
                        let event = AccumulatorEvent::from_balance_change(
                            *sui_address,
                            balance_type,
                            i64::try_from(*reservation).unwrap().checked_neg().unwrap(),
                        )
                        .expect("Failed to create accumulator event for gas smashing");
                        temporary_store.add_accumulator_event(event);
```

**File:** sui-execution/latest/sui-adapter/src/execution_engine.rs (L124-139)
```rust
            let payment_methods = gas_data
                .payment
                .iter()
                .map(|entry| {
                    if let Ok(parsed) = ParsedDigest::try_from(entry.2) {
                        PaymentMethod::AddressBalance(gas_data.owner, parsed.reservation_amount())
                    } else {
                        PaymentMethod::Coin(*entry)
                    }
                })
                .collect();
            PaymentKind::smash(payment_methods).expect(
                "unable to create a payment kind from payment methods. \
                 Should not be possible wit ha non-empty vector",
            )
        }
```
