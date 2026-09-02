### Title
Multisig requests can execute below the configured confirmation threshold because deleting a signer key does not purge that key's confirmations from other pending requests - ([File: multisig/src/lib.rs])

### Summary
`MultiSigContract::confirm` in `multisig/src/lib.rs` authorizes execution purely by counting entries in a request's `confirmations` set against `num_confirmations`. When a key is removed via the `DeleteKey` multisig action, the code only purges confirmations from requests that key itself *originated* — not confirmations that key placed on other, still-pending requests. A stale confirmation from a now-deleted key therefore continues to count toward the threshold, allowing a request to execute with fewer *live* authorized confirmers than `num_confirmations` requires.

### Finding Description
`confirm` treats reaching the threshold purely as a count comparison: [1](#0-0) 

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
        self.execute_request(request)
    } else {
        confirmations.insert(env::signer_account_pk());
        self.confirmations.insert(&request_id, &confirmations);
        PromiseOrValue::Value(true)
    }
}
```

The invariant the multisig is supposed to enforce is: `count(confirmations on R) == count(confirmations from keys that are still valid access keys on the account)`. The `DeleteKey` action, however, only cleans confirmations for requests *originated* by the removed key, not confirmations that key contributed to other requests: [2](#0-1) 

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

The filter is `r.signer_pk == pk` — i.e. only requests whose *creator* was the deleted key. Any other pending request `R2` (created by a different key) that the now-deleted key had already confirmed keeps that confirmation entry in `self.confirmations[R2]` forever; nothing ever removes it. `multisig2/src/lib.rs`'s `delete_member` has the identical gap, filtering on `r.member == member` (the request originator) rather than scanning/pruning confirmations left by that member on other requests: [3](#0-2) 

Because the whitelist/authorization primitive here is "confirmations from currently valid members," and the code silently keeps counting confirmations from a member/key that has since been removed, `confirm` can reach `num_confirmations` while one of the contributing confirmations came from an account/key that is no longer trusted — the exact "confirmations counted versus live members" custody-binding violation.

### Impact Explanation
This lets a multisig request (e.g. a `Transfer`, `AddKey`, or `DeployContract` action) execute with effectively fewer live, currently-authorized confirmers than the configured `num_confirmations` — a multisig request executed below threshold, moving NEAR or granting access with weaker authorization than the account owner configured. This falls under the Critical impact category "a multisig request executed below threshold."

### Likelihood Explanation
This requires realistic multisig lifecycle events that are explicitly supported by the contract's own API: a member confirms a pending request, and later that member's key is removed via a normal `DeleteKey`/`DeleteMember` governance action (e.g., because the key holder left, was compromised, or rotated keys) while the other request they confirmed is still outstanding. The remaining members are never warned that a stale confirmation exists, and nothing in `confirm`, `get_confirmations`, or `assert_valid_request` re-validates that each recorded confirming key/member is still part of the current member set. No special privileges beyond normal multisig operation are needed to trigger it — only ordinary key-rotation practice combined with a not-yet-executed request.

### Recommendation
When removing a key/member (`DeleteKey`/`DeleteMember`), iterate over *all* pending requests' confirmation sets (not just those the removed key originated) and strip the removed key/member from each. Alternatively, at `confirm`-time, filter `confirmations` to only those entries whose key/member is still present in the current member set before comparing the count to `num_confirmations`.

### Proof of Concept
Given a multisig with `num_confirmations = 3` and members A, B, C, D:
1. A calls `add_request_and_confirm(R)` targeting a `Transfer` — `R.signer_pk = A`, `confirmations(R) = {A}`.
2. B calls `confirm(R)` — `confirmations(R) = {A, B}` (2/3, not yet executed).
3. Separately, C submits and gets executed a request `X` (signer_pk = C) containing `DeleteKey { public_key: B }`, removing B as an authorized key. Per `execute_request`'s `DeleteKey` handling, only requests where `signer_pk == B` are cleaned up; `R` (signer_pk = A) is untouched, so `confirmations(R)` still contains B.
4. D calls `confirm(R)` — `confirmations(R).len() + 1 = 3 >= num_confirmations`, so `R` executes.

`R` executed with 3 counted confirmations (A, B, D), but B's key was deleted before step 4 — only 2 currently-live keys (A and D) actually authorized the transfer, one short of the configured 3-of-4 threshold.

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

**File:** multisig2/src/lib.rs (L356-379)
```rust
    fn delete_member(&mut self, promise: Promise, member: MultisigMember) -> Promise {
        assert(
            self.members.len() - 1 >= self.num_confirmations as u64,
            "Removing given member will make total number of members below number of confirmations",
        );
        // delete outstanding requests by public_key
        let request_ids: Vec<u32> = self
            .requests
            .iter()
            .filter_map(|(k, r)| if r.member == member { Some(k) } else { None })
            .collect();
        for request_id in request_ids {
            // remove confirmations for this request
            self.confirmations.remove(&request_id);
            self.requests.remove(&request_id);
        }
        // remove num_requests_pk entry for member
        self.num_requests_pk.remove(&member.to_string());
        self.members.remove(&member);
        match member {
            MultisigMember::AccessKey { public_key } => promise.delete_key(public_key.into()),
            MultisigMember::Account { account_id: _ } => promise,
        }
    }
```
