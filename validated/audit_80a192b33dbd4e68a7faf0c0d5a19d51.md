### Title
Stale confirmations from removed multisig members allow requests to execute below the live-member confirmation threshold - (File: multisig2/src/lib.rs)

### Summary
`MultiSigContract::delete_member` only purges requests *created* by the removed member; it never scrubs that member's entries from the `confirmations` sets of requests created by *other* members. Because `confirm()` counts raw confirmation-set size against `num_confirmations` without checking that each confirming identity is still in `members`, a stale confirmation left behind by a removed member is counted as if it were a live approval, letting a request execute with fewer than `num_confirmations` currently-authorized signers.

### Finding Description
`confirm()` accepts a request as approved purely by set cardinality: `confirmations.len() as u32 + 1 >= self.num_confirmations` then calls `execute_request`, with no re-validation that every string in `confirmations` still corresponds to a member in `self.members`. [1](#0-0) 

`delete_member` removes the departing member from `self.members` and from `num_requests_pk`, and deletes only the requests where `r.member == member` (i.e., requests *that member created*). It does not iterate over `self.confirmations` to strip the removed member's identity from confirmation sets belonging to requests created by other members: [2](#0-1) 

The contract state: [3](#0-2) 

Binding broken: `confirmations_counted(request) == live_signers_who_approved(request)`. After a member removal, `confirmations_counted(request) > live_signers_who_approved(request)` for any pre-existing request that the removed member had confirmed but not created, since the stale entry survives.

### Impact Explanation
This lets a `MultiSigRequest` (including `Transfer`, `FunctionCall`, `AddKey`/`AddMember`/`DeleteMember`, `DeployContract`) execute while the number of *currently valid* signers who approved is one (or more) below `num_confirmations`. That is exactly "a multisig request executed below threshold" — funds can move, keys can be added, or contracts can be upgraded with an effective quorum weaker than the contract's declared security policy, undermining the custody guarantee the multisig is supposed to provide.

### Likelihood Explanation
This requires no attacker privilege beyond normal multisig lifecycle usage: (1) member A confirms request R created by member B (a routine action), (2) member A is later removed via a legitimate `DeleteMember` request (routine key rotation/offboarding), (3) any subsequent confirmer pushes the count to threshold, and `confirm()` executes R with A's stale confirmation counted. No malicious insider collusion beyond ordinary use is required — member rotation is expected operational behavior, making this readily reachable.

### Recommendation
When executing `DeleteMember`, iterate all entries in `self.confirmations` and remove the departing member's string from every confirmation set (not just from requests they authored). Alternatively, validate membership of every entry in a confirmation set at `confirm()` time before counting it toward `num_confirmations`, discarding stale/removed-member entries.

### Proof of Concept
1. Deploy `MultiSigContract::new([A, B, C, D], 3)`. [4](#0-3) 
2. Member B calls `add_request` to create request `R` transferring funds to an arbitrary receiver.
3. Member A calls `confirm(R)` → `confirmations[R] = {A}`.
4. Member C calls `confirm(R)` → `confirmations[R] = {A, C}` (still below threshold 3).
5. A separate, properly-confirmed `DeleteMember { member: A }` request executes, removing A from `self.members`, but `confirmations[R]` remains `{A, C}` because `delete_member` only cleans requests authored by A. [5](#0-4) 
6. Member D calls `confirm(R)` → `confirmations.len() + 1 == 3 >= num_confirmations(3)` → `execute_request(R)` runs and the transfer executes, even though only C and D (2 live members) actually approved it — one below the required 3-of-4 threshold. [6](#0-5)

### Citations

**File:** multisig2/src/lib.rs (L116-133)
```rust
#[near_bindgen]
#[derive(BorshDeserialize, BorshSerialize, PanicOnDefault)]
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

**File:** multisig2/src/lib.rs (L147-167)
```rust
    #[init]
    pub fn new(members: Vec<MultisigMember>, num_confirmations: u32) -> Self {
        assert(
            members.len() >= num_confirmations as usize,
            "Members list must be equal or larger than number of confirmations",
        );
        let mut multisig = Self {
            members: UnorderedSet::new(StorageKeys::Members),
            num_confirmations,
            request_nonce: 0,
            requests: UnorderedMap::new(StorageKeys::Requests),
            confirmations: LookupMap::new(StorageKeys::Confirmations),
            num_requests_pk: LookupMap::new(StorageKeys::NumRequestsPk),
            active_requests_limit: ACTIVE_REQUESTS_LIMIT,
        };
        let mut promise = Promise::new(env::current_account_id());
        for member in members {
            promise = multisig.add_member(promise, member);
        }
        multisig
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
