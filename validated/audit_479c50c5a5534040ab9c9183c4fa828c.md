### Title
Deleted multisig key confirmations remain counted toward the approval threshold, allowing requests to execute below the live k‑of‑n threshold - (File: multisig/src/lib.rs)

### Summary
`MultiSigContract::execute_request`'s `DeleteKey` handler only purges pending requests *proposed* by the deleted key; it never scans `self.confirmations` to remove that key's confirmation from other requests it had *confirmed but not proposed*. Because `confirm()` simply counts entries in the `HashSet<PublicKey>` against `num_confirmations`, a stale confirmation from a revoked key still counts toward the threshold, letting a request execute (including fund transfers, `AddKey`, `DeployContract`, etc.) with fewer live/authorized keys than the configured `num_confirmations`.

### Finding Description
The contract enforces a k‑of‑n approval scheme via `num_confirmations` and a per‑request `HashSet<PublicKey>` of confirming keys: [1](#0-0) 

When a key is removed via `MultiSigRequestAction::DeleteKey`, the cleanup logic is: [2](#0-1) 

This only removes requests where `r.signer_pk == pk` (i.e., requests *proposed* by the deleted key) and clears `num_requests_pk` for that key. It does **not** iterate `self.confirmations` to strip the deleted key's public key from confirmation sets of requests *proposed by other keys* that this key had already confirmed.

Since `confirm()`'s threshold check is purely `confirmations.len() as u32 + 1 >= self.num_confirmations`, any confirmation entry left over from a now-deleted key is indistinguishable from a live one — it still counts.

**Binding broken:** the intended invariant is `live confirming keys ≥ num_confirmations` before a request executes. After a `DeleteKey` action, this becomes `live confirming keys < num_confirmations` while `stored confirmations == num_confirmations`, because a revoked key's stale entry is counted as if it were live.

### Impact Explanation
This is Critical under the stated impact criteria: "a multisig request executed below threshold." A pending, not-yet-fully-confirmed request (e.g., a large `Transfer`, a `DeployContract` upgrade, or an `AddKey` granting full access) can be pushed to execution by counting a deleted signer's stale confirmation, meaning fewer genuinely authorized keys than `num_confirmations` actually approved the action. Funds can move, or privileged actions (contract upgrade, key addition) can execute, without the intended number of live approvers — an authorization-threshold bypass, not merely a revert/DoS as in the referenced report.

### Likelihood Explanation
This requires only ordinary multisig operation, no attacker-controlled parameters beyond normal usage: (1) a request is proposed and partially confirmed by key B, (2) key B is later removed via a normal `DeleteKey` request (a routine operational action, e.g. rotating a compromised or departing signer), (3) the original pending request is still outstanding, (4) it later receives one more live confirmation and executes, silently counting B's stale approval. No special privilege beyond being one of the legitimate keyholders is needed, and the flaw is deterministic in `execute_request`'s `DeleteKey` branch.

### Recommendation
When handling `DeleteKey`, iterate over `self.confirmations` for all outstanding request IDs and remove the deleted `pk` from each `HashSet<PublicKey>`, not just from requests it proposed. Alternatively, revalidate confirmations against the current set of active access keys at execution time in `confirm()` before comparing against `num_confirmations`.

### Proof of Concept
1. Deploy `MultiSigContract::new(3)` with keys A, B, C, D as full-access keys.
2. Key A calls `add_request_and_confirm(request_transfer)` → `confirmations = {A}`.
3. Key B calls `confirm(request_id)` → `confirmations = {A, B}` (count = 2, below threshold 3).
4. Separately, the group proposes and fully confirms a `DeleteKey { public_key: B }` request (using A, C, D confirmations) to revoke B (e.g., because B's device was lost). This executes via `execute_request`'s `DeleteKey` branch shown above — it removes requests *proposed* by B, but `request_transfer`'s confirmation set `{A, B}` is untouched because `request_transfer` was proposed by A, not B.
5. Key C now calls `confirm(request_id)` on `request_transfer`: `confirmations.len() + 1 == 3 >= num_confirmations (3)` → the transfer executes.
6. Result: the transfer executed with confirmations nominally `{A, B, C}`, but B's key was already revoked — only 2 live keys (A, C) actually authorized it, one short of the required 3-of-n threshold.

### Citations

**File:** multisig/src/lib.rs (L198-216)
```rust
                MultiSigRequestAction::DeleteKey { public_key } => {
                    self.assert_self_request(receiver_id.clone());
                    let pk: PublicKey = public_key.into();
                    // delete outstanding requests by public_key
                    let request_ids: Vec<u32> = self
                        .requests
                        .iter()
                        .filter(|(_k, r)| r.signer_pk == pk)
                        .map(|(k, _r)| k)
                        .collect();
                    for request_id in request_ids {
                        // remove confirmations for this request
                        self.confirmations.remove(&request_id);
                        self.requests.remove(&request_id);
                    }
                    // remove num_requests_pk entry for public_key
                    self.num_requests_pk.remove(&pk);
                    promise.delete_key(pk)
                }
```

**File:** multisig/src/lib.rs (L248-266)
```rust
    pub fn confirm(&mut self, request_id: RequestId) -> PromiseOrValue<bool> {
        self.assert_valid_request(request_id);
        let mut confirmations = self.confirmations.get(&request_id).unwrap();
        assert!(
            !confirmations.contains(&env::signer_account_pk()),
            "Already confirmed this request with this key"
        );
        if confirmations.len() as u32 + 1 >= self.num_confirmations {
            let request = self.remove_request(request_id);
            /********************************
            NOTE: If the tx execution fails for any reason, the request and confirmations are removed already, so the client has to start all over
            ********************************/
            self.execute_request(request)
        } else {
            confirmations.insert(env::signer_account_pk());
            self.confirmations.insert(&request_id, &confirmations);
            PromiseOrValue::Value(true)
        }
    }
```
