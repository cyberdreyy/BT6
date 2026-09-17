### Title
Equality-based `ValidityPredicate::Balance`/`Storage` checks can be permanently griefed by an unprivileged frontrunner, DOSing pending validity transactions - (File: `crates/execution/txpool/src/validity.rs`)

### Summary
Base's experimental validity-transaction extension lets a submitter attach `ValidityPredicate`s (e.g. `Balance`, `Storage`) with an `Equal` comparison operator that must hold at the flashblock the transaction is included in. Because `ValidityOperator::Equal` performs strict `==` comparison against live, externally-influenceable state, any unprivileged actor can send a trivial transaction that nudges the watched balance/storage value away from the exact target, permanently blocking the victim's pending validity transaction from ever being included — mirroring the reported `SwapType.CloseExactOut` `==` balance check DOS, but reachable purely through normal transaction submission against Base's txpool/payload builder.

### Finding Description
`ValidityOperator::matches` implements strict equality for the `Equal` variant: [1](#0-0) 

`ValidityPredicate::Balance` and `ValidityPredicate::Storage` compare a live account balance or masked storage slot against a fixed target `value` using this operator, and are explicitly user-suppliable via the JSON-tagged predicate payload: [2](#0-1) 

During block building, `ValidityPredicateEvaluation::evaluate` / `ValidityPredicateKey::first_unsatisfied` re-check these predicates against current EVM state each time the watched location changes, and unsatisfied transactions are "parked" rather than dropped, waiting to be woken by a future state change on the exact same key: [3](#0-2) [4](#0-3) 

Because the balance/storage location watched is a plain `Address` (or `Address`+`slot`) chosen by the submitter with no restriction that it be under the submitter's exclusive control, any other unprivileged party who can affect that same balance or storage slot (e.g. by sending a 1-wei transfer to the watched address, or interacting with the watched contract slot) can change the value away from the required exact match. Since the predicate demands exact equality, once perturbed it will not spontaneously become true again (an attacker can also repeat the perturbation on every wakeup), so the transaction stays parked until its block-expiry window elapses. Test harnesses in this exact predicate module confirm this is a functioning production code path bounded only by an expiry window, not a hard cap that would neutralize repeated griefing during the window: [5](#0-4) 

### Impact Explanation
An unprivileged, anonymous RPC/transaction sender can grief any other user's validity-predicate transaction that relies on `Equal` against a balance or storage location the attacker can also influence, by front-running/interleaving a minimal state-changing transaction that shifts the observed value off the exact target. The victim's transaction remains stuck (parked) in the builder/pool rather than being included, denying service to that specific transaction for the duration of its permitted lifetime window (bounded by `DEFAULT_MAX_VALIDITY_EXPIRY_SECS`/`block_number` predicate requirements), analogous to the Medium-severity `SwapType.CloseExactOut` DOS in the source report — no direct fund loss, but a reliable transaction-inclusion denial-of-service against a specific, otherwise legitimate builder feature path.

### Likelihood Explanation
Likelihood is moderate: the feature is opt-in and must be explicitly enabled by the builder operator, as validated by `test_validity_transactions_require_explicit_opt_in`: [6](#0-5) 

But once enabled, exploitation requires no special privilege — merely submitting an ordinary transaction that changes the watched balance/storage — making it trivially reachable by any anonymous sender who observes a pending `Equal`-predicate transaction (e.g. via mempool visibility or the txpool RPC) targeting a location they can also touch.

### Recommendation
Disallow or discourage `ValidityOperator::Equal` for `Balance`/`Storage` predicates in favor of range comparisons (`>=`/`<=`), or require that `Balance`/`Storage` predicates only target the submitting sender's own address under an atomic/self-contained value the sender exclusively controls between submission and inclusion. At minimum, document the exact-match griefing risk and consider rejecting `Equal`-balance predicates targeting third-party addresses at ingress validation (alongside the existing `ValidityPredicateError` ingress checks).

### Proof of Concept
1. Builder operator enables validity-predicate transactions (`enabled = true`, as in `test_validity_transactions_require_explicit_opt_in`).
2. Victim submits a `base_insertValidatedTransaction` request carrying a `ValidityPredicate::Balance { address: victim, op: Equal, value: X }`, where `X` is the victim's current balance, expecting inclusion once the flashblock builder observes that exact balance.
3. Attacker, an unprivileged party, sends a trivial 1-wei (or any nonzero) transfer to `victim` (or otherwise perturbs the watched storage slot) before the builder processes the victim's transaction.
4. `ValidityPredicateKey::first_unsatisfied` now finds the `Balance(victim)` predicate false (balance is `X+1 != X`), so the transaction is parked via `ParkedPredicateIndex::park` under `ValidityPredicateKey::Balance(victim)`, per the logic in `crates/execution/payload/src/validity.rs`.
5. Attacker repeats step 3 whenever the balance is woken/re-evaluated (`affected_by_state`), keeping the exact-equality predicate perpetually false until the transaction's block-number expiry is reached, at which point it is dropped from the pool — denying inclusion of a legitimate transaction with no direct exploit cost to the attacker beyond dust transfers/gas.

### Citations

**File:** crates/execution/txpool/src/validity.rs (L12-26)
```rust
/// Default maximum number of experimental validity predicates carried by one transaction.
pub const DEFAULT_MAX_VALIDITY_PREDICATES: usize = 64;

/// Default maximum lifetime, in seconds, for experimental validity transactions.
pub const DEFAULT_MAX_VALIDITY_EXPIRY_SECS: u64 = 60;

/// The first flashblock index at which pooled transactions are evaluated.
///
/// The fallback block published at flashblock index `0` executes only sequencer
/// (attribute-derived) transactions; pooled transactions are first considered in
/// the flashblock at index `1`. A [`ValidityPredicate::FlashblockIndex`] whose
/// greatest satisfiable index is below this can therefore never hold for a pooled
/// transaction.
pub const FIRST_POOL_FLASHBLOCK_INDEX: u64 = 1;

```

**File:** crates/execution/txpool/src/validity.rs (L128-140)
```rust
impl ValidityOperator {
    /// Returns whether `left op right` holds.
    #[must_use]
    pub fn matches(self, left: U256, right: U256) -> bool {
        match self {
            Self::LessThan => left < right,
            Self::LessThanOrEqual => left <= right,
            Self::Equal => left == right,
            Self::NotEqual => left != right,
            Self::GreaterThan => left > right,
            Self::GreaterThanOrEqual => left >= right,
        }
    }
```

**File:** crates/execution/txpool/src/validity.rs (L165-197)
```rust
#[derive(Debug, Clone, PartialEq, Eq, serde::Deserialize, serde::Serialize)]
#[serde(tag = "type", content = "params", rename_all = "snake_case", deny_unknown_fields)]
pub enum ValidityPredicate {
    /// Compares an account balance with a value.
    Balance {
        /// Account whose balance is read.
        address: Address,
        /// Comparison to apply to the account balance.
        op: ValidityOperator,
        /// Right-hand comparison value.
        value: U256,
    },
    /// Compares a masked storage value with a value.
    Storage {
        /// Contract whose storage is read.
        address: Address,
        /// Storage slot to read.
        slot: U256,
        /// Bit mask applied to the loaded storage value.
        #[serde(default = "ValidityPredicate::default_mask")]
        mask: U256,
        /// Comparison to apply to the masked storage value.
        op: ValidityOperator,
        /// Right-hand comparison value.
        value: U256,
    },
    /// Compares the number of the block being built with a value.
    BlockNumber {
        /// Comparison to apply to the block number.
        op: ValidityOperator,
        /// Right-hand comparison value.
        value: U256,
    },
```

**File:** crates/execution/payload/src/validity.rs (L39-56)
```rust
    /// Returns the first predicate that does not hold against `db` and `context`.
    ///
    /// `Ok(None)` means every predicate matches. `Err` means a predicate's state could not be
    /// read; callers must treat that as an inability to verify rather than a successful match.
    pub fn first_unsatisfied<DB: Database>(
        predicates: &[ValidityPredicate],
        db: &mut DB,
        context: &PredicateContext,
    ) -> Result<Option<Self>, DB::Error> {
        for predicate in predicates {
            match predicate.matches(db, context) {
                Ok(true) => {}
                Ok(false) => return Ok(Some(Self::for_predicate(predicate))),
                Err(error) => return Err(error),
            }
        }
        Ok(None)
    }
```

**File:** crates/execution/payload/src/validity.rs (L164-198)
```rust
    /// Returns parked transactions and index-bucket wakeups triggered by `state`.
    pub fn affected_by_state(&self, state: &EvmState) -> StateChangeEffects {
        let mut effects = StateChangeEffects::default();
        for (address, account) in state {
            if account.info.balance != account.original_info().balance
                && let Some(hashes) = self.blockers.get(&ValidityPredicateKey::Balance(*address))
            {
                effects.affected_transactions.extend(hashes.iter().copied());
                effects.woken_buckets += 1;
            }

            // Selfdestruct can clear slots that were not loaded during this execution. Wake every
            // storage blocker for the account so those predicates are re-read from committed state.
            if account.is_selfdestructed() {
                for (key, hashes) in &self.blockers {
                    if matches!(key, ValidityPredicateKey::Storage(key_address, _) if key_address == address)
                    {
                        effects.affected_transactions.extend(hashes.iter().copied());
                        effects.woken_buckets += 1;
                    }
                }
            } else {
                for (slot, value) in &account.storage {
                    if value.is_changed()
                        && let Some(hashes) =
                            self.blockers.get(&ValidityPredicateKey::Storage(*address, *slot))
                    {
                        effects.affected_transactions.extend(hashes.iter().copied());
                        effects.woken_buckets += 1;
                    }
                }
            }
        }
        effects
    }
```

**File:** crates/builder/core/tests/rpc.rs (L174-205)
```rust
/// Verifies validity-bearing requests require explicit builder opt-in.
#[tokio::test]
async fn test_validity_transactions_require_explicit_opt_in() -> eyre::Result<()> {
    let validity = TransactionValidity {
        validity: vec![ValidityPredicate::Balance {
            address: Account::Alice.address(),
            op: ValidityOperator::Equal,
            value: U256::ZERO,
        }],
    };

    let (disabled_harness, disabled_client) = setup(false, DEFAULT_MAX_VALIDITY_PREDICATES).await?;
    let (sender, raw) = create_eip1559_tx(disabled_harness.chain_id());
    let disabled_tx = ValidatedTransaction { sender, raw, extensions: validity.clone() };
    let disabled: Result<(), _> =
        disabled_client.request("base_insertValidatedTransaction", (disabled_tx,)).await;
    assert!(
        disabled
            .expect_err("disabled builder should reject validity")
            .to_string()
            .contains("transaction extensions are disabled")
    );

    let (enabled_harness, enabled_client) = setup(true, DEFAULT_MAX_VALIDITY_PREDICATES).await?;
    let (sender, raw) = create_eip1559_tx(enabled_harness.chain_id());
    let enabled_tx = ValidatedTransaction { sender, raw, extensions: validity };
    let enabled: Result<(), _> =
        enabled_client.request("base_insertValidatedTransaction", (enabled_tx,)).await;
    assert!(enabled.is_ok(), "enabled builder should accept validity: {enabled:?}");

    Ok(())
}
```
