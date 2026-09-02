### Title
Multisig executes requests below threshold using confirmations from removed members - (File: `multisig2/src/lib.rs`, `multisig/src/lib.rs`)

### Summary
`confirm()` counts confirmations already stored in `self.confirmations` toward `self.num_confirmations` without verifying that every confirming member is still a current member. `delete_member` (multisig2) and the `DeleteKey` handling in `MultiSigRequestAction::DeleteKey` (multisig v1) only purge requests *created by* the departing member; they do not scan and purge that member's *confirmations* recorded on requests created by other members. A confirmation left by a since-removed member therefore still counts toward the threshold, letting a request execute with fewer live-member approvals than `num_confirmations` requires.

### Finding Description
In `multisig2/src/lib.rs`, `delete_member` cleans up state for a departing member like this: [1](#0-0) 

The cleanup loop filters `self.requests` by `r.member == member`, i.e. only requests where the departing member is the *creator* (`MultiSigRequestWithSigner.member`). It removes `num_requests_pk` for that member and deletes their access key, but it never inspects `self.confirmations` entries belonging to *other* requests where the departing member had merely called `confirm()`.

`confirm()` treats the stored confirmation set as authoritative without re-validating current membership of each entry: [2](#0-1) 

So if member `M` confirms request `R` created by someone else, and `M` is later removed via `DeleteMember`, `R`'s confirmation set still contains `M`'s entry. When a subsequent live member confirms `R`, `confirmations.len() + 1 >= num_confirmations` can become true even though only `num_confirmations - 1` *currently authorized* members actually approved it, and `execute_request` runs.

The same defect exists in the legacy `multisig/src/lib.rs`: the `DeleteKey` action only removes requests signed (`r.signer_pk == pk`) by the removed key, not that key's confirmations on other requests: [3](#0-2) 

and `confirm()` there has the identical unconditional counting logic: [4](#0-3) 

### Impact Explanation
This breaks the equality that should hold: *confirmations counted == confirmations from currently authorized members*. A `Transfer`, `AddKey`/`FunctionCall`, or any other `MultiSigRequestAction` can be executed by the multisig account with fewer live signers than the configured `num_confirmations` threshold, i.e. a multisig request executed below threshold — explicitly listed as a Critical impact in this analysis' scope (funds or privileged actions moved by a party set not entitled/authorized to move them under the current member set).

### Likelihood Explanation
No privileged actor, redeploy, or social engineering is required beyond ordinary multisig operation: any deployment that (a) removes a member/key at some point in its lifetime via `DeleteMember`/`DeleteKey` and (b) has outstanding requests that were confirmed but not yet executed by that member before removal is exposed. Membership churn (rotating signers, removing a compromised or departing key) is a normal, expected operational event for these contracts, making the precondition realistic rather than contrived.

### Recommendation
When removing a member (`delete_member` in multisig2, `DeleteKey` handling in multisig v1), iterate all pending requests' confirmation sets (not just requests the member created) and strip the departing member's entry from each. Alternatively, validate membership of every entry in the confirmation set against current `self.members` at the time `confirm()` recomputes the threshold, rather than trusting the stored count.

### Proof of Concept
1. Deploy `multisig2` with members `{A, B, C, D}` and `num_confirmations = 3`.
2. `D` calls `add_request` to create request `R` (e.g. `Transfer` to an attacker-controlled account), then `D` calls `confirm(R)` → `confirmations = {D}`.
3. `C` calls `confirm(R)` → `confirmations = {D, C}` (2/3, below threshold, not yet executed).
4. Separately, `A`, `B`, and `D` create and confirm a `DeleteMember { member: C }` request (3/3 confirmations from live members) → executes `delete_member(C)`. Since `C` did not create `R`, the cleanup filter `r.member == C` does not match `R`, so `R`'s confirmation set `{D, C}` is left untouched even though `C` is no longer a member.
5. `A` calls `confirm(R)` → `confirmations.len() (2) + 1 = 3 >= num_confirmations (3)` → `execute_request(R)` runs the `Transfer`.
6. Result: `R` executed with confirmations from `D`, stale-`C`, and `A` — only 2 of the 3 approvals came from currently authorized members (`A`, `D`), yet the threshold check passed as if 3 live members approved.

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
