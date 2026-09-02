### Title
Stale confirmations from deleted multisig keys remain counted toward the confirmation threshold, allowing a request to execute below the intended threshold - (File: multisig/src/lib.rs)

### Summary
`MultiSigContract::confirm` executes a request once `confirmations.len() + 1 >= self.num_confirmations` [1](#0-0) , but the `DeleteKey` action only purges requests *created* by the removed key — it does not remove that key's confirmations recorded on other pending requests it merely *confirmed* [2](#0-1) . A confirmation cast by a key that is later revoked therefore keeps counting toward the threshold forever, breaking the binding "confirmations counted == live members."

### Finding Description
`execute_request`'s `DeleteKey` handler filters `self.requests` for entries whose `signer_pk == pk` (the key being deleted) and removes only those requests (and their confirmation sets) [3](#0-2) . It never scans `self.confirmations` for *other* requests (created by a different signer) that the to-be-deleted key had already confirmed. After the key is deleted from the account via `promise.delete_key(pk)` [4](#0-3) , that key can no longer sign anything, yet its already-cast vote remains stored in `self.confirmations` for any request it confirmed but did not itself create.

When `confirm` is later called by a still-valid key, the check `confirmations.len() as u32 + 1 >= self.num_confirmations` [5](#0-4)  treats the stale, now-unauthorized confirmation as if it came from a live member, so the request can be pushed over threshold with fewer genuinely live signers than `num_confirmations` requires.

### Impact Explanation
This is a Critical-class issue per the scope rules ("a multisig request executed below threshold"): the number of confirmations recorded diverges from the number of currently-authorized (live) signers, letting a request execute with effectively fewer independent, currently-trusted approvals than the configured k-of-n policy demands. Any action type (`Transfer`, `AddKey`, `FunctionCall`, etc.) can be pushed through this way once enough stale confirmations accumulate against a smaller live set.

### Likelihood Explanation
The trigger sequence uses only ordinary, expected multisig operations: (1) any member confirms a request created by a different member, (2) the group later revokes that member's key via a normal `DeleteKey` request (e.g., offboarding, key rotation, or reacting to a suspected compromise). No malicious behavior by a currently-authorized member is required at exploitation time — the flaw is purely in bookkeeping during key removal, so it will surface in normal operational lifecycles of any long-lived multisig deployment.

### Recommendation
When executing `DeleteKey`, iterate over all entries in `self.confirmations` (not just requests created by the removed key) and remove the deleted `public_key` from every confirmation set; re-evaluate whether any request should be considered "already satisfied" only against currently valid keys. Alternatively, maintain a `valid_keys` set and have `confirm`/threshold checks count only confirmations whose signer key is still present in that set.

### Proof of Concept
1. `num_confirmations = 2`, members A, B, C (full-access keys on the multisig account).
2. B calls `add_request` (not confirm) to create request `R` (e.g., `Transfer { amount }`) — `confirmations[R] = {}` [6](#0-5) .
3. A calls `confirm(R)` → `confirmations[R] = {A}` (1 of 2, not yet executed) [1](#0-0) .
4. B and C submit and confirm a `DeleteKey { public_key: A }` request (2-of-2 reached, legitimate revocation of A). This deletes A's access key but `R`'s `confirmations` set still equals `{A}` because `R.signer_pk == B`, not `A`, so it is not swept by the `DeleteKey` handler's filter [3](#0-2) .
5. C (the only other live signer) calls `confirm(R)`. Check: `confirmations.len() (1) + 1 >= num_confirmations (2)` → true → `R` executes, transferring funds, even though only **one** currently-live key (C) ever approved it after A's revocation — the intended 2-of-3 (now 2-of-2 live) guarantee is broken.

### Citations

**File:** multisig/src/lib.rs (L116-146)
```rust
    pub fn add_request(&mut self, request: MultiSigRequest) -> RequestId {
        assert_eq!(
            env::current_account_id(),
            env::predecessor_account_id(),
            "Predecessor account must much current account"
        );
        // track how many requests this key has made
        let num_requests = self
            .num_requests_pk
            .get(&env::signer_account_pk())
            .unwrap_or(0)
            + 1;
        assert!(
            num_requests <= self.active_requests_limit,
            "Account has too many active requests. Confirm or delete some."
        );
        self.num_requests_pk
            .insert(&env::signer_account_pk(), &num_requests);
        // add the request
        let request_added = MultiSigRequestWithSigner {
            signer_pk: env::signer_account_pk(),
            added_timestamp: env::block_timestamp(),
            request: request,
        };
        self.requests.insert(&self.request_nonce, &request_added);
        let confirmations = HashSet::new();
        self.confirmations
            .insert(&self.request_nonce, &confirmations);
        self.request_nonce += 1;
        self.request_nonce - 1
    }
```

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
