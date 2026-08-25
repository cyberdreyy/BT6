### Title
Transaction Log Messages and Return Data Are Excluded From the Bank Hash, Letting an Untrusted RPC/Geyser Reporter Fabricate Off-Chain-Consumed Program Output - (File: `runtime/src/bank.rs`, `svm/src/transaction_execution_result.rs`, `rpc/src/transaction_status_service.rs`)

### Summary
The Hinkal finding is a case of "data that downstream users cryptographically rely on for correctness is emitted alongside a transaction but is not covered by any value that is actually verified (`callDataHash`)." The structural analog in Agave is that `log_messages`, `return_data`, and `inner_instructions` produced by transaction execution (e.g. via `sol_log_data`/`sol_set_return_data`, used by programs to publish structured outputs such as encrypted UTXO payloads for privacy protocols, swap amounts, oracle values, etc.) are never included in the bank hash that other validators check for consensus. Only account state changes (via `accounts_lt_hash`) are consensus-checked. This means a single leader/validator's local execution record of these fields — the copy that actually reaches RPC clients, geyser plugins, and the blockstore's `TransactionStatusMeta` — is never independently verifiable against a canonical, cross-validated value, exactly mirroring the unverified `encryptedOutputs` field in Hinkal.

### Finding Description
`Bank::hash_internal_state` computes the consensus-critical bank hash strictly from `parent_hash`, `signature_count`, `last_blockhash`, and the `accounts_lt_hash` (account state): [1](#0-0) 

Meanwhile, `TransactionExecutionDetails` — which carries `log_messages`, `inner_instructions`, and `return_data` produced during execution — is a completely separate structure that is never folded into that hash: [2](#0-1) 

This same unverified data is exactly what's forwarded to RPC clients and geyser/transaction notifiers as `TransactionStatusMeta`/`CommittedTransaction`: [3](#0-2) 

and it is also captured separately in `TransactionCommitDetails` for bank-hash-details tooling, again decoupled from the actual hash computation: [4](#0-3) 

Just as Hinkal's `encryptedOutputs` parameter is logged for off-chain decryption but omitted from `callDataHash` (so a relayer can swap it without invalidating the ZK proof), Solana's execution logs/return data are produced deterministically by the SVM but are excluded from the one value (`bank_hash`) that consensus actually cross-checks. Any protocol or client that relies on `log_messages`/`return_data` fetched from a single RPC endpoint or geyser plugin (rather than reading committed account state) to reconstruct sensitive off-chain artifacts (encrypted notes, order fills, oracle prices, cross-chain message payloads, etc.) is trusting that single reporter's honesty with no cryptographic backstop — the network provides no way to prove the reported logs are what other validators actually computed.

### Impact Explanation
A malicious or compromised RPC node / geyser consumer (the Solana-side counterpart of Hinkal's "malicious relayer") can serve a client fabricated `log_messages` or `return_data` for a transaction that otherwise executed correctly and produced a valid bank hash. For any application built on Solana that encodes user-critical output (e.g., encrypted payloads, computed amounts, proof outputs) in logs/return data instead of committed account state, this allows exactly the same failure mode described in the report: users fail to recover correct off-chain state, potentially leading to loss of funds or untraceable protocol interactions, without any on-chain signal that something was tampered with, since the transaction's success/status and bank hash remain valid.

### Likelihood Explanation
This is reachable by any ordinary RPC consumer without needing validator/leader privilege — an operator of a public or "free" RPC endpoint, or a geyser-plugin-based indexer, controls exactly this reporting path and there's no cryptographic commitment client-side to cross-check log/return-data integrity beyond re-simulating the transaction with a trusted node. Applications that build privacy/settlement logic on top of program logs (rather than committed account state) inherit this weaker guarantee by design of the Agave hashing model.

### Recommendation
Applications should never treat `log_messages`/`return_data`/`inner_instructions` as tamper-proof; sensitive commitments should be written into account data (which is covered by `accounts_lt_hash` and thus consensus-verified) rather than solely emitted as logs/return data. At minimum, clients relying on log-derived data should independently corroborate it across multiple validators/RPC providers or by re-deriving expected values from committed account state.

### Proof of Concept
1. Deploy a program that emits sensitive output solely via `sol_log_data`/`sol_set_return_data` (e.g., an encrypted payload analogous to Hinkal's `encryptedOutputs`).
2. Submit a transaction; note that its execution succeeds and the resulting bank hash is identical regardless of what is logged, since `hash_internal_state` never incorporates `log_messages`/`return_data`.
3. Have a colluding/malicious RPC or geyser reporter return altered `log_messages`/`return_data` for that transaction's `getTransaction`/geyser notification while the underlying account state (and thus bank hash) is unaffected.
4. Clients depending on this endpoint receive corrupted payloads, unrecoverable off-chain artifacts, with no on-chain evidence of tampering.

### Citations

**File:** runtime/src/bank.rs (L5345-5360)
```rust
    fn hash_internal_state(&self) -> Hash {
        let measure_total = Measure::start("");
        let slot = self.slot();

        let mut hash = hashv(&[
            self.parent_hash.as_ref(),
            &self.signature_count().to_le_bytes(),
            self.last_blockhash().as_ref(),
        ]);

        let accounts_lt_hash_checksum = {
            let accounts_lt_hash = &*self.accounts_lt_hash.lock().unwrap();
            let lt_hash_bytes = bytemuck::must_cast_slice(&accounts_lt_hash.0.0);
            hash = hashv(&[hash.as_ref(), lt_hash_bytes]);
            accounts_lt_hash.0.checksum()
        };
```

**File:** svm/src/transaction_execution_result.rs (L30-40)
```rust
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct TransactionExecutionDetails {
    pub status: TransactionResult<()>,
    pub log_messages: Option<Vec<String>>,
    pub inner_instructions: Option<InnerInstructionsList>,
    pub return_data: Option<TransactionReturnData>,
    pub executed_units: u64,
    /// deltas related to total account data size changes for this transaction.
    /// NOTE: set to None IFF `status` is not `Ok`.
    pub accounts_deltas: Option<AccountsDeltas>,
}
```

**File:** rpc/src/transaction_status_service.rs (L172-205)
```rust
                    let CommittedTransaction {
                        status,
                        log_messages,
                        inner_instructions,
                        return_data,
                        executed_units,
                        fee_details,
                        ..
                    } = committed_tx;

                    let fee = fee_details.total_fee();
                    let inner_instructions = inner_instructions.map(|inner_instructions| {
                        map_inner_instructions(inner_instructions).collect()
                    });

                    let pre_token_balances = Some(pre_token_balances);
                    let post_token_balances = Some(post_token_balances);
                    let rewards = Some(vec![]);
                    let loaded_addresses = transaction.get_loaded_addresses();
                    let mut transaction_status_meta = TransactionStatusMeta {
                        status,
                        fee,
                        pre_balances,
                        post_balances,
                        inner_instructions,
                        log_messages,
                        pre_token_balances,
                        post_token_balances,
                        rewards,
                        loaded_addresses,
                        return_data,
                        compute_units_consumed: Some(executed_units),
                        cost_units: cost,
                    };
```

**File:** runtime/src/bank/bank_hash_details.rs (L68-98)
```rust
#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize, Default)]
pub struct TransactionDetails {
    pub signature: String,
    pub index: usize,
    pub accounts: Vec<String>,
    pub instructions: Vec<UiInstruction>,
    pub is_simple_vote_tx: bool,
    pub commit_details: Option<TransactionCommitDetails>,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct TransactionCommitDetails {
    pub status: TransactionResult<()>,
    pub log_messages: Option<Vec<String>>,
    pub inner_instructions: Option<InnerInstructionsList>,
    pub return_data: Option<TransactionReturnData>,
    pub executed_units: u64,
    pub fee_details: FeeDetails,
}

impl From<CommittedTransaction> for TransactionCommitDetails {
    fn from(committed_tx: CommittedTransaction) -> Self {
        Self {
            status: committed_tx.status,
            log_messages: committed_tx.log_messages,
            inner_instructions: committed_tx.inner_instructions,
            return_data: committed_tx.return_data,
            executed_units: committed_tx.executed_units,
            fee_details: committed_tx.fee_details,
        }
    }
```
