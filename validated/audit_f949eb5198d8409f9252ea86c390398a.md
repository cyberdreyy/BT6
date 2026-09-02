### Title
Confirmations from a revoked signing key remain valid toward quorum, allowing a multisig request to execute below the current live-key threshold - (File: `multisig/src/lib.rs`)

### Summary
The `confirm` function in `MultiSigContract` counts confirmations recorded in the `confirmations: UnorderedMap<RequestId, HashSet<PublicKey>>` map against the fixed `num_confirmations` threshold. When a key is revoked via a `DeleteKey` request, the contract only purges requests and confirmations that were *originally added* (as signer) by the deleted key; it does not purge confirmations that key contributed to *other* still-open requests as a co-signer. As a result, a request can execute using a confirmation from a key that is no longer authorized, effectively executing a multisig action below the intended live-signer threshold.

### Finding Description
`execute_request`'s `DeleteKey` branch only cleans up requests/confirmations where the request's original creator (`signer_pk`) equals the deleted key: [1](#0-0) 

It does not scan `self.confirmations` for entries where the deleted key appears as a *co-signer* confirmation on requests created by other keys. Meanwhile, `confirm` blindly trusts the stored `confirmations` set size against `num_confirmations`: [2](#0-1) 

The binding that should hold is:
`confirmations counted toward quorum == confirmations from currently-authorized (live) keys`

But after a `DeleteKey` execution, a stale confirmation from the now-revoked key can still be present in `self.confirmations` for any request that key confirmed (but did not create), breaking this equality: `confirmations counted > confirmations from live keys`.

Concretely:
1. Keys K1, K2, K3 exist, `num_confirmations = 3`.
2. K1 creates and confirms Request A (a `Transfer`) — `confirmations[A] = {K1}`.
3. K2 confirms Request A — `confirmations[A] = {K1, K2}` (2/3, not yet executed).
4. Separately, K1 creates a `DeleteKey{K2}` request (Request B) and it reaches quorum (3/3, using K1, K2, K3 — K2 can even confirm its own removal, or simply the remaining 3 keys agree K2 should be removed). Request B executes: K2's access key is deleted from the account, `num_requests_pk` for K2 is cleared. Note the `DeleteKey` cleanup only removes requests *created by* K2 — Request A was created by K1, so it is untouched.
5. K3 now confirms Request A — `confirmations[A].len() == 2 + 1 == 3 >= num_confirmations`, so Request A (the `Transfer`) executes, counting K2's stale confirmation even though K2 is no longer a valid signer on the account.

This directly matches the required custody binding class: "confirmations counted versus live members." The confirmation ledger diverges from the actual live-key set, and a request executes with fewer than the intended number of currently-authorized approvals.

### Impact Explanation
This crosses the "multisig request executed below threshold" Critical-impact boundary explicitly listed in scope. A `Transfer`, `FunctionCall`, `AddKey`, or `DeployContract` request can be pushed to execution using a mix of live and revoked-key confirmations, meaning the actual number of currently-trusted approvers backing the executed action is less than `num_confirmations`. This can move NEAR funds or grant access in a way not actually authorized by the current key set, e.g. a departing/compromised key holder's earlier confirmation on a pending transfer remains "live" for quorum purposes even after that key is explicitly revoked.

### Likelihood Explanation
This does not require any privileged foundation/owner role beyond being one of the multisig's own signing keys (i.e., a normal, unprivileged-relative-to-the-contract signer within the K-of-N scheme). It only requires: (a) a pending, under-threshold request confirmed by a key, and (b) that key later being removed via `DeleteKey` while the earlier request remains open. Both are ordinary, expected multisig operations (key rotation while other requests are in flight) — no malicious deployment, redeploy, or social engineering is needed. The contract's own README documents a related-but-different gotcha (removing keys can lock the contract) but does not address or mitigate this stale-confirmation issue, confirming it is not accounted for by design.

### Recommendation
When executing `DeleteKey`, iterate over **all** entries in `self.confirmations` (not just requests whose `signer_pk` matches the deleted key) and remove the deleted public key from every confirmation set. Alternatively, when counting confirmations in `confirm`/`execute_request`, filter the confirmations set to currently valid keys (e.g., by checking against the account's access keys or an explicit tracked "authorized keys" set) before comparing against `num_confirmations`, rather than trusting the raw historical confirmation set.

### Proof of Concept
Given the code above, a Rust unit test analogous to the existing `test_multi_3_of_n` test demonstrates this:
1. `MultiSigContract::new(3)` with keys K1, K2, K3.
2. K1: `add_request_and_confirm(TransferRequest_A)` → `confirmations[A] = {K1}`.
3. K2: `confirm(A)` → `confirmations[A] = {K1, K2}` (2/3).
4. K1: `add_request_and_confirm(DeleteKey{K2})` (as a separate self-request to `alice()`), and get remaining keys to `confirm` until it executes, deleting K2's access key on-chain but leaving `confirmations[A]` untouched because Request A's `signer_pk == K1 != K2`.
5. K3: `confirm(A)` → `confirmations[A].len() + 1 == 3 == num_confirmations`, executing the Transfer using K2's stale confirmation even though K2 has been removed.

This reproduces the "confirmations counted versus live members" mismatch and results in a multisig action executing below the currently-authorized threshold. [3](#0-2)

### Citations

**File:** multisig/src/lib.rs (L198-266)
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
                MultiSigRequestAction::FunctionCall {
                    method_name,
                    args,
                    deposit,
                    gas,
                } => promise.function_call(
                    method_name.into_bytes(),
                    args.into(),
                    deposit.into(),
                    gas.into(),
                ),
                // the following methods must be a single action
                MultiSigRequestAction::SetNumConfirmations { num_confirmations } => {
                    self.assert_one_action_only(receiver_id, num_actions);
                    self.num_confirmations = num_confirmations;
                    return PromiseOrValue::Value(true);
                }
                MultiSigRequestAction::SetActiveRequestsLimit {
                    active_requests_limit,
                } => {
                    self.assert_one_action_only(receiver_id, num_actions);
                    self.active_requests_limit = active_requests_limit;
                    return PromiseOrValue::Value(true);
                }
            };
        }
        promise.into()
    }

    /// Confirm given request with given signing key.
    /// If with this, there has been enough confirmation, a promise with request will be scheduled.
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
