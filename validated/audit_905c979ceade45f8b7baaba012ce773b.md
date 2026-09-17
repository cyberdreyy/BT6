## Analysis

The reported TRON incident is a **roll-back attack**: the attacker gets a DApp to treat a not-yet-final blockchain state as a trustworthy confirmation, collects a payout based on that confirmation, and then the underlying transaction/state is reversed, leaving the DApp with the loss. The reachable analog in this codebase is the `eth_sendRawTransactionSync` RPC method exposed by the flashblocks-enabled node, which can return a "final" receipt for a transaction that is only included in unconfirmed, reorg-prone flashblock state rather than the canonical chain.

### Title
`eth_sendRawTransactionSync` can return a receipt for a transaction that is later dropped or altered by a flashblocks reorg - (File: `crates/execution/flashblocks/src/rpc/eth.rs`)

### Summary
The flashblocks override of `eth_sendRawTransactionSync` races two receipt sources — canonical inclusion and *pending flashblock* inclusion — and returns whichever resolves first. Because flashblocks are speculative, pre-canonical block previews that are explicitly documented to be subject to reordering and reversal ("HandleReorg"), a caller can receive a receipt for a transaction that never lands in the canonical chain, or lands with different execution results.

### Finding Description
`send_raw_transaction_sync` submits the transaction and then races two futures with `tokio::select!`: `wait_for_flashblocks_receipt` and `wait_for_canonical_receipt`, returning the first winner. [1](#0-0) 

`wait_for_flashblocks_receipt` returns as soon as *any* published pending (flashblock) state contains a receipt for the hash — it performs no check that this flashblock ever becomes canonical: [2](#0-1) 

The non-flashblocks implementation of the same RPC method, by contrast, only resolves on a canonical-state stream notification, i.e. genuine finality: [3](#0-2) 

The crate's own design documentation confirms that flashblock content is not final and can be discarded or rewritten: when the canonical block's actual transaction set differs from what pending flashblocks tracked, `ReorgDetector`/`CanonicalBlockReconciler` triggers `HandleReorg`, and "Flashblocks beyond the canonical block are re-executed from canonical state without reusing the existing pending state." [4](#0-3) [5](#0-4) 

Because the RPC call has already returned by the time such a reconciliation happens, there is no mechanism to inform the caller that the "confirmed" receipt they received does not correspond to what ultimately lands on-chain.

### Impact Explanation
Any unprivileged, anonymous RPC client (including a DApp/exchange integrator relying on `eth_sendRawTransactionSync` for fast finality signaling) can be handed a receipt indicating a transaction succeeded, while the transaction is later dropped from, or executes differently in, the canonical chain. A counterparty who releases funds or goods upon seeing this receipt can be defrauded exactly as in the reported TRON roll-back incident — the "confirmation" it relied on is retroactively invalidated. This is a genuine funds-theft/fraud vector for integrators, not merely a resource or availability issue.

### Likelihood Explanation
This does not require a malicious sequencer, builder, or node — it is a property of ordinary, honest flashblock production: flashblocks are speculative previews and are routinely revised as new information arrives (mempool changes, reordering, fee market changes) before the block is canonicalized. The race always favors the (much faster) flashblock branch under normal operating conditions, since flashblock intervals are sub-second while canonical block time is materially longer, so the flashblock receipt will typically win the `tokio::select!` race. Any transaction with a reasonable chance of being reordered/dropped between flashblocks (e.g., colliding nonce, fee competition, dependent state changes) can trigger this.

### Recommendation
`eth_sendRawTransactionSync` should not resolve on `wait_for_flashblocks_receipt` alone. Either remove the flashblocks-only race path so this method always waits for canonical inclusion (matching the non-flashblocks implementation), or require confirmation that the winning flashblock's containing block has been canonicalized (or matches canonical content) before returning the receipt to the caller.

### Proof of Concept
1. Start a `base-flashblocks-node` with `eth_sendRawTransactionSync` enabled.
2. Submit transaction `T` via `eth_sendRawTransactionSync`.
3. Have the builder emit a flashblock including `T` (this is exactly what `test_send_raw_transaction_sync` in the test suite exercises) — the RPC call returns a receipt for `T`. [6](#0-5) 
4. Before the block containing `T` is canonicalized, have the builder republish a later flashblock sequence that drops or reorders `T` out of that block (e.g., due to fee competition or dependent-state invalidation), producing a canonical block whose transaction set differs from what was tracked — triggering `HandleReorg` in `process_canonical_block`. [7](#0-6) 
5. The caller already holds a receipt claiming `T` succeeded, even though `T` is absent from (or executes differently in) the canonical chain — a rolled-back confirmation an integrator could have already acted upon.

### Citations

**File:** crates/execution/flashblocks/src/rpc/eth.rs (L402-415)
```rust
        let timeout = Duration::from_millis(timeout_ms);
        tokio::select! {
            receipt = self.wait_for_flashblocks_receipt(tx_hash) => {
                receipt.ok_or_else(|| EthApiError::TransactionConfirmationTimeout {
                    hash: tx_hash,
                    duration: timeout,
                }.into())
            }
            receipt = self.wait_for_canonical_receipt(tx_hash) => {
                receipt.ok_or_else(|| EthApiError::TransactionConfirmationTimeout {
                    hash: tx_hash,
                    duration: timeout,
                }.into())
            }
```

**File:** crates/execution/flashblocks/src/rpc/eth.rs (L659-680)
```rust
    async fn wait_for_flashblocks_receipt(&self, tx_hash: TxHash) -> Option<RpcReceipt<Base>> {
        let mut receiver = self.flashblocks_state.subscribe_to_flashblocks();

        loop {
            match receiver.recv().await {
                Ok(pending_state) if pending_state.get_receipt(tx_hash).is_some() => {
                    debug!(message = "found receipt in flashblock", tx_hash = %tx_hash);
                    return pending_state.get_receipt(tx_hash).cloned();
                }
                Ok(_) => {
                    trace!(message = "flashblock does not contain receipt", tx_hash = %tx_hash);
                }
                Err(RecvError::Closed) => {
                    debug!(message = "flashblocks receipt queue closed");
                    return None;
                }
                Err(RecvError::Lagged(_)) => {
                    warn!("Flashblocks receipt queue lagged, maybe missing receipts");
                }
            }
        }
    }
```

**File:** crates/execution/rpc/src/eth/transaction.rs (L130-151)
```rust
        async move {
            // Subscribe before submission so immediate inclusion cannot race the receipt listener.
            let mut canonical_stream = this.provider().canonical_state_stream();
            let hash = EthTransactions::send_raw_transaction(&this, tx).await?;

            tokio::time::timeout(timeout_duration, async {
                while let Some(notification) = canonical_stream.next().await {
                    let chain = notification.committed();
                    if let Some((block, tx, receipt, all_receipts)) =
                        chain.find_transaction_and_receipt_by_hash(hash)
                        && let Some(receipt) = convert_transaction_receipt(
                            block,
                            all_receipts,
                            tx,
                            receipt,
                            this.converter(),
                        )
                        .transpose()?
                    {
                        return Ok(receipt);
                    }
                }
```

**File:** crates/execution/flashblocks/src/processor.rs (L396-443)
```rust
        // Check for reorg by comparing transaction sets
        let tracked_txns = pending_blocks.get_transactions_for_block(block.number);
        let tracked_txn_hashes: Vec<_> = tracked_txns.map(|tx| tx.tx_hash()).collect();
        let block_txn_hashes: Vec<_> = block.body().transactions().map(|tx| tx.tx_hash()).collect();

        let reorg_result = ReorgDetector::detect(&tracked_txn_hashes, &block_txn_hashes);
        let reorg_detected = reorg_result.is_reorg();

        // Determine the reconciliation strategy. Reorg detection compares against the block
        // we were notified about, but reconciliation must use the node's real canonical height
        // so a lagging queue cannot hide that pending has drifted away from the tip.
        let canonical_number = self.effective_canonical_number(block.number);
        let strategy = CanonicalBlockReconciler::reconcile(
            Some(pending_blocks.earliest_block_number()),
            Some(pending_blocks.latest_block_number()),
            canonical_number,
            self.max_depth,
            reorg_detected,
        );

        match strategy {
            ReconciliationStrategy::CatchUp => {
                debug!(
                    message = "pending snapshot cleared because canonical caught up",
                    latest_pending_block = pending_blocks.latest_block_number(),
                    notified_block = block.number,
                    canonical_block = canonical_number,
                );
                Metrics::pending_clear_catchup().increment(1);
                Metrics::pending_snapshot_fb_index()
                    .set(pending_blocks.latest_flashblock_index() as f64);
                self.clear_live_state();
                Ok(None)
            }
            ReconciliationStrategy::HandleReorg => {
                warn!(
                    message = "reorg detected, recomputing pending flashblocks going ahead of reorg",
                    tracked_txn_hashes = ?tracked_txn_hashes,
                    block_txn_hashes = ?block_txn_hashes,
                );
                Metrics::pending_clear_reorg().increment(1);

                // Rebuild from the real tip, not the notified height: under queue lag those
                // two differ, and re-executing the already-canonical range cannot publish.
                flashblocks
                    .retain(|flashblock| flashblock.metadata.block_number > canonical_number);
                self.build_pending_state(None, &flashblocks)
            }
```

**File:** crates/execution/flashblocks/README.md (L104-115)
```markdown
### Canonical reconciliation

When a canonical block is applied, `ReorgDetector` compares the transactions pending had tracked
for that block against the block's actual transactions. If pending exists,
`CanonicalBlockReconciler` then chooses one of:

- **`CatchUp`**: canonical reached or passed pending's latest block. Pending is cleared.
- **`HandleReorg`**: the transaction sets differ. Flashblocks beyond the canonical block are
  re-executed from canonical state without reusing the existing pending state.
- **`DepthLimitExceeded`**: pending retains more than `max_pending_blocks_depth` blocks that the
  canonical chain has already covered. Pending is rebuilt from the canonical block forward.
- **`Continue`**: no conflict. The existing pending state is extended.
```

**File:** crates/execution/flashblocks-node/tests/flashblocks_rpc.rs (L748-769)
```rust
#[tokio::test]
async fn test_send_raw_transaction_sync() -> Result<()> {
    let setup = TestSetup::new().await?;

    setup.send_flashblock(setup.create_first_payload()).await?;

    // run the Tx sync and, in parallel, deliver the payload that contains the Tx
    let second_payload = setup.create_second_payload();
    let (receipt_result, payload_result) = tokio::join!(
        setup.send_raw_transaction_sync(setup.txn_details.alice_eth_transfer_tx.clone(), None),
        async {
            tokio::time::sleep(std::time::Duration::from_millis(100)).await;
            setup.send_flashblock(second_payload).await
        }
    );

    payload_result?;
    let receipt = receipt_result?;

    assert_eq!(receipt.transaction_hash(), setup.txn_details.alice_eth_transfer_hash);
    Ok(())
}
```
