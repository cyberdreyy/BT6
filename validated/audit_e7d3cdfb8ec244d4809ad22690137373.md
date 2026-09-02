### Title
Stale confirmations from removed multisig members are still counted toward the confirmation threshold - ([File: multisig2/src/lib.rs])

### Summary
`multisig2`'s `delete_member` only purges confirmations for requests that the departing member *created*, not confirmations that member cast on other members' requests. Because `confirm()` blindly counts every string stored in the `confirmations` set for a request (regardless of whether that member is still in `self.members`), a request can reach `num_confirmations` using a "ghost" vote from an account/key that has already been removed from the multisig. This lets a request execute (including a `Transfer`) with fewer genuinely live approving members than the configured threshold requires.

### Finding Description
The intended invariant is:

```
confirmations counted for a request == number of currently active, distinct members who approved that specific request
```

`confirm()` only checks that the *caller* is currently a member via `current_member()` [1](#0-0) , then compares `confirmations.len() + 1` against `self.num_confirmations` without re-validating that every *previously recorded* entry in `confirmations` still belongs to a live member.

`delete_member` is the only place that prunes stale confirmations, and it prunes far too little: [2](#0-1) 

It removes requests (and their confirmation sets) only where `r.member == member`, i.e., requests that the departing member *originally created* — not requests created by someone else that the departing member merely *confirmed*. The member's string identity is then removed from `self.members`, but any confirmation entry the member left behind in `self.confirmations` for a request they didn't create is never deleted.

Since `self.confirmations` is a raw `LookupMap<RequestId, HashSet<String>>` [3](#0-2) , that stale string entry persists and continues to count toward the threshold check in `confirm()`.

This is the direct NEAR/Rust analog of the reported Solidity bug class: an unvalidated piece of trusted state (there, an unset contract address defaulting to `address(0)` and silently succeeding; here, a removed member's stale confirmation silently continuing to count) is used without checking that it is still valid/current, breaking a security-critical binding — in this case, "confirmations counted" versus "live members who confirmed."

### Impact Explanation
This crosses the threshold/authorisation boundary explicitly called out as Critical: "a multisig request executed below threshold." A `Transfer`, `FunctionCall`, `AddKey`, or `DeployContract` request can be executed with fewer distinct, currently-authorized approvals than `num_confirmations` mandates, because one of the counted votes belongs to an account/key that is no longer a member (e.g., removed for being compromised, off-boarded, or malicious). This directly threatens custody of funds held by the multisig account.

### Likelihood Explanation
This requires no privileged access beyond being (or having been) a normal multisig member/key at some point — exactly the kind of scenario multisig systems are designed to survive (key rotation, off-boarding a member, removing a suspected-compromised key). Any sequence where:
1. a member confirms a request they did not create, and
2. that member is later removed via `DeleteMember`

leaves a live exploitable ghost-vote on that pending request. This is a completely realistic operational sequence, not a contrived edge case, making the likelihood high in any multisig doing member rotation with in-flight requests.

### Recommendation
When deleting a member, also strip that member's confirmation entry from every request's confirmation set (not only requests they created), e.g. iterate `self.confirmations` for all `RequestId`s and remove `member.to_string()` from each `HashSet`. Alternatively/defensively, `confirm()` should recompute the count by filtering `confirmations` against `self.members` before comparing to `num_confirmations`, so stale entries can never contribute to reaching the threshold.

### Proof of Concept
1. Deploy `multisig2` with `members = [A, B, C, D]`, `num_confirmations = 3`.
2. `A.add_request(R)` — a `Transfer` request to an attacker-controlled account, created by `A`.
3. `D.confirm(R)` — confirmations for `R` = `{A? no—D}` (assuming `A` didn't auto-confirm; adjust so `confirmations(R) = {D}`, 1 of 3).
4. Multisig separately reaches `num_confirmations` (3) to execute `DeleteMember{D}` (D is being off-boarded/suspected compromised). `delete_member` runs: it only deletes requests where `r.member == D` (i.e., requests *created* by D) — `R` was created by `A`, so it is untouched; `D`'s confirmation entry in `confirmations(R) = {D}` is never removed. `self.members` now = `{A, B, C}` (3 members), `num_confirmations` still 3.
5. `B.confirm(R)` — code computes `confirmations.len() (=1, "D") + 1 = 2 >= 3`? No — need one more genuine confirmation. `C.confirm(R)` — now `confirmations.len() = 2 ("D","B") + 1 = 3 >= 3` → `execute_request(R)` fires, transferring funds.
6. Result: `R` executed with confirmations from `D` (no longer a member) and `B`, `C` — i.e., only 2 currently-live members (`B`, `C`) genuinely approved, yet the request executed as if 3-of-3 (or the configured K-of-N) approval was met, breaking the multisig's authorisation guarantee. [2](#0-1) [1](#0-0)

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
