### Title
Stale confirmations from removed members allow multisig request execution below the live-member confirmation threshold - (File: `multisig2/src/lib.rs`)

### Summary
`confirm()` counts confirmations purely by the size of the `confirmations` set stored against a `request_id`, without re-validating that each confirming member is still a current member of the multisig. `delete_member()` only purges pending *requests* originated by the removed member, but does not scrub that member's existing *confirmations* recorded on other members' pending requests. This lets a request reach `num_confirmations` and execute even though one or more of the counted confirmations came from an account/key that is no longer a live member.

### Finding Description
`confirm()` compares the number of already-recorded confirmations against `self.num_confirmations` and, once the threshold is met, immediately executes the request: [1](#0-0) 

The confirmation count check `confirmations.len() as u32 + 1 >= self.num_confirmations` treats every entry in the stored `HashSet<String>` as a valid, live confirmation. It never re-checks that each account/public key recorded in that set is still present in `self.members`.

`delete_member()` removes the departing member from `self.members` and deletes only the requests that member originally *authored*, along with that member's own `num_requests_pk` bookkeeping: [2](#0-1) 

It does not iterate `self.confirmations` to strip the removed member's prior confirmation from any *other* pending request they had confirmed but did not author. As a result, a confirmation recorded before a member's removal remains present in the stored confirmation set indefinitely, and continues to count toward `num_confirmations` for that request.

Binding broken (as an equality): the security model promises
`executed_request.confirmations == num_confirmations` confirmations *from currently live members*.
In practice, after a member removal, the invariant becomes
`executed_request.confirmations == num_confirmations` where some subset of those confirmations belong to accounts that are no longer members — i.e. `live_confirming_members < num_confirmations` while the request still executes.

### Impact Explanation
This falls in the Critical bucket described by the scope rules: "a multisig request executed below threshold." Concretely, for a K-of-N multisig:
1. Member A creates and confirms a sensitive request (e.g., `Transfer`, `FunctionCall`, `AddKey`) that needs K confirmations; Member B also confirms it, leaving it one confirmation short of K.
2. Separately (through a legitimate `DeleteMember` multisig action), Member B is later removed from the multisig, e.g., because their key was compromised or they left the organization.
3. Member B's confirmation on the pending request from step 1 is never removed by `delete_member()`.
4. Only one additional live member now needs to confirm to hit `num_confirmations`, even though the "removed" member's stale confirmation is counted as if they were still authorized. The transaction executes with fewer genuinely live-member confirmations than the configured threshold requires.

This directly undermines the K-of-N custody guarantee of the multisig: funds can move, keys can be added, or contracts can be redeployed with an effective threshold lower than configured, without any live member noticing that a "ghost" confirmation is being counted.

### Likelihood Explanation
No privileged access beyond normal multisig membership is required — this is reachable through the documented member lifecycle (a member is confirmed via normal `DeleteMember` request) combined with ordinary pending-request confirmation activity, which is common in any active multisig with request backlog. The scenario (a member being removed while they have outstanding confirmations on other pending requests) is a realistic day-to-day occurrence, not a contrived edge case, making this a background risk in any long-lived deployment rather than a one-off setup mistake.

### Recommendation
When executing `delete_member()`, iterate all entries in `self.confirmations` and remove the departing member's entry from any confirmation set it appears in (not just requests it authored). Alternatively, at confirmation-count time in `confirm()`, filter the stored confirmation set against `self.members` before comparing its length to `num_confirmations`, so only confirmations from currently live members are counted toward the threshold.

### Proof of Concept
1. Deploy `MultiSigContract::new(members = [A, B, C], num_confirmations = 3)`.
2. Member A calls `add_request_and_confirm(request)` — creates a `Transfer` request with 1 confirmation (A).
3. Member B calls `confirm(request_id)` — now 2 confirmations (A, B), still short of 3.
4. Members confirm and execute a separate `DeleteMember{member: B}` request, removing B from `self.members` — see `delete_member` at [3](#0-2) . B's confirmation on the pending Transfer request is untouched.
5. Member C calls `confirm(request_id)` — `confirmations.len() + 1 == 3 >= num_confirmations`, so `remove_request`/`execute_request` fires at [4](#0-3) , transferring funds even though only 2 of the 3 confirmations (A and C) came from currently live members.

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
