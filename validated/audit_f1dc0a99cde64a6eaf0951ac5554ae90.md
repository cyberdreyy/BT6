### Title
Multisig executes requests below `num_confirmations` live members because `delete_member` only purges requests submitted by (not merely confirmed by) the removed member - (File: multisig2/src/lib.rs)

### Summary
`MultiSigContract::confirm` counts entries in the `confirmations` `HashSet<String>` without checking whether each entry still corresponds to a live member of `self.members`. `delete_member` only removes pending requests whose `r.member` (the original submitter) equals the removed member, leaving stale confirmation entries from that member on requests submitted by others. This lets a request execute with fewer live confirmers than `num_confirmations` requires.

### Finding Description
The broken binding: `confirmations.get(request_id).len()` (the value used at line 304 `confirmations.len() as u32 + 1 >= self.num_confirmations`) is assumed to equal `count(current self.members whose to_string() ∈ confirmations.get(request_id))`. After a member removal, these two values diverge because stale confirmer strings are never purged from other members' pending requests.

Code path:
- `add_request` [1](#0-0)  stores `member: current_member` as the *submitter* of the request in `MultiSigRequestWithSigner.member`, while `confirmations` is a separate `HashSet<String>` keyed only by request id, independent of who submitted.
- `confirm` inserts a confirming member's `to_string()` into that set and later checks only the set's size against `num_confirmations`: [2](#0-1) 
- `delete_member` purges requests only where `r.member == member` (i.e., where the removed member was the *original submitter*), not requests the removed member merely confirmed as an approver: [3](#0-2) 

Exploit flow (members = [A,B,C], num_confirmations = 2):
1. B calls `add_request` for a `Transfer` request R (`r.member = B`), `confirmations[R] = {}`.
2. A calls `confirm(R)`: `confirmations.len()+1 = 1 < 2`, so A's identity string is inserted: `confirmations[R] = {A}`.
3. A separate `DeleteMember{member: A}` request (self-request, `receiver_id == current_account_id`, checked by `assert_self_request`) is submitted and confirmed by B and C, reaching `2 >= num_confirmations`, so it executes immediately via `execute_request` → `delete_member`. This removes A from `self.members`, but its request-purge filter only matches requests where `r.member == A` — R's `r.member` is B, so R and its confirmation set `{A}` are untouched.
4. C now calls `confirm(R)`: `confirmations.len() (1, stale "A") + 1 (C) = 2 >= num_confirmations (2)`, so `remove_request` + `execute_request` fire, transferring NEAR out even though only C is a currently live member who approved R.

No existing guard catches this: `assert_valid_request` only checks that the *caller* (C) is a current member and that the request/confirmations exist [4](#0-3) ; it never re-validates that previously recorded confirmer strings in the `HashSet<String>` still correspond to live members. `current_member()` is only used to validate the calling member each time `confirm` is invoked, not to filter/revalidate the stored `confirmations` set. [5](#0-4) 

### Impact Explanation
NEAR held in the multisig's account can be transferred out (or any other multisig action executed, including `AddKey`/`DeployContract`/`FunctionCall`) with fewer live, currently-authorized confirmations than `num_confirmations` requires. This directly matches the Critical category "a multisig request executed below `num_confirmations` live members." The blast radius covers any multisig deployed from this contract that ever removes a member who had confirmed (but not submitted) a still-pending request from another member — repeatable across requests and across any multisig instance using this contract.

### Likelihood Explanation
Preconditions are simple and reachable purely through calls available to multisig members: `num_confirmations >= 2`, at least 3 members, one pending request confirmed by a member who is later removed via a separate `DeleteMember` request. No special privilege beyond being a multisig member is needed (this scoped scenario grants members this position), and the sequence of `add_request`/`confirm` calls is standard usage. Since this only requires normal multisig operation with no owner/foundation intervention, likelihood is high whenever member turnover happens alongside pending requests.

### Recommendation
When counting confirmations in `confirm` (and anywhere else confirmation counts gate execution), filter `confirmations.get(&request_id)` to only those entries whose corresponding `MultisigMember` (reconstructed from the stored string, or by storing typed members instead of strings) is still contained in `self.members`, rather than trusting the raw `HashSet<String>` length. Additionally, `delete_member` should purge the removed member's confirmation entry from *every* pending request's `confirmations` set (not just requests it originally submitted).

### Proof of Concept
```rust
#[test]
fn test_stale_confirmation_after_member_removal_executes_below_threshold() {
    // members = [A, B, C], num_confirmations = 2
    // 1. testing_env! as B -> c.add_request(Transfer{..}) -> request_id R (r.member = B)
    // 2. testing_env! as A -> c.confirm(R) -> confirmations[R] = {A}, request still pending
    // 3. testing_env! as B -> c.add_request_and_confirm(DeleteMember{member: A}) -> 1 confirmation
    //    testing_env! as C -> c.confirm(delete_request_id) -> executes delete_member(A)
    //    assert!(!c.members.contains(&member_A));
    // 4. testing_env! as C -> let result = c.confirm(R);
    //    assert_eq!(c.confirmations.get(&R), None); // request executed/removed
    //    assert_eq!(c.requests.get(&R), None);
    //    // Binding check: only C is a live confirmer (count = 1) but num_confirmations = 2 was reported satisfied
    //    // result should be a PromiseOrValue::Promise(_) (transfer scheduled) despite only 1 live confirmer
}
```
This demonstrates `confirmations.len()+1 >= num_confirmations` (2 >= 2) triggering execution while the actual count of live members among confirmers is 1 (only C), violating the required binding.

### Citations

**File:** multisig2/src/lib.rs (L189-197)
```rust
        let request_added = MultiSigRequestWithSigner {
            member: current_member,
            added_timestamp: env::block_timestamp(),
            request,
        };
        self.requests.insert(&self.request_nonce, &request_added);
        let confirmations = HashSet::new();
        self.confirmations
            .insert(&self.request_nonce, &confirmations);
```

**File:** multisig2/src/lib.rs (L294-315)
```rust
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

**File:** multisig2/src/lib.rs (L322-339)
```rust
    fn current_member(&self) -> Option<MultisigMember> {
        let member = if env::current_account_id() == env::predecessor_account_id() {
            MultisigMember::AccessKey {
                public_key: env::signer_account_pk()
                    .try_into()
                    .unwrap_or_else(|_| env::panic_str("Failed to deserialize public key")),
            }
        } else {
            MultisigMember::Account {
                account_id: env::predecessor_account_id(),
            }
        };
        if self.members.contains(&member) {
            Some(member)
        } else {
            None
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

**File:** multisig2/src/lib.rs (L407-423)
```rust
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
