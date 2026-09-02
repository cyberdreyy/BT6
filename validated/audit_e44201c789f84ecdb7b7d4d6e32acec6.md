This is a valid analog. It maps the "oracle result flagged/changed after being counted as final" bug class onto the NEAR multisig contracts, where a **removed member's confirmation is still counted toward the execution threshold**, breaking the binding *confirmations counted == live members' confirmations*.

### Title
Stale confirmations from removed multisig members are still counted toward execution threshold - (File: `multisig2/src/lib.rs`, `multisig/src/lib.rs`)

### Summary
When a member is removed from the multisig via `DeleteMember` (in `multisig2`) or `DeleteKey` (in `multisig`), the contract only purges **requests originated by** the removed member. It does not purge that member's **confirmations on other pending requests** that they merely confirmed but did not create. As a result, a request can later be executed by counting a confirmation from an account/key that is no longer a member of the multisig, letting a request execute with fewer *live* approvers than `num_confirmations` requires.

### Finding Description
`delete_member` filters requests by `r.member == member` (the request creator) and clears confirmations only for those requests: [1](#0-0) 

But confirmations added by a member on requests **created by someone else** are stored independently in the `confirmations: LookupMap<RequestId, HashSet<String>>` map, keyed only by `RequestId`, and are never scanned or filtered against a member being removed: [2](#0-1) 

`confirm()` simply checks `confirmations.len() + 1 >= self.num_confirmations` using whatever is stored in the set, without re-validating that every existing entry still corresponds to a current member: [3](#0-2) 

The only membership check performed is on the *new* confirmer, via `current_member()`/`assert_valid_request`, not on the confirmations already accumulated in the set: [4](#0-3) 

The same pattern exists in the legacy `multisig` contract, where `DeleteKey` only removes requests where `r.signer_pk == pk` and clears their confirmations, leaving that key's confirmations on other pending requests intact: [5](#0-4) [6](#0-5) 

This is structurally the same flaw as the external report: a piece of state (an oracle answer / a confirmation) is treated as permanently valid once recorded, even though a later, authoritative action (the negRiskAdapterOperator's flag / the multisig removing a member) invalidates it. The contract's "is resolved" check (`_isQuestionPriceAvailable` / `confirmations.len() >= num_confirmations`) doesn't re-derive validity from the current authoritative state (fully-determined market / current live membership), so stale approval is still honored.

### Impact Explanation
This breaks the equality that should hold: *number of confirmations counted == number of confirmations from currently authorized (live) members*. A request (e.g. a `Transfer` of NEAR, an `AddKey`/`AddMember` granting new access, or a `FunctionCall`) can execute with effectively fewer live signers than the configured `num_confirmations` threshold, because a stale confirmation from a since-removed member still counts. This matches the Critical impact category "a multisig request executed below threshold," since the multisig's core security guarantee — that N-of-M *current* members must approve — is violated.

### Likelihood Explanation
This requires no compromised keys and no special privilege beyond normal multisig operation: it triggers whenever (a) a request is created and partially confirmed, (b) one of its confirmers is later removed from the multisig for any legitimate reason (key rotation, offboarding, suspected compromise), and (c) the request is still pending. Multisig membership changes over time are a normal, expected occurrence, making this a realistic operational scenario rather than a contrived edge case.

### Recommendation
When removing a member (`delete_member` / `DeleteKey`), scan all pending requests and strip the removed member's entry from every confirmation set, not just from requests they originated. Alternatively, at confirmation-count time in `confirm()`, recompute the count by filtering the stored confirmation set down to entries that are still `self.members.contains(...)`, rather than trusting the raw `HashSet` length.

### Proof of Concept
1. Deploy `multisig2` with members `{A, B, C}` and `num_confirmations = 3`.
2. Member `A` calls `add_request_and_confirm` with a `Transfer` request X to attacker-controlled `receiver_id` (1 confirmation: `{A}`).
3. Member `B` calls `confirm(X)` (2 confirmations: `{A, B}`), one short of executing. [3](#0-2) 
4. Separately, members legitimately vote to remove `B` from the multisig (e.g. suspected key leak) via a `DeleteMember` request that reaches quorum among `{A, C}` and whichever else. `delete_member` runs, removing `B` from `members`, but request X's confirmation set still contains `B` because X was created by `A`, not `B`. [7](#0-6) 
5. Member `C` (the only other live member) calls `confirm(X)`. The check `confirmations.len() as u32 + 1 >= self.num_confirmations` evaluates `2 + 1 >= 3` → true, and request X executes, transferring funds out. [8](#0-7) 
6. Result: only 2 live members (`A`, `C`) actually approved the transfer, yet the configured 3-of-N threshold was reported as satisfied — the stale confirmation from removed member `B` was silently counted.

### Citations

**File:** multisig2/src/lib.rs (L122-133)
```rust
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

**File:** multisig2/src/lib.rs (L406-423)
```rust
    /// Prevents access to calling requests and make sure request_id is valid - used in delete and confirm
    fn assert_valid_request(&mut self, request_id: RequestId) {
        // request must come from key added to contract account
        assert(
            self.current_member().is_some(),
            "Caller (predecessor or signer) is not a member of this multisig",
        );
        // request must exist
        assert(
            self.requests.get(&request_id).is_some(),
            "No such request: either wrong number or already confirmed",
        );
        // request must have
        assert(
            self.confirmations.get(&request_id).is_some(),
            "Internal error: confirmations mismatch requests",
        );
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
