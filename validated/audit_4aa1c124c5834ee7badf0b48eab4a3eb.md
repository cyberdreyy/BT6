### Title
Stale confirmations from removed multisig members allow requests to execute below the configured threshold - (File: `multisig2/src/lib.rs`, also `multisig/src/lib.rs`)

### Summary
`delete_member` (multisig2) and the `DeleteKey` action handler (multisig) only purge requests and confirmation records for requests *originated* by the removed member/key. They never scan the `confirmations` map for entries where the removed member merely *confirmed* (but did not create) some other still-open request. A removed member's stale confirmation therefore continues to count toward `num_confirmations` in `confirm()`, letting a request execute with fewer currently-live, authorized members than the policy-configured threshold `K`.

### Finding Description
`confirm()` in `multisig2/src/lib.rs` executes a request once `confirmations.len() as u32 + 1 >= self.num_confirmations`: [1](#0-0) 

`delete_member()` cleans up only requests whose `signer`/creator (`r.member == member`) is the member being removed, and clears `num_requests_pk` for that member — it does **not** search `confirmations` for other requests where the removed member's confirmation is recorded: [2](#0-1) 

The equivalent v1 contract has the same gap in the `DeleteKey` action inside `execute_request`, which filters outstanding requests by `r.signer_pk == pk` (the request creator) but never inspects the `confirmations: UnorderedMap<RequestId, HashSet<PublicKey>>` map for stale confirmations by that key on other requests: [3](#0-2) [4](#0-3) 

The binding that should hold is: `confirmations.len()` at execution time == number of *currently live* members who approved. Once a member is deleted, any confirmation they previously placed on an unrelated, still-pending request remains in the `HashSet`, so `confirmations.len()` at execution time can exceed the number of live members who actually agreed — breaking `confirmations counted == live members who agreed`.

### Impact Explanation
This crosses the "confirmations counted versus live members" custody boundary called out in the rules and maps to the Critical impact category: "a multisig request executed below threshold." Concretely, with `K` confirmations required out of `N` members, a request can be pushed to execution (including a `Transfer` action moving NEAR out of the multisig account) with only `K-1` live, currently-authorized confirmations plus one stale confirmation from a member who has since been removed — i.e., fewer genuinely authorized parties than the contract's own threshold guarantees.

### Likelihood Explanation
This requires no privileged access beyond being (at some point) a legitimate multisig member/key — exactly the "unprivileged attacker" class allowed by the rules (it does not require the foundation, a redeploy, or social engineering). The sequence — confirm a request, then have that member/key removed via a separate, independently-threshold-approved `DeleteMember`/`DeleteKey` request, then have the remaining members supply only `K-1` further confirmations — is a normal operational sequence for any multisig that rotates members, making this reachable in ordinary usage, not just a contrived edge case.

### Recommendation
When removing a member (`delete_member` in `multisig2/src/lib.rs`) or a key (`DeleteKey` in `multisig/src/lib.rs`), iterate over all entries in `confirmations` (not just requests created by that member) and remove the departing member's/key's entry from every confirmation set, so that stale approvals from removed members can never count toward `num_confirmations`.

### Proof of Concept
1. Multisig2 contract initialized with 4 members, `num_confirmations = 3`.
2. Member A creates request X (e.g., `Transfer` of contract funds) and confirms it via `add_request_and_confirm` → `confirmations[X] = {A}` [5](#0-4) .
3. Separately, members B, C, D create and confirm a `DeleteMember{member: A}` request that reaches threshold and executes, calling `delete_member` — this removes A from `members` but leaves `confirmations[X] = {A}` untouched because request X was not created by A [2](#0-1) .
4. Only 2 more live members (e.g., B and C) confirm request X. In `confirm()`, `confirmations.len()` is now `1 (stale A) + 1 (B) = 2`, and on C's confirmation `confirmations.len() as u32 + 1 = 3 >= num_confirmations (3)`, so request X (the `Transfer`) executes [6](#0-5) .
5. Result: the transfer executed with confirmations from only 2 currently-live members (B, C) plus 1 stale, no-longer-authorized confirmation (A), even though the contract's policy requires 3 live confirmations — the request executed below the effective live-member threshold.

### Citations

**File:** multisig2/src/lib.rs (L169-207)
```rust
    /// Add request for multisig.
    pub fn add_request(&mut self, request: MultiSigRequest) -> RequestId {
        let current_member = self.current_member().unwrap_or_else(|| {
            env::panic_str(
                "Predecessor must be a member or transaction signed with key of given account",
            )
        });
        // track how many requests this key has made
        let num_requests = self
            .num_requests_pk
            .get(&current_member.to_string())
            .unwrap_or(0)
            + 1;
        assert(
            num_requests <= self.active_requests_limit,
            "Account has too many active requests. Confirm or delete some.",
        );
        self.num_requests_pk
            .insert(&current_member.to_string(), &num_requests);
        // add the request
        let request_added = MultiSigRequestWithSigner {
            member: current_member,
            added_timestamp: env::block_timestamp(),
            request,
        };
        self.requests.insert(&self.request_nonce, &request_added);
        let confirmations = HashSet::new();
        self.confirmations
            .insert(&self.request_nonce, &confirmations);
        self.request_nonce += 1;
        self.request_nonce - 1
    }

    /// Add request for multisig and confirm with the pk that added.
    pub fn add_request_and_confirm(&mut self, request: MultiSigRequest) -> RequestId {
        let request_id = self.add_request(request);
        self.confirm(request_id);
        request_id
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
