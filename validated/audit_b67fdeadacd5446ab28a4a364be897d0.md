## Analysis

I confirmed the exact custody/threshold binding that `delete_member` is supposed to preserve, and found that it breaks that binding for *other* members' pre-existing confirmations rather than only the deleted member's own requests.

### Title
Removing a multisig member does not purge that member's existing confirmations on other pending requests, allowing execution below the intended live-member threshold - (File: `multisig2/src/lib.rs`)

### Summary
`delete_member()` in `multisig2/src/lib.rs` only removes requests *originated by* the deleted member (`r.member == member`), and clears `num_requests_pk` for that member. It never scans the `confirmations` map to strip that member's confirmation from other requests where the deleted member had already called `confirm()` but the request had not yet reached threshold. Because `confirmations` is `HashSet<String>` keyed only by the member's serialized identity, a confirmation cast by a member who is later removed continues to count toward `self.num_confirmations` in `confirm()`, even though that member is no longer part of the `members` set and has lost their access key.

### Finding Description
`confirm()` at [1](#0-0)  compares `confirmations.len() as u32 + 1 >= self.num_confirmations` — it counts the size of the historical `confirmations` set for a request, not the number of currently-live members who signed it.

`delete_member()` at [2](#0-1)  only removes confirmations for requests where `r.member == member` (i.e., requests the deleted member *created*):
```
let request_ids: Vec<u32> = self
    .requests
    .iter()
    .filter_map(|(k, r)| if r.member == member { Some(k) } else { None })
    .collect();
for request_id in request_ids {
    self.confirmations.remove(&request_id);
    self.requests.remove(&request_id);
}
```
It does not iterate `self.confirmations` to strip the deleted member's `to_string()` entry from requests created by *other* members that the deleted member had already confirmed.

Scenario: with `num_confirmations = K` and members {A, B, C, D}. Member A creates a request and confirms it (1 confirmation). Member D confirms it too (2 confirmations, still < K=3). The group then removes member D via a `DeleteMember` action (e.g., because D's key was compromised, or D left the organization) executed by the remaining members. `delete_member` does not touch the request A created, because it was not created *by* D — D's confirmation entry remains embedded in `confirmations[request_id]`. Now only 2 of the (now 3) live members (A, B, C) are needed to reach `confirmations.len() + 1 >= 3` — but the set already contains D's stale confirmation, so **only one more live member (B or C) needs to confirm** to hit the threshold and execute the transfer. This lets a request be executed with fewer than `num_confirmations` *currently authorized* signers approving it — the invariant `confirmations counted == confirmations from live members` is violated.

### Impact Explanation
This directly matches the allowed impact category "a multisig request executed below threshold" (Critical). A transfer, `AddKey`, `DeployContract`, or `FunctionCall` request can be executed with fewer live-member approvals than `num_confirmations` mandates, because a removed member's stale confirmation persists and is still counted. This can result in unauthorized movement of NEAR held by the multisig account, or unauthorized code/key changes, effectively defeating the K-of-N security guarantee that is the entire purpose of the contract.

### Likelihood Explanation
The bug requires no attacker capability beyond normal governance flows already anticipated by the protocol: any legitimate `DeleteMember` action (e.g., rotating a compromised or departing member's key) that happens while that member has an outstanding confirmation on some other unconfirmed request will silently leave a stale confirmation that counts against the new, smaller-or-same threshold. It doesn't require a malicious member, a redeploy, or any out-of-scope precondition — only the sequence: (1) member confirms a request without reaching threshold, (2) that member is removed via a separate `DeleteMember` request, (3) remaining members continue confirming the first request. This is a realistic, foreseeable operational sequence, not a contrived edge case.

### Recommendation
In `delete_member()`, iterate all entries of `self.confirmations` (not just requests authored by the deleted member) and remove the deleted member's identity string from every confirmation set. Additionally, `confirm()`'s threshold check should validate that `member.to_string()` entries in the confirmation set are still members of `self.members` before counting them (or actively prune stale entries lazily), ensuring the threshold is always computed against the current live membership rather than a historical snapshot.

### Proof of Concept
Conceptually (given `multisig2` test harness conventions in [3](#0-2) ):
1. Initialize `MultiSigContract::new(members = [A, B, C, D], num_confirmations = 3)`.
2. As A: `add_request_and_confirm(transfer_request)` → `confirmations = {A}` (len 1).
3. As D: `confirm(request_id)` → `confirmations = {A, D}` (len 2, still < 3).
4. As A/B/C (reaching threshold via a separate `DeleteMember{member: D}` request): execute `DeleteMember` for D. `delete_member` removes D from `members` and any requests D *authored*, but the `transfer_request` (authored by A) is untouched — `confirmations[transfer_request_id]` still equals `{A, D}`.
5. As B: `confirm(transfer_request_id)` → `confirmations.len() (2) + 1 >= num_confirmations (3)` → request executes.
6. Result: the transfer executed with confirmations from only A and B — two live members plus one stale (removed) member D — instead of three currently-authorized members, violating the K-of-N guarantee.

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

**File:** multisig2/src/lib.rs (L573-612)
```rust
    #[test]
    fn test_multi_3_of_n() {
        let amount = 1_000;
        testing_env!(context_with_key(
            PublicKey::from(
                "Eg2jtsiMrprn7zgKKUk79qM1hWhANsFyE6JSX4txLEuy"
                    .parse()
                    .unwrap()
            ),
            amount
        ));
        let mut c = MultiSigContract::new(members(), 3);
        let request = MultiSigRequest {
            receiver_id: bob(),
            actions: vec![MultiSigRequestAction::Transfer {
                amount: amount.into(),
            }],
        };
        let request_id = c.add_request(request.clone());
        assert_eq!(c.get_request(request_id), request);
        assert_eq!(c.list_request_ids(), vec![request_id]);
        c.confirm(request_id);
        assert_eq!(c.requests.len(), 1);
        assert_eq!(c.confirmations.get(&request_id).unwrap().len(), 1);
        testing_env!(context_with_key(
            PublicKey::from(
                "HghiythFFPjVXwc9BLNi8uqFmfQc1DWFrJQ4nE6ANo7R"
                    .parse()
                    .unwrap()
            ),
            amount
        ));
        c.confirm(request_id);
        assert_eq!(c.confirmations.get(&request_id).unwrap().len(), 2);
        assert_eq!(c.get_confirmations(request_id).len(), 2);
        testing_env!(context_with_account(bob(), amount));
        c.confirm(request_id);
        // TODO: confirm that funds were transferred out via promise.
        assert_eq!(c.requests.len(), 0);
    }
```
