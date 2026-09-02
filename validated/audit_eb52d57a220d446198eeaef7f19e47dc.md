### Title
Confirmations from removed/deleted multisig members remain counted toward the approval threshold on other pending requests, allowing a request to execute with fewer live-member approvals than `num_confirmations` - (File: `multisig2/src/lib.rs`, also `multisig/src/lib.rs`)

### Summary
Both the account-based multisig (`multisig2/src/lib.rs`) and the key-based multisig (`multisig/src/lib.rs`) let members `confirm` a pending request, storing the confirming member's identity in a `confirmations: HashSet` keyed by `request_id`. When a member is later removed (`DeleteMember`/`DeleteKey`), the cleanup logic only purges **requests created by** that member, not the **confirmations that member cast on other members' requests**. This breaks the intended custody binding: `confirmations counted == confirmations from currently-live members`.

### Finding Description
In `multisig2/src/lib.rs`, `confirm()` checks the size of the stored `confirmations` set against `self.num_confirmations` to decide whether to execute a request: [1](#0-0) 

When a member is deleted via `MultiSigRequestAction::DeleteMember`, `delete_member()` only scrubs requests where `r.member == member` (i.e. requests *created* by the removed member) and removes that member from `self.num_requests_pk` and `self.members`: [2](#0-1) 

It does **not** scan `self.confirmations` to strip entries belonging to the removed member on requests created by *other* members. Since `confirmations` is a `HashSet<String>` storing serialized member identities, and `confirm()` never re-validates that every entry in that set still belongs to `self.members`, a stale confirmation from a now-removed member permanently inflates the confirmation count of any request it was cast on prior to removal.

The exact same pattern exists in the legacy key-based contract, `multisig/src/lib.rs`, where `DeleteKey` execution only removes requests signed (created) by the deleted public key, not confirmations that key placed on other requests: [3](#0-2) 
and `confirm()` there uses the same unfiltered `confirmations.len() + 1 >= self.num_confirmations` check: [4](#0-3) 

**Binding broken:** the contract's safety invariant is "a request executes only when `num_confirmations` *live* members approved it." What is actually enforced is "a request executes when `num_confirmations` *entries* exist in its confirmation set," where those entries are never revalidated against current membership: `|confirmations ∩ live_members| < num_confirmations` yet the request still executes because `|confirmations| >= num_confirmations`.

### Impact Explanation
This is a **Critical** authorization-bypass: a `Transfer`, `FunctionCall`, `AddKey`, or `DeployContract` request can be executed by the multisig with strictly fewer live-member approvals than the configured threshold, because a stale confirmation from an already-removed member is still counted. In the extreme, if enough confirmations from since-removed members accumulate across pending requests, the effective live-member threshold required to move funds or change contract code drops below the number of members who actually consented at execution time — directly weakening the "funds moved by a party entitled to it" guarantee the multisig is meant to provide.

### Likelihood Explanation
Reaching this requires no privileged capability beyond being (or having been) a legitimate multisig member — a normal, expected role in this contract's flow, not an out-of-scope "malicious multisig member" attack, since the flaw is triggered by the *governance-approved* removal of a member combined with routine `confirm()` calls on unrelated pending requests. Any workflow where members regularly have several requests pending concurrently (explicitly supported via `active_requests_limit`, defaulting to 12 per member) and where membership changes over time (an expected, documented operation via `DeleteMember`/`DeleteKey`) will trigger this drift. No malicious node, RPC interception, or key compromise is needed — only normal use of `confirm`, `add_request`, and `DeleteMember`/`DeleteKey`.

### Recommendation
When removing a member (`delete_member` in `multisig2/src/lib.rs`, `DeleteKey` handling in `multisig/src/lib.rs`), iterate all entries in `self.confirmations` and remove the deleted member/key's entry from every request's confirmation set, not just the requests that member created. Alternatively, revalidate every confirming identity against `self.members` (or the key registry) at the moment `confirm()` computes `confirmations.len()`, discarding stale entries before comparing against `num_confirmations`.

### Proof of Concept
1. Deploy `multisig2` with 4 members `{A, B, C, D}` and `num_confirmations = 3`.
2. `A` creates request `R1` (e.g. `Transfer` of contract funds) via `add_request`.
3. `D` calls `confirm(R1)` → `confirmations(R1) = {D}` (per `multisig2/src/lib.rs` lines 292-315).
4. Separately, the group agrees to remove `D` (e.g. key compromise suspicion) and executes a `DeleteMember { member: D }` request with 3 confirmations from `A, B, C`. `delete_member()` only removes requests *created by* `D`; `R1` (created by `A`) is untouched, so `confirmations(R1)` still contains `D` (lines 355-379).
5. `B` calls `confirm(R1)` → `confirmations(R1) = {D, B}`, size 2, still below threshold.
6. `C` calls `confirm(R1)` → size becomes 3 (`D, B, C`) ≥ `num_confirmations = 3` → `execute_request` fires the `Transfer` (lines 304-309).
7. Result: `R1` executed with only 2 live members (`B`, `C`) actually approving at execution time, plus one stale confirmation from the already-removed `D` — one short of the intended 3-live-member threshold, violating the multisig's core authorization guarantee.

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
