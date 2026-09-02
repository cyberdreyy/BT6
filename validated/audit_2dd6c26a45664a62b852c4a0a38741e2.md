### Title
Multisig confirmations from a deleted key remain counted toward the approval threshold, allowing a request to execute below the effective live-member threshold - (File: multisig/src/lib.rs)

### Summary
`MultiSigContract::confirm()` decides whether a request has enough approvals purely by comparing the size of the `confirmations` `HashSet<PublicKey>` stored for that request against `num_confirmations`. When a member's key is removed via the `DeleteKey` action, the cleanup logic only purges confirmations on requests that the removed key itself *added* (`r.signer_pk == pk`); it never scrubs confirmations that the removed key previously *cast* on other, still-pending requests added by someone else. Those stale confirmations remain in the set and keep counting toward the threshold, so a request can later execute with fewer than `num_confirmations` currently-valid signers.

### Finding Description
`confirm()` computes eligibility from raw confirmation-set size: [1](#0-0) 

The `DeleteKey` handler inside `execute_request` removes confirmations only for requests whose *signer* (adder) is the deleted key, not for requests the deleted key merely *confirmed*: [2](#0-1) 

This is the same class of bug as the reported issue: a getter/threshold check (`getDataByReceipt` in the report; `confirmations.len()` here) is used by a downstream privileged action (claiming liquidation funds; executing a multisig request) without accounting for the fact that an entity it relies on (the receipt owner; a member's key) can be removed by a separate, valid action (`withdraw()`; `DeleteKey`) beforehand, breaking the assumed binding between "recorded state" and "current reality." Here the broken equality is:

`confirmations.len() (recorded) == number of currently valid signers who approved (reality)`

After a `DeleteKey` action executes, this equality no longer holds for any request the deleted key had confirmed before removal but did not add.

### Impact Explanation
This breaks the core multisig authorization threshold guarantee documented in the contract's own README ("`K` signatures ... required to be performed"): a request can be executed using a stale confirmation from a key that no longer has any authority on the account, meaning it can pass with one fewer *currently live* signer than `num_confirmations` mandates. This matches the listed Critical impact: "a multisig request executed below threshold." Any transfer, `FunctionCall`, `AddKey`, or further `DeleteKey`/`SetNumConfirmations` request that was pending confirmation from a key subsequently removed is exploitable this way, potentially moving NEAR out of the account with less-than-required live authorization.

### Likelihood Explanation
This requires no external privilege beyond being one of the existing multisig members and normal usage of documented flows: (1) add a request, (2) get it partially confirmed by a member whose key will later be deleted, (3) execute a separate `DeleteKey` request removing that member (itself a normal, legitimate multisig operation), (4) confirm the original pending request with the remaining live keys. No malicious deployment, foundation involvement, or protocol misuse is required — it can happen even accidentally during routine key rotation, and can be engineered deliberately by any subset of members that can reach `num_confirmations` for the `DeleteKey` action.

### Recommendation
When executing `DeleteKey` (and the analogous `DeleteMember` in `multisig2`), scrub the deleted key/member from the `confirmations` set of every pending request, not only requests it originally added. Alternatively, when tallying confirmations in `confirm()`, filter the stored confirmation set against the current set of live keys/members before comparing against `num_confirmations`.

### Proof of Concept
Using a 2-of-3 multisig with keys A, B, C (mirrors the code path in `multisig/src/lib.rs`):

1. Key A calls `add_request(X)` for a sensitive `Transfer` — `confirmations[X] = {}`.
2. Key B calls `confirm(X)` — `confirmations[X] = {B}` (len 1, `1+1 < 2`, not yet executed).
3. Separately, keys A and C confirm a `DeleteKey{public_key: B}` request Y and it executes (2-of-3 reached). Per `execute_request`'s `DeleteKey` branch, only requests with `signer_pk == B` are purged — request X (added by A) is untouched, so `confirmations[X]` still contains B, even though B's access key has just been deleted from the account.
4. Key C calls `confirm(X)`. `confirmations[X].len() (1, containing only B) + 1 = 2 >= num_confirmations (2)` → request X executes, transferring funds, even though only B's now-revoked signature and C's fresh signature were involved — i.e., the request executed with only one currently-valid signer's real-time approval (C), not two. [3](#0-2) [2](#0-1)

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

**File:** multisig/src/lib.rs (L246-266)
```rust
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
