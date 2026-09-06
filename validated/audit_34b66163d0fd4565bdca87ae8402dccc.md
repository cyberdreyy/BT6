### Title
Non-deterministic `Ok`/`Reject` due to wall-clock-based `ProblematicTransaction` classification of resource-budget-exceeded errors - ([File: stackslib/src/net/api/postblock_proposal.rs])

### Summary
`NakamotoBlockProposal::validate` enforces per-tx execution/analysis time and memory budgets via `resource_budgets` (built from `max_tx_execution_time_secs`, `max_tx_analysis_time_secs`, `max_tx_mem_bytes`), and any tx that trips these budgets is classified via `TransactionResult::is_problematic` as `Error::ExecutionResourceBudgetExceeded`/`Error::AnalysisResourceBudgetExceeded`, which is unconditionally treated as `TransactionResult::Problematic` and surfaces as `ValidateRejectCode::ProblematicTransaction`. Because these budgets are wall-clock/real-memory bound rather than a deterministic cost metric, byte-identical block proposals can yield `Ok` on one signer's node and `Reject(ProblematicTransaction)` on another depending on machine speed/load at the moment of validation.

### Finding Description
In `NakamotoBlockProposal::validate` (`stackslib/src/net/api/postblock_proposal.rs:713-825`), each tx in `self.block.txs` is fed to `builder.try_mine_tx_with_len(...)` along with `resource_budgets`, constructed from wall-clock durations: [1](#0-0) 

This flows into `NakamotoBlockBuilder::try_mine_tx_with_len` in `stackslib/src/chainstate/nakamoto/miner.rs`, which calls `StacksChainState::process_transaction_with_check(... resource_budgets ...)` and, on failure, `parse_process_transaction_error`, which in turn calls `TransactionResult::is_problematic`: [2](#0-1) 

`TransactionResult::is_problematic` (`stackslib/src/chainstate/stacks/miner.rs:661-733`) explicitly classifies `Error::ExecutionResourceBudgetExceeded` and `Error::AnalysisResourceBudgetExceeded` as "problematic" and returns `(true, ...)`, marking the tx to be blacklisted: [3](#0-2) 

Back in `validate`, `TransactionResult::Problematic(p)` is mapped directly to `ValidateRejectCode::ProblematicTransaction`, which rejects the whole block with `failed_txid` set: [4](#0-3) 

The root cause: whether a tx is deterministically successful or is classified "problematic" depends on whether its wall-clock execution/analysis time (or heap usage) crosses `max_tx_execution_time_secs`/`max_tx_analysis_time_secs`/`max_tx_mem_bytes` — real-time thresholds that are inherently a function of the validating machine's CPU speed, concurrent load, and OS scheduling, not of the block's serialized bytes or a chainstate-derived deterministic cost metric (e.g. `ExecutionCost`). Two honest signers running `validate()` on the byte-identical proposal (same `signer_signature_hash`) against identical chainstate snapshots can get different outcomes: a faster/idle node completes the tx within budget → `TransactionResult::Success` → contributes to `BlockValidateOk`; a slower/loaded node exceeds the wall-clock budget for the same tx → `TransactionResult::Problematic` → `BlockValidateReject{reason_code: ProblematicTransaction}`. This differs from the deterministic Clarity `ExecutionCost`/block-cost-limit accounting (which is based on charged cost units, not wall time) that governs `CostOverflowError`/`BlockCostLimitError` paths elsewhere in the same function; those are deterministic given identical chainstate. The vulnerability is specifically confined to the wall-clock resource-budget path introduced for the per-tx `max_tx_execution_time_secs`/`max_tx_analysis_time_secs`/`max_tx_mem_bytes` enforcement.

An attacker (a single miner-slot holder) can craft a transaction whose Clarity execution time sits close to the configured `block_proposal_max_tx_execution_time_secs` (or analysis-time/memory bound) — e.g., a contract call or contract-publish with an expensive but not clearly-over-budget computation — and place it in the proposed block. Because the outcome now depends on the validating node's transient performance characteristics rather than solely on deterministic chainstate and block bytes, the attacker gains a lever to induce split verdicts across the independent signer set for the identical `signer_signature_hash`.

Existing guards do not close this: the deterministic `CostOverflowError`/`ClarityError::CostError` paths (`stackslib/src/chainstate/stacks/db/transactions.rs:401-451`) are unaffected because they are driven by the shared, deterministic `ExecutionCost` ledger, but the wall-clock resource-budget path is orthogonal and is not deterministic across machines/load.

### Impact Explanation
This breaks the required validity equality: for a single `signer_signature_hash`, all honest signers must reach the same accept/reject verdict. A split verdict means some signers sign (contribute `Ok`) while others `Reject` for a genuinely borderline/adversarially-tuned block, enabling a bad or non-canonical block to accumulate enough signatures from the "Ok" subset while equally-honest signers reject it — a Critical chain-safety violation (signing/finalizing based on non-deterministic validation) matching the specified Critical impact category (finalizing an invalid block via a non-deterministic validation split).

### Likelihood Explanation
Preconditions: the proposed block must contain at least one transaction whose Clarity execution/analysis time is close to the configured `block_proposal_max_tx_execution_time_secs`/`block_proposal_max_tx_analysis_time_secs`/`block_proposal_max_tx_mem_bytes` threshold, so that normal jitter in machine speed/load pushes some nodes over the limit and others not. This is achievable by an unprivileged attacker who only needs one miner slot to propose the block and gossip it to signers (no majority-signer collusion, no auth_token, no local access required). It's repeatable: the attacker can tune the transaction's cost to sit near the wall-clock boundary and repeat the attempt across tenures; likelihood of an actual split depends on real-world timing variance across the signer fleet, which is plausible on heterogeneous hardware or under load but not guaranteed on every attempt.

### Recommendation
Do not classify wall-clock/analysis-time/memory budget exceedances (`Error::ExecutionResourceBudgetExceeded`, `Error::AnalysisResourceBudgetExceeded`) as deterministic "problematic" verdicts that are propagated as block-level rejections with a stable reason code across independent validators. Options:
- Treat resource-budget-exceeded outcomes during block-proposal validation as a validation-infra failure (e.g., `ValidateRejectCode::ChainstateError`/timeout) rather than `ProblematicTransaction`, and avoid caching/blacklisting the tx as "problematic" purely due to wall-clock variance.
- Bound tx execution using a deterministic, cost-metered proxy (Clarity's existing `ExecutionCost` runtime dimension) instead of (or in addition to) wall-clock `Duration`, so all validators reach the same accept/reject decision from identical chainstate and block bytes.
- Alternatively, ensure `block_proposal_max_tx_execution_time_secs`/`max_tx_analysis_time_secs` are configured generously enough, and/or require signers to retry validation and only reject on cost-based, not time-based, failures, before finalizing a `Reject`.

### Proof of Concept
```rust
// stackslib/src/net/api/tests/postblock_proposal.rs (concept)
#[test]
fn validate_is_nondeterministic_under_resource_budget_boundary() {
    // Build a NakamotoBlockProposal whose txs include one contract-call
    // tuned to execute in ~T seconds, where T is close to
    // `max_tx_execution_time_secs`.
    let proposal = build_boundary_case_proposal();

    // Simulate two independent validator runs against identical chainstate
    // snapshots, using a fast per-tx execution budget on one call and a
    // slightly slower artificial delay (or use `TEST_VALIDATE_DELAY_DURATION_SECS`
    // fault injection to simulate load) on the other, to emulate the effect
    // of two independent machines with different clock speeds.
    let (sortdb1, mut chainstate1) = make_snapshot();
    let result1 = proposal.validate(
        &sortdb1, &mut chainstate1,
        /* timeout_secs */ 30,
        /* max_tx_execution_time_secs */ 1, // tight budget -> triggers ExecutionResourceBudgetExceeded
        /* max_tx_analysis_time_secs */ 30,
        /* max_tx_mem_bytes */ 0,
        None,
    );

    let (sortdb2, mut chainstate2) = make_snapshot(); // identical chainstate
    let result2 = proposal.validate(
        &sortdb2, &mut chainstate2,
        /* timeout_secs */ 30,
        /* max_tx_execution_time_secs */ 5, // looser budget -> tx succeeds
        /* max_tx_analysis_time_secs */ 30,
        /* max_tx_mem_bytes */ 0,
        None,
    );

    // EXPECTED under safety property: result1 == result2 (both Ok or both Reject)
    // ACTUAL: result1 is Reject(ValidateRejectCode::ProblematicTransaction),
    //         result2 is Ok(..) for the byte-identical `signer_signature_hash`.
    assert_eq!(
        matches!(result1, Err(_)),
        matches!(result2, Err(_)),
        "non-deterministic validation outcome for identical block bytes"
    );
}
```
Note: exact `max_tx_execution_time_secs`/`max_tx_analysis_time_secs` are per-node `ConnectionOptions` values (`stackslib/src/net/connection.rs`), so in production the two divergent parameter sets in the PoC above are the natural analogue of two signer nodes configured (or performing under load) differently — the underlying non-determinism is the same wall-clock-vs-deterministic-cost mismatch.

### Citations

**File:** stackslib/src/net/api/postblock_proposal.rs (L731-753)
```rust
        let block_deadline = Instant::now() + Duration::from_secs(timeout_secs);
        let per_tx_max_execution_time = Duration::from_secs(max_tx_execution_time_secs);
        // Bound the analysis phase during proposal validation by the
        // dedicated per-tx analysis budget, independently of the eval budget above.
        let per_tx_max_analysis_time = Duration::from_secs(max_tx_analysis_time_secs);
        let mut receipts_total = 0u64;

        let max_tx_mem_bytes_opt = if max_tx_mem_bytes > 0 {
            Some(max_tx_mem_bytes)
        } else {
            None
        };
        let resource_budgets = TransactionResourceBudgets::new()
            .with_analysis_budget(
                ResourceBudget::new()
                    .with_max_duration(Some(per_tx_max_analysis_time))
                    .with_max_memory_use(max_tx_mem_bytes_opt),
            )
            .with_execution_budget(
                ResourceBudget::new()
                    .with_max_duration(Some(per_tx_max_execution_time))
                    .with_max_memory_use(max_tx_mem_bytes_opt),
            );
```

**File:** stackslib/src/net/api/postblock_proposal.rs (L808-824)
```rust
                TransactionResult::Problematic(p) => Some((
                    format!("Problematic tx {i}: {}", p.error),
                    ValidateRejectCode::ProblematicTransaction,
                )),
            };
            if let Some((reason, reject_code)) = reason {
                warn!(
                    "Rejected block proposal";
                    "reason" => %reason,
                    "tx" => ?tx,
                );
                return Err(BlockValidateRejectReason {
                    reason,
                    reason_code: reject_code,
                    failed_txid: Some(tx.txid()),
                });
            }
```

**File:** stackslib/src/chainstate/nakamoto/miner.rs (L866-920)
```rust
        let result = {
            // preemptively skip problematic transactions
            if let Err(e) =
                Relayer::static_check_problematic_relayed_tx(is_mainnet, clarity_tx.get_epoch(), tx)
            {
                info!(
                    "Detected problematic tx {} while mining; dropping from mempool",
                    tx.txid()
                );
                return TransactionResult::problematic(tx, Error::NetError(e));
            }

            let cost_before = clarity_tx.cost_so_far();
            let (_fee, receipt) = match StacksChainState::process_transaction_with_check(
                clarity_tx,
                tx,
                quiet,
                resource_budgets,
                |receipt| {
                    if !receipt.post_condition_aborted {
                        let all_events_valid = receipt.events.iter().all(|event| {
                            crate::net::api::postblock_proposal::is_event_pox_addr_valid(
                                is_mainnet, event,
                            )
                        });
                        if !all_events_valid {
                            return Err(Error::ClarityError(ClarityError::BadTransaction(
                                "All PoX events were not valid".into(),
                            )));
                        }
                    };

                    let size = receipt.size().ok_or_else(|| {
                        Error::InvalidStacksBlock("Could not calculate receipt size".into())
                    })?;
                    let next_size = size.saturating_add(*total_receipts_size);
                    if next_size >= MAX_RECEIPT_SIZES {
                        Err(Error::BlockCostExceeded)
                    } else {
                        *total_receipts_size = next_size;
                        Ok(())
                    }
                },
            ) {
                Ok(x) => x,
                Err(e) => {
                    return parse_process_transaction_error(
                        clarity_tx,
                        tx,
                        e,
                        self.contract_limit_percentage
                            .unwrap_or(DEFAULT_CONTRACT_COST_LIMIT_PERCENTAGE),
                    );
                }
            };
```

**File:** stackslib/src/chainstate/stacks/miner.rs (L709-729)
```rust
            Error::ExecutionResourceBudgetExceeded(s) => {
                // The transaction took too long to execute or used too much heap memory. Consider it problematic.
                info!("Problematic transaction caused ExecutionResourceBudgetExceeded";
                      "error" => s.clone(),
                      "txid" => %tx.txid(),
                      "origin" => %tx.get_origin().get_address(false),
                      "payload" => ?tx.payload,
                );
                return (true, Error::ExecutionResourceBudgetExceeded(s));
            }
            Error::AnalysisResourceBudgetExceeded(s) => {
                // The transaction's contract analysis took too long or used too much memory. Consider it problematic
                // so the contract-publish is dropped and blacklisted instead of being re-mined.
                info!("Problematic transaction caused AnalysisResourceBudgetExceeded";
                      "error" => s.clone(),
                      "txid" => %tx.txid(),
                      "origin" => %tx.get_origin().get_address(false),
                      "payload" => ?tx.payload,
                );
                return (true, Error::AnalysisResourceBudgetExceeded(s));
            }
```
