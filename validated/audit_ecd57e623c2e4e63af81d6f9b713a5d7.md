## Title
Stale confirmations from a deleted member/key remain counted toward the confirmation threshold, allowing a request to execute below the configured `num_confirmations` - (File: `multisig2/src/lib.rs`, `multisig/src/lib.rs`)

### Summary
Both the `multisig` and `multisig2` contracts implement a K-of-N confirmation scheme where a request executes once `confirmations.len() + 1 >= num_confirmations`. When a key/member is removed, the contract only purges **requests originated by that key/member**, but never scans the `confirmations` map to strip that key/member's confirmation from requests **created by someone else**. A stale confirmation from a since-removed member is therefore still counted toward the threshold, letting a request execute with fewer live confirmations than `num_confirmations` requires.

### Finding Description
In `multisig2/src/lib.rs`, `confirm()` checks the threshold purely against the size of the `confirmations` `HashSet` for the request: [1](#0-0) 

`delete_member()` is the only place that scrubs `requests`/`confirmations`, and it filters by `r.member == member` — i.e., only requests that the removed member itself created: [2](#0-1) 

Any confirmation the removed member cast on a request **created by a different member** is left untouched in the `confirmations` map for that `request_id`. The same pattern exists in the original `multisig/src/lib.rs`, where `DeleteKey` only removes requests whose `signer_pk` equals the deleted key, without cleaning that key's confirmations on other requests: [3](#0-2) [4](#0-3) 

The intended binding is: `confirmations recorded on a request == confirmations from currently-live members`. Because deletion doesn't purge stale entries in `confirmations` for requests it didn't create, this binding breaks: `confirmations recorded > confirmations from currently-live members`, letting later confirms tip the counter to `num_confirmations` using a "confirmation" from an account/key that is no longer a member.

### Impact Explanation
This directly matches the specified Critical impact category "a multisig request executed below threshold." A `Transfer`, `FunctionCall`, `AddKey`, `DeployContract`, etc. request can be executed with strictly fewer *live* confirmations than the contract's configured `num_confirmations`, undermining the K-of-N custody guarantee the contract is supposed to enforce and enabling funds/permissions to move with insufficient real authorization.

### Likelihood Explanation
This requires only ordinary, expected multisig lifecycle events — a request created by one member, confirmed by another member, and that confirming member later removed via a normal `DeleteMember`/`DeleteKey` governance action (routine membership rotation, not any attacker exploit of a bug). No malicious insider collusion beyond normal operation is required; the stale-confirmation accounting bug alone reduces the effective threshold for any request that was partially confirmed before a confirmer's removal.

### Recommendation
When removing a member/key (`delete_member` in `multisig2`, the `DeleteKey` branch in `multisig`), scan all pending requests' confirmation sets (not just those authored by the removed member/key) and remove the deleted member's/key's entry from every `confirmations` entry, e.g.:
```rust
for (request_id, mut confirmations) in self.confirmations.iter() {
    if confirmations.remove(&member.to_string()) {
        self.confirmations.insert(&request_id, &confirmations);
    }
}
```
so that only confirmations from currently-live members count toward `num_confirmations`.

### Proof of Concept
1. Deploy `multisig2` with `num_confirmations = 3` and members `{A, B, C, D}`.
2. `D` calls `add_request` creating request `R` (e.g., a `Transfer`).
3. `B` calls `confirm(R)` → `confirmations = {B}` (len 1).
4. `C` calls `confirm(R)` → `confirmations = {B, C}` (len 2), request not yet executed.
5. Members execute a separate, properly-confirmed `DeleteMember { member: B }` request, removing `B`. Since `R` was created by `D` (not `B`), `delete_member`'s filter `r.member == member` does not match `R`, so `R`'s confirmations set `{B, C}` is left untouched — see [5](#0-4) .
6. `A` calls `confirm(R)`. `confirmations.len() + 1 == 3 >= num_confirmations (3)`, so `execute_request` fires — see [6](#0-5) .
7. Result: `R` executes with only 2 live confirmers (`A`, `C`); `B`'s stale confirmation counted toward the threshold even though `B` is no longer a member — the request executed below the intended live-member threshold.

### Citations

**File:** multisig2/src/lib.rs (L292-315)
```rust
    /// Confirm given request with given signing key.
    /// If with this, there has been enough confirmation, a promise with request will be scheduled.
    pub fn confirm(&mut self, request_id: RequestId) -> PromiseOrValue<bool> {
        self.assert_valid_request(request_id);
        let member = self
            .current_member()
            .unwrap_or_else(|| env::panic_str("Must be validated above"));
        let mut confirmations = self.confirmations.get(&request_id).unwrap();
        assert(
            !confirmations.contains(&member.to_string()),
            "Already confirmed this request with this key",
        );
        if confirmations.len() as u32 + 1 >= self.num_confirmations {
            let request = self.remove_request(request_id);
            /********************************
            NOTE: If the tx execution fails for any reason, the request and confirmations are removed already, so the client has to start all over
            ********************************/
            self.execute_request(request)
        } else {
            confirmations.insert(member.to_string());
            self.confirmations.insert(&request_id, &confirmations);
            PromiseOrValue::Value(true)
        }
    }
```

**File:** multisig2/src/lib.rs (L355-379)
```rust
    /// Delete member from the list. Removes access key if the member is key based.
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
