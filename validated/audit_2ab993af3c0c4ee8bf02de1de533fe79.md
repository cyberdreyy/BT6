### Title
Stale confirmations from removed multisig members allow requests to execute below the configured threshold - (File: `multisig2/src/lib.rs`, `multisig/src/lib.rs`)

### Summary
Both multisig implementations (`multisig` and `multisig2`) let a request accumulate confirmations from any current member, but when a member/key is removed, only the requests *created* by that member are purged. Confirmations that the removed member gave on requests created by *other* members are never cleaned up, so they remain counted toward the `num_confirmations` threshold even though that member is no longer part of the multisig.

### Finding Description
In `multisig2/src/lib.rs`, `confirm()` counts distinct confirming identities against `self.num_confirmations` to decide whether to execute a request: [1](#0-0) 

Membership removal is handled by `delete_member()`, which only scrubs requests *authored* by the removed member (`r.member == member`), and clears that member's `num_requests_pk` entry — it never scans other requests' `confirmations` sets to strip the removed member's prior confirmations: [2](#0-1) 

The same pattern exists in the v1 contract: `DeleteKey` inside `execute_request()` only removes requests signed (created) by the deleted key, and `confirm()` only checks whether the confirming key is not already in the confirmations set — it never validates that every entry still in `confirmations` corresponds to a still-active key/member: [3](#0-2) [4](#0-3) 

Because `confirm()` in neither version re-validates that all *existing* entries in the `confirmations` set are still live members before counting `confirmations.len() as u32 + 1 >= self.num_confirmations`, a stale confirmation from a removed member is treated as equivalent to a live one.

This breaks the intended binding: `live confirming members >= num_confirmations` before executing a request. After a member removal, the actual binding that holds is `distinct live confirmers + stale confirmations from removed members >= num_confirmations`, which is weaker than intended.

### Impact Explanation
This is a "multisig request executed below threshold" case, explicitly listed as Critical impact. An attacker (or even benign sequence of events) can cause a `Transfer`, `FunctionCall`, `AddKey`/`AddMember`, or `DeployContract` request to execute with fewer genuinely live approving members than `num_confirmations` requires, because a phantom confirmation from an already-removed member is still counted. For a `Transfer` action this results in NEAR being moved out of the multisig account by a party (or set of parties) not entitled to authorize it under the account's own threshold policy.

### Likelihood Explanation
No privileged actor is required beyond the normal multisig members who are already entitled to add/confirm requests and to remove members — actions available in the contract's public interface (`add_request`, `confirm`, `add_request_and_confirm` for `DeleteMember`/`DeleteKey`). The sequence (create a request, get one confirmation, then remove that confirming member via a separate quorum action, then have any other member add one more confirmation) is a normal, low-complexity operational sequence that does not require exploiting any external dependency, deployment misconfiguration, or victim key.

### Recommendation
In `confirm()` (both `multisig/src/lib.rs` and `multisig2/src/lib.rs`), before comparing `confirmations.len()` against `num_confirmations`, filter the stored confirmation set down to only entries still present in `self.members` (or, in v1, keys the account still trusts), and persist the filtered set. Alternatively, when removing a member (`delete_member` / `DeleteKey`), iterate over *all* outstanding requests' confirmation sets (not just requests the member authored) and strip that member's/key's entry from each, decrementing effective confirmation counts as needed.

### Proof of Concept
1. Deploy `multisig2` with members `{A, B, C}` and `num_confirmations = 2`.
2. `A` calls `add_request` to create request `R` (a `Transfer` to an external account). `confirmations[R] = {}`.
3. `B` calls `confirm(R)`. `confirmations[R] = {B}` (len 1, below threshold 2, request not yet executed).
4. `A` and `C` jointly pass a `DeleteMember { member: B }` request (reaching the 2-confirmation threshold through a separate request). `delete_member(B)` executes: it only deletes requests where `r.member == B` (i.e., requests B *authored*), so request `R` (authored by `A`) is untouched, and `confirmations[R]` still contains `B`. `B` is removed from `self.members`.
5. `A` now calls `confirm(R)`. `confirmations[R].len() + 1 == 2 >= num_confirmations (2)`, so `execute_request(R)` fires and the `Transfer` executes — even though the only currently-live members who approved `R` are `A` (and the removed `B`), i.e., only 1 live member actually approved a 2-of-3 request.

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
