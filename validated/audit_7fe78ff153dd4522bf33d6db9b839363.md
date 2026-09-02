### Title
Stale confirmations from a revoked multisig key are still counted toward the confirmation threshold, allowing requests to execute below the configured quorum - (File: `multisig/src/lib.rs`)

### Summary
`MultiSigContract::confirm` executes any pending request once the size of its `confirmations` `HashSet<PublicKey>` reaches `num_confirmations` [1](#0-0) . When a key is revoked via the `DeleteKey` action, the contract only purges requests that were *originated* by that key; it never removes that key's confirmations from other, still-pending requests it had confirmed [2](#0-1) . This breaks the intended binding "number of confirmations counted == number of live, currently-authorized members who approved," letting a request execute with fewer real approvals than `num_confirmations` requires.

### Finding Description
`add_request` creates an empty confirmation set for each new request and records only the `signer_pk` of the account that created it [3](#0-2) . Any other holder of a valid access key on the multisig account can call `confirm(request_id)`, which inserts their public key into that request's `confirmations` set until `confirmations.len() + 1 >= num_confirmations`, at which point `execute_request` runs [1](#0-0) .

Key revocation is handled by the `DeleteKey` action inside `execute_request`:

```rust
MultiSigRequestAction::DeleteKey { public_key } => {
    ...
    let request_ids: Vec<u32> = self
        .requests
        .iter()
        .filter(|(_k, r)| r.signer_pk == pk)   // only requests ORIGINATED by pk
        .map(|(k, _r)| k)
        .collect();
    for request_id in request_ids {
        self.confirmations.remove(&request_id);
        self.requests.remove(&request_id);
    }
    self.num_requests_pk.remove(&pk);
    promise.delete_key(pk)
}
``` [2](#0-1) 

This filter only matches requests where `r.signer_pk == pk`, i.e., requests created (with `add_request`) by the key being deleted. It does **not** scan `self.confirmations` for entries where `pk` merely *confirmed* someone else's pending request. Those stale entries remain in the `HashSet<PublicKey>` forever, even after the key is deleted from the account and can no longer sign any transaction.

Because `confirm` only compares set cardinality (not the identity/liveness of each key), a stale confirmation from a now-revoked member is indistinguishable from a live one and still counts toward `num_confirmations`. This is the same class of defect described in the external report: a decision (execute vs. reject/keep-pending) is made using state that no longer reflects reality (deleted key ≈ invalid proposal), and the code path treats it as if nothing were wrong.

### Impact Explanation
This crosses the "confirmations counted versus live members" custody boundary called out in scope. A multisig request (including `Transfer`, `FunctionCall`, `AddKey`, etc.) can be executed with fewer genuinely live, currently-authorized approvals than the configured `num_confirmations` threshold — a multisig request executed below threshold, which is explicitly listed as Critical impact.

### Likelihood Explanation
No special privileges beyond normal multisig membership are required. The bug is triggered by ordinary contract usage: a pending request accumulates partial confirmations, one of the confirming (non-originating) keys is later removed through a routine `DeleteKey` request (e.g., offboarding a member, rotating a compromised key), and the older pending request's stale confirmation silently survives. Any later legitimate confirmer can then push the count to threshold and trigger execution, unaware that one of the "confirmations" belongs to a key that no longer has any authority over the account.

### Recommendation
When processing `DeleteKey`, also scan and prune the removed public key from every entry in `self.confirmations` (not just requests it originated), or alternatively re-validate at `confirm`/`execute_request` time that every public key present in a request's `confirmations` set still corresponds to a currently active access key on the account before counting it toward `num_confirmations`.

### Proof of Concept
1. Deploy `MultiSigContract::new(3)` on account `M` with three full-access keys `A`, `B`, `C` (3 members, `num_confirmations = 3`).
2. Key `A` calls `add_request` to create request `R` (e.g., `Transfer { amount }`) — `R.signer_pk = A`, `confirmations[R] = {}`.
3. Key `B` calls `confirm(R)` → `confirmations[R] = {B}` (size 1, `1+1 < 3`, request stays pending) [1](#0-0) .
4. Members later submit and fully confirm a separate request `D` containing `MultiSigRequestAction::DeleteKey { public_key: B }` to revoke `B` (e.g., because `B` is compromised or leaving). `execute_request` removes only requests where `signer_pk == B` (none, since `B` never originated a request) and calls `promise.delete_key(B)` [2](#0-1) . `R`'s confirmation set `{B}` is left untouched.
5. Only `A` and `C` remain as valid keys on account `M`; `B` can no longer sign anything.
6. Key `C` calls `confirm(R)` → `confirmations[R] = {B, C}`, size 2, `2+1 = 3 >= num_confirmations(3)` → `execute_request(R)` runs and the transfer is sent, even though only two live keys (`B`'s prior stale vote plus `C`) plus at most `A`'s not being needed — i.e., the request executes having received live approval from only `C` (and originator `A` who never explicitly confirmed), one short of the three genuinely live confirmations the threshold was meant to require.

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
