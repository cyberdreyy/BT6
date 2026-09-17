## Finding

### Title
Unbounded L1 deposit `opaqueData`/`input` size lets an unprivileged depositor permanently halt the Base batcher pipeline - (File: `crates/batcher/encoder/src/encoder.rs`)

### Summary
`TxDeposit.input` is derived from the `TransactionDeposited` L1 event's `opaqueData` with no size cap anywhere in the decode/derivation path, and unlike ordinary mempool transactions, deposit transactions are protocol-mandatory: they must be included in the corresponding L2 block regardless of size, and the block itself cannot be skipped because "skipping the block would gap the L2 sequence." If a depositor submits an oversized `depositTransaction` call on L1, the resulting L2 block cannot fit in an empty derivation channel, and the batcher's encoding pipeline returns a documented "Fatal: do not continue" error that halts the batcher process. This is the same bug class as the Synapse advisory: an unbounded, attacker-controlled field embedded in a unit of data that *must* be processed/forwarded (an invite event there, a deposit-carrying L2 block here) permanently breaks the downstream transport once it crosses a hard size limit.

### Finding Description
1. **Unbounded field.** `Deposits::unmarshal_v0` copies the entire remainder of the L1 log's `opaqueData` into `tx.input` with no length cap: `tx.input = Bytes::copy_from_slice(&data[offset..offset + tx_data_len]);` [1](#0-0)  The harness/action test confirms the on-chain shape being mirrored — an arbitrary-length `data: Vec<u8>` field flows straight into the deposit's opaque payload. [2](#0-1)  No downstream check in `Deposits::decode`/`unmarshal_v0` bounds `opaque_content_len` or the resulting `input` length. [3](#0-2) 

2. **Mandatory, unskippable inclusion.** Deposit transactions cannot be excluded from a block for any reason — a failing/oversized deposit is still committed with nonce bump and mint credited: "if for any reason a deposit transaction fails, the transaction must still be included in the block." [4](#0-3)  The sequencer's attribute-execution path (`execute_sequencer_transactions`) iterates `self.attributes().transactions` unconditionally and applies no DA/size gate to these transactions (deposits are explicitly excluded from DA accounting) — the only checks are EVM execution-error checks, not size checks. [5](#0-4)  The mempool-only `is_tx_over_limits`/DA gates in the payload builder are likewise bypassed for deposits — that path explicitly skips DA-limit filtering for deposit/blob txs since they never come from the pool. [6](#0-5) 

3. **Batcher cannot make progress.** `BatchEncoder::step()` tries to add the resulting `SingleBatch` (which includes the oversized deposit) to a channel. If the channel is empty and the batch alone exceeds `ChannelLimit::RlpBytes`/`FrameCount`/`AssembledBytes`, the encoder discards the channel and returns `StepError::BlockExceedsChannelLimit`, which is documented as fatal: "A block cannot fit in an empty derivation channel ... Encoding failed. Fatal: do not continue." [7](#0-6)  The encoder's own comment states composition/discard failures are fatal precisely because skipping the block would create a gap in the L2 sequence — there is no mechanism to drop or truncate the offending block. [8](#0-7) 

4. **Process halt.** `BatchDriver::drain_encoding` propagates this as a hard error and the driver logs "fatal encoding step error, batcher halting" and returns `Err`, terminating the batcher's run loop. [9](#0-8)  `BatchDriverError::Step` documents this exact semantics: "A block could not be composed into a batch; continuing would produce a gap in the submitted L2 block sequence." [10](#0-9)  Since the encoder retains `block_cursor` pointing at the same undropped block and the channel that rejected it was discarded, restarting the batcher re-derives the identical failing block and hits the same fatal error again — there is no automatic recovery path.

This is the direct analog of the Synapse bug class: an unbounded, attacker-controlled data field (`opaqueData`/`input` here, `invite_room_state` there) embedded in a unit that a system is obligated to forward/process (a mandatory L2 deposit tx here, a room invite there) becomes oversized and permanently breaks the downstream transport (batcher pipeline here, outbound federation there) once accepted.

### Impact Explanation
An unprivileged L1 account calling the deposit-bridge contract with a sufficiently large `_data` payload (bounded only by L1 calldata/gas limits, which are far larger than any reasonable L2 derivation-channel byte/frame limit) can force the L2 sequencer to build a block containing an unbatchable deposit transaction. Once that block reaches the batcher, the pipeline halts (`BatchDriverError::Step`), stopping all subsequent L1 batch submissions for every user on the chain — not just a specific counterparty as in the Synapse case. This prevents the safe/finalized head from advancing on L1, which is a node/chain-halting availability impact.

### Likelihood Explanation
This requires only a single L1 transaction from any depositor account with enough L1 gas to submit large calldata (or via the deposit-bridge’s `data` field) — no special privileges, and it can be repeated deterministically. The only obstacle is the actual derivation-channel byte/frame limits configured in `RollupConfig`; these are generally on the order of tens/hundreds of KB to low MB, well within reach of a single L1 calldata transaction.

### Recommendation
Enforce a maximum size on the deposit `opaqueData`/derived `input` field during `Deposits::decode`/`unmarshal_v0` (or at attribute-building time) that is provably smaller than the smallest configured derivation-channel/frame limit across all forks, and reject/deterministically handle oversized deposits at derivation time (matching op-stack's actual `MAX_RESOURCE_ID` / opaque-data bound conventions) rather than only bounding it as far as L1 gas allows. Add regression tests that build a deposit-only block whose single deposit input is at/just-over every configured channel limit (`RlpBytes`, `FrameCount`, `AssembledBytes`) and assert the batcher pipeline does not enter a permanently fatal, unrecoverable state.

### Proof of Concept
1. On L1, call the deposit bridge's `depositTransaction` (or equivalent) with `_data` sized just over the configured `max_rlp_bytes_per_channel` (or high enough to blow `ChannelLimit::FrameCount`/`AssembledBytes` for an otherwise-empty channel), e.g. reusing the harness helper shape: `UserDeposit { data: vec![0u8; N], .. }` with `N` chosen so the resulting `TxDeposit` alone exceeds the channel limit. [2](#0-1) 
2. The sequencer derives/produces an L2 block whose first transaction is this oversized deposit (mandatory inclusion, no DA gate). [5](#0-4) 
3. The batcher's `BatchEncoder::step()` opens a new (empty) channel for this block, and `Channel::add_batch` rejects it against `ChannelLimit`; since the channel is empty, the encoder discards the channel and raises `StepError::BlockExceedsChannelLimit`, exactly mirrored by the existing unit test `test_step_discards_block_that_exceeds_empty_channel`. [11](#0-10) 
4. `BatchDriver::run`/`drain_encoding` propagates this as a fatal `BatchDriverError::Step`, halting the batcher process; `block_cursor` still points at the same undeliverable block on restart. [9](#0-8)

### Citations

**File:** crates/consensus/protocol/src/deposits.rs (L219-228)
```rust
impl Deposits {
    /// Unmarshals a deposit transaction from the opaque data.
    pub fn unmarshal_v0(
        tx: &mut TxDeposit,
        to: Address,
        data: &[u8],
    ) -> Result<(), DepositDecodeError> {
        if data.len() < 32 + 32 + 8 + 1 {
            return Err(DepositDecodeError::UnexpectedOpaqueDataLen(data.len()));
        }
```

**File:** crates/consensus/protocol/src/deposits.rs (L259-266)
```rust
        // The remainder of the opaqueData is the transaction data (without length prefix).
        // The data may be padded to a multiple of 32 bytes
        let tx_data_len = data.len() - offset;

        // Remaining bytes fill the data
        tx.input = Bytes::copy_from_slice(&data[offset..offset + tx_data_len]);

        Ok(())
```

**File:** actions/harness/src/l1/miner.rs (L402-429)
```rust
    /// Queue a `TransactionDeposited` log for the next mined block.
    ///
    /// Mirrors the on-chain `OptimismPortal.sol` `TransactionDeposited` event.
    /// The derivation pipeline reads this from L1 receipts to include the
    /// deposit in the corresponding L2 block's attribute set.
    pub fn enqueue_user_deposit(&mut self, deposit: &UserDeposit) {
        self.enqueue_user_deposit_with_status(deposit, true);
    }

    /// Queue a `TransactionDeposited` log for the next mined block with an
    /// explicit receipt status.
    pub fn enqueue_user_deposit_with_status(&mut self, deposit: &UserDeposit, success: bool) {
        // opaqueData: mint(32) + value(32) + gas_limit(8) + isCreation(1) + calldata
        let opaque_len = 32 + 32 + 8 + 1 + deposit.data.len();
        let opaque_padded = opaque_len.div_ceil(32) * 32;
        let total_len = 64 + opaque_padded; // offset(32) + length(32) + padded opaqueData

        let mut log_data = vec![0u8; total_len];
        log_data[24..32].copy_from_slice(&32u64.to_be_bytes()); // offset
        log_data[56..64].copy_from_slice(&(opaque_len as u64).to_be_bytes()); // length

        let base = 64;
        log_data[base + 16..base + 32].copy_from_slice(&deposit.mint.to_be_bytes());
        log_data[base + 32..base + 64].copy_from_slice(&deposit.value.to_be_bytes::<32>());
        log_data[base + 64..base + 72].copy_from_slice(&deposit.gas_limit.to_be_bytes());
        log_data[base + 72] = 0; // isCreation: false
        log_data[base + 73..base + 73 + deposit.data.len()].copy_from_slice(&deposit.data);

```

**File:** crates/common/evm/src/transaction/error.rs (L45-52)
```rust
    /// In addition, this error is internal, and bubbles up into a [`BaseHaltReason::FailedDeposit`][crate::BaseHaltReason::FailedDeposit] error
    /// in the `revm` handler for the consumer to easily handle. This is due to a state transition
    /// rule on Base where, if for any reason a deposit transaction fails, the transaction
    /// must still be included in the block, the sender nonce is bumped, the `mint` value persists, and
    /// special gas accounting rules are applied. Normally on L1, [`EVMError::Transaction`] errors
    /// are cause for non-inclusion, so a special [`BaseHaltReason`][crate::BaseHaltReason] variant was introduced to handle this
    /// case for failed deposit transactions.
    DepositSystemTxPostRegolith,
```

**File:** crates/builder/core/src/flashblocks/context.rs (L636-645)
```rust
            // add gas used by the transaction to cumulative gas used, before creating the receipt
            let gas_used = result.tx_gas_used();
            info.cumulative_gas_used += gas_used;

            if !sequencer_tx.is_deposit() {
                info.cumulative_da_bytes_used += base_common_flz::tx_estimated_size_fjord_bytes(
                    sequencer_tx.encoded_2718().as_slice(),
                );
                info.cumulative_uncompressed_bytes += sequencer_tx.encode_2718_len() as u64;
            }
```

**File:** crates/execution/payload/src/builder.rs (L1300-1319)
```rust
            if info.is_tx_over_limits(
                tx_da_size,
                block_gas_limit,
                tx_da_limit,
                block_da_limit,
                tx.gas_limit().saturating_add(tx_payer_auth),
                da_footprint_gas_scalar,
            ) {
                // we can't fit this transaction into the block, so we need to mark it as
                // invalid which also removes all dependent transaction from
                // the iterator before we can continue
                Self::skip_current(&mut best_txs, tx.signer(), tx.nonce(), replay_independent);
                continue;
            }

            // A sequencer's block should never contain blob or deposit transactions from the pool.
            if tx.is_eip4844() || tx.is_deposit() {
                Self::skip_current(&mut best_txs, tx.signer(), tx.nonce(), replay_independent);
                continue;
            }
```

**File:** crates/batcher/encoder/src/pipeline.rs (L20-39)
```rust
/// Encoding failed. Fatal: do not continue.
#[derive(Debug, thiserror::Error)]
pub enum StepError {
    /// The block could not be converted to a [`base_protocol::SingleBatch`].
    #[error("batch composition failed for block at cursor {cursor}: {source}")]
    CompositionFailed {
        /// Index of the block in the encoder's input queue.
        cursor: usize,
        /// Underlying composition error.
        #[source]
        source: BatchComposeError,
    },
    /// A block cannot fit in an empty derivation channel.
    #[error("block at cursor {cursor} exceeds a derivation channel limit: {limit}")]
    BlockExceedsChannelLimit {
        /// Index of the block in the encoder's input queue.
        cursor: usize,
        /// Limit that the block exceeded in an otherwise empty channel.
        limit: ChannelLimit,
    },
```

**File:** crates/batcher/encoder/src/encoder.rs (L482-513)
```rust
        // Composition failure is fatal: skipping the block would gap the L2 sequence.
        let single_batch = BatchComposer::block_to_single_batch(block)
            .map_err(|source| StepError::CompositionFailed { cursor: self.block_cursor, source })?;

        if !self.channels.back().is_some_and(Channel::is_open) {
            self.open_new_channel(self.block_cursor);
        }

        let channel = self.channels.back_mut().expect("channel exists after open_new_channel");
        let outcome = channel.add_batch(&single_batch, block_da_backlog_bytes)?;

        match outcome {
            accepted @ (ChannelAddOutcome::Accepted | ChannelAddOutcome::TargetReached) => {
                // Cursor advances only after accept, so a later reject retries this block.
                self.block_cursor += 1;
                if accepted == ChannelAddOutcome::TargetReached {
                    self.close_current_channel(ChannelCloseReason::SoftTarget)?;
                    Ok(StepResult::ChannelClosed)
                } else {
                    Ok(StepResult::BlockEncoded)
                }
            }
            ChannelAddOutcome::Rejected(limit) => {
                // Empty channel: this block cannot fit anywhere. Discard and fail.
                if channel.is_empty() {
                    self.channels.pop_back();
                    BatcherMetrics::channel_closed_total(BatcherMetrics::REASON_DISCARD)
                        .increment(1);
                    return Err(StepError::BlockExceedsChannelLimit {
                        cursor: self.block_cursor,
                        limit,
                    });
```

**File:** crates/batcher/encoder/src/encoder.rs (L875-890)
```rust
    #[test]
    fn test_step_discards_block_that_exceeds_empty_channel() {
        let mut encoder = tiny_frame_encoder();
        encoder.add_block(make_block_with_user_tx_bytes(B256::ZERO, 100_000, 1)).unwrap();

        let err = encoder.step().unwrap_err();
        assert!(matches!(
            err,
            StepError::BlockExceedsChannelLimit {
                cursor: 0,
                limit: ChannelLimit::FrameCount { .. }
            }
        ));
        assert!(encoder.channels.is_empty());
        assert_eq!(encoder.block_cursor, 0);
    }
```

**File:** crates/batcher/core/src/driver.rs (L328-346)
```rust
    fn drain_encoding(&mut self) -> Result<bool, BatchDriverError> {
        let mut budget = Self::STEP_BUDGET;
        let mut steps = 0usize;
        let idle = loop {
            match self.pipeline.step() {
                Ok(StepResult::Idle) => break true,
                Ok(StepResult::BlockEncoded | StepResult::ChannelClosed) => {
                    steps += 1;
                    budget -= 1;
                    if budget == 0 {
                        debug!(steps = %steps, "encoding step budget exhausted, yielding");
                        break false;
                    }
                }
                Err(e) => {
                    error!(error = %e, "fatal encoding step error, batcher halting");
                    return Err(e.into());
                }
            }
```

**File:** crates/batcher/core/src/error.rs (L16-19)
```rust
    /// A block could not be composed into a batch; continuing would produce a gap
    /// in the submitted L2 block sequence.
    #[error("fatal pipeline step error: {0}")]
    Step(#[from] StepError),
```
