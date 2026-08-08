### Title
`requestAirdrop` accepts `lamports = 0`, letting a caller bypass the faucet's per-time/per-request rate limits while still forcing the faucet to sign and broadcast a fee-paying transaction on every call - (File: `rpc/src/rpc.rs`, `faucet/src/faucet.rs`)

### Summary
The `requestAirdrop` JSON-RPC handler and the faucet's `build_airdrop_transaction`/`check_time_request_limit` logic never validate that the requested `lamports` amount is non-zero. Because the per-time rate-limit cache only accumulates the *requested amount*, a `lamports = 0` request always passes every cap check, yet the faucet still signs a real `Transfer` instruction, serializes it, and the RPC node still submits it to the cluster via `_send_transaction`. This mirrors the `craftAmount = 0` pattern from the external report: a caller-controlled "amount" parameter that can legally be zero bypasses the cost/limit gate while the expensive/paid side effect (here, faucet-signed fee-paying transaction submission) still fires unconditionally on every call.

### Finding Description
`request_airdrop` in `rpc/src/rpc.rs` takes `lamports: u64` directly from the RPC caller with no minimum-value check, and forwards it straight to `request_airdrop_transaction`: [1](#0-0) 

That call reaches `Faucet::build_airdrop_transaction` in `faucet/src/faucet.rs`, whose only "amount" gate compares `lamports > cap` for `per_request_cap`, which is trivially false when `lamports == 0`: [2](#0-1) 

The per-IP/per-address time-window limiter, `check_time_request_limit`, adds `request_amount` into the sliding-window cache and only rejects when the *accumulated* total exceeds `per_time_cap`: [3](#0-2) 

Because `request_amount` is `0` for every such call, the cached total for that IP/address never increases, so the rate limiter can never trip regardless of how many times the same caller requests an airdrop. Despite the transfer amount being zero, the faucet still builds and signs a valid `Transfer` transaction paid for by `faucet_keypair` (the fee payer), and `request_airdrop` in `rpc.rs` still calls `_send_transaction`, pushing the signed transaction into the cluster's transaction-processing/broadcast pipeline: [4](#0-3) 

This is the direct analog of the reported bug: an amount parameter that is allowed to be `0` bypasses the accounting/limit check (`RESERVE_AMOUNT_MUST_BE_NON_ZERO`-style protection is absent here as well), while the costly downstream action (chain state mutation via a faucet-funded transaction, plus RPC/TPU broadcast work) still runs unconditionally per call.

### Impact Explanation
An unprivileged caller can call `requestAirdrop` with `lamports = 0` in an unbounded loop against any RPC node that has a faucet configured (`--rpc-faucet-address`). Every call: (1) never counts against the faucet's per-time/per-IP/per-address caps, defeating the intended anti-abuse limiter entirely for this vector; (2) forces the faucet to sign and pay network fees for a real transaction from its own keypair on every request; and (3) forces the RPC node to serialize and submit that transaction through `_send_transaction`, consuming validator RPC/TPU processing resources for each call at effectively zero cost to the caller. This is an unbounded-cost condition triggered by a single low-value RPC parameter, exactly the class of issue called out as in-scope ("unbounded cost for a single low-rate call").

### Likelihood Explanation
Likelihood is moderate: it requires an RPC node/faucet with `requestAirdrop` enabled (common on devnet/testnet operator setups), no special privileges, and a single trivially-formed RPC call (`lamports: 0`) that any caller can issue repeatedly.

### Recommendation
Add an explicit `lamports > 0` check in `request_airdrop` (`rpc/src/rpc.rs`) and/or in `Faucet::build_airdrop_transaction` (`faucet/src/faucet.rs`), rejecting zero-value airdrop requests before they reach `check_time_request_limit` and before a transaction is built/signed/submitted, so the rate limiter cannot be starved with no-op requests.

### Proof of Concept
1. Start an RPC node/test validator with a faucet configured (`--rpc-faucet-address`).
2. Repeatedly call `requestAirdrop(pubkey, 0)` from the same IP/pubkey.
3. Observe that `check_time_request_limit` never rejects the calls (cache total stays at 0 regardless of call count), while each call still causes `Faucet::build_airdrop_transaction` to sign a fee-paying `Transfer` transaction and `rpc.rs::request_airdrop` to submit it via `_send_transaction`, demonstrating unbounded free consumption of faucet signing/fee and RPC transaction-submission resources.

### Citations

**File:** rpc/src/rpc.rs (L3808-3839)
```rust
        fn request_airdrop(
            &self,
            meta: Self::Metadata,
            pubkey_str: String,
            lamports: u64,
            config: Option<RpcRequestAirdropConfig>,
        ) -> Result<String> {
            debug!("request_airdrop rpc request received");
            trace!("request_airdrop id={pubkey_str} lamports={lamports} config: {config:?}");

            let faucet_addr = meta.config.faucet_addr.ok_or_else(Error::invalid_request)?;
            let pubkey = verify_pubkey(&pubkey_str)?;

            let config = config.unwrap_or_default();
            let bank = meta.bank(config.commitment);

            let blockhash = if let Some(blockhash) = config.recent_blockhash {
                verify_hash(&blockhash)?
            } else {
                bank.confirmed_last_blockhash()
            };
            let last_valid_block_height = bank
                .get_blockhash_last_valid_block_height(&blockhash)
                .unwrap_or(0);

            let transaction =
                request_airdrop_transaction(&faucet_addr, &pubkey, lamports, blockhash).map_err(
                    |err| {
                        info!("request_airdrop_transaction failed: {err:?}");
                        Error::internal_error()
                    },
                )?;
```

**File:** rpc/src/rpc.rs (L3840-3863)
```rust

            let wire_transaction = wincode::serialize(&transaction).map_err(|err| {
                info!("request_airdrop: serialize error: {err:?}");
                Error::internal_error()
            })?;

            let signature = if !transaction.signatures.is_empty() {
                transaction.signatures[0]
            } else {
                return Err(RpcCustomError::TransactionSignatureVerificationFailure.into());
            };
            let message_hash = transaction.message().hash();

            _send_transaction(
                meta,
                message_hash,
                signature,
                blockhash,
                wire_transaction,
                last_valid_block_height,
                None,
                None,
            )
        }
```

**File:** faucet/src/faucet.rs (L146-164)
```rust
    pub fn check_time_request_limit<T: LimitByTime + std::fmt::Display>(
        &mut self,
        request_amount: u64,
        to: T,
    ) -> Result<(), FaucetError> {
        let new_total = to.check_cache(self, request_amount);
        to.datapoint_info(request_amount, new_total);
        if let Some(cap) = self.per_time_cap
            && new_total > cap
        {
            return Err(FaucetError::PerTimeCapExceeded(
                build_balance_message(request_amount, false, false),
                to.to_string(),
                build_balance_message(new_total, false, false),
                build_balance_message(cap, false, false),
            ));
        }
        Ok(())
    }
```

**File:** faucet/src/faucet.rs (L194-220)
```rust
                if let Some(cap) = self.per_request_cap
                    && lamports > cap
                {
                    let memo = format!(
                        "{}",
                        FaucetError::PerRequestCapExceeded(
                            build_balance_message(lamports, false, false),
                            build_balance_message(cap, false, false),
                        )
                    );
                    let memo_instruction = Instruction {
                        program_id: spl_memo_interface::v4::id(),
                        accounts: vec![],
                        data: memo.as_bytes().to_vec(),
                    };
                    let message = Message::new(&[memo_instruction], Some(&mint_pubkey));
                    return Ok(FaucetTransaction::Memo((
                        Transaction::new(&[&self.faucet_keypair], message, blockhash),
                        memo,
                    )));
                }
                if !ip.is_loopback() && !self.allowed_ips.contains(&ip) {
                    self.check_time_request_limit(lamports, ip)?;
                }
                self.check_time_request_limit(lamports, to)?;

                let transfer_instruction = transfer(&mint_pubkey, &to, lamports);
```
