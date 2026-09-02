### Title
Stale confirmations from removed multisig members are still counted toward the confirmation threshold, allowing a request to execute below the required live-member threshold - (File: `multisig2/src/lib.rs`, `multisig/src/lib.rs`)

### Summary
When a multisig member is removed via `DeleteMember` (in `multisig2`) or `DeleteKey` (in `multisig`), the contract only purges the *requests originally created by* that member. It does not purge that member's *confirmations recorded on other members' outstanding requests*. Because `confirm()` simply compares the size of the stored `confirmations` set against `num_confirmations` without checking that every entry in that set still belongs to a current, live member, a stale confirmation from a since-removed member can still push a request over the execution threshold. This is the same root-cause pattern as the reported `isLpCreated` bug: a state/flag (`confirmations` recorded per request) is set once and never re-validated against the entity's current standing (`self.members`) before being trusted at a later, consequential decision point (`confirm`'s threshold check that triggers fund transfer / key changes).

### Finding Description
`confirm()` in `multisig2/src/lib.rs` decides whether to execute a request purely from the cardinality of the stored `confirmations` set: [1](#0-0) 

The set is a `HashSet<String>` of member identifiers collected over time (`confirmations.insert(member.to_string())`), persisted independently of the current `self.members` set: [2](#0-1) 

When a member is removed, `delete_member` only clears requests whose *creator* (`r.member`) equals the removed member, and removes that member's own request-count bookkeeping. It never scans other requests' `confirmations` sets to strip an entry belonging to the removed member: [3](#0-2) 

The only safeguard on `delete_member` is that it can't shrink the membership below `num_confirmations`: [4](#0-3) 

but this only bounds the *count* of members, not whether previously recorded confirmations still correspond to *live* members. So a confirmation recorded by member `A` on request `R` (created by member `B`) survives `A`'s removal from the multisig and is still counted by `confirm()`'s `confirmations.len() as u32 + 1 >= self.num_confirmations` check.

The same pattern exists in the legacy `multisig/src/lib.rs` contract, where `DeleteKey` only removes requests created by the deleted public key, not confirmations left by that key on other requests: [5](#0-4) [6](#0-5) 

**Binding broken**: `confirmations counted == live members who confirmed`. In reality, after a member is removed, `confirmations counted > live members who confirmed`, so a request can execute with fewer genuinely live approvals than `num_confirmations` requires.

### Impact Explanation
This matches the "Critical" impact class explicitly listed as in-scope: *a multisig request executed below threshold*. A `Transfer`, `AddKey`, `DeployContract`, or `FunctionCall` action can be executed against the multisig's own funds/account with fewer live signers than the configured `num_confirmations`, effectively lowering the security threshold of the wallet without the remaining members' knowledge or consent.

### Likelihood Explanation
This requires only ordinary usage of the contract's documented feature set (no owner/foundation compromise, no redeploy, no key theft):
1. A pending request `R` created by member `B` receives a confirmation from member `A` (a normal step in multisig usage where a request doesn't reach threshold immediately).
2. Member `A` is later removed from the multisig through the normal `DeleteMember`/`DeleteKey` governance flow (e.g., team member offboarding, key rotation) - a routine and expected operational event, not an attack.
3. `R`'s stale confirmation from `A` remains and can later combine with fewer live confirmations to reach `num_confirmations`, letting `R` execute with less real approval than intended.
No malicious insider action, redeploy, or foundation-level trust is needed — only the ordinary combination of "confirm before removal" + "member removal" + "confirm again," which is a foreseeable operational sequence for any long-lived multisig wallet.

### Recommendation
When removing a member (`delete_member` / `DeleteKey` handling), iterate over all outstanding requests' `confirmations` sets and strip any entry matching the removed member/key, not just requests the removed member originated. Alternatively, validate at `confirm()` time (or at execution time) that every identifier in the `confirmations` set still belongs to `self.members` before counting it toward `num_confirmations`.

### Proof of Concept
1. Initialize `multisig2` contract with 4 members `A, B, C, D` and `num_confirmations = 3`.
2. Member `B` calls `add_request` to create request `R` (e.g., `Transfer` of contract funds to attacker's account).
3. Member `A` calls `confirm(R)` → `confirmations = {A}` (1 of 3 needed).
4. Through normal governance, a `DeleteMember { member: A }` request is created and approved by 3 members (`B, C, D`) and executes — this passes the `members.len() - 1 >= num_confirmations` check (`4-1=3 >= 3`). `A` is removed; `R`'s `confirmations` set is left untouched and still contains `A`. [7](#0-6) 
5. Member `C` calls `confirm(R)` → `confirmations.len() (1, still containing stale A) + 1 = 2` — this is still below 3, so with just one more genuinely live confirmer (e.g. `D`) the request executes counting `A`'s stale confirmation as valid, i.e. `R` executes with only 2 truly live approvals (`C`, `D`) despite `num_confirmations = 3`. [8](#0-7) 

This demonstrates a request executing below the configured live-member confirmation threshold.

### Citations

**File:** multisig2/src/lib.rs (L118-133)
```rust
pub struct MultiSigContract {
    /// Members of the multisig.
    members: UnorderedSet<MultisigMember>,
    /// Number of confirmations required.
    num_confirmations: u32,
    /// Latest request nonce.
    request_nonce: RequestId,
    /// All active requests.
    requests: UnorderedMap<RequestId, MultiSigRequestWithSigner>,
    /// All confirmations for active requests.
    confirmations: LookupMap<RequestId, HashSet<String>>,
    /// Number of requests per member.
    num_requests_pk: LookupMap<String, u32>,
    /// Limit number of active requests per member.
    active_requests_limit: u32,
}
```

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
