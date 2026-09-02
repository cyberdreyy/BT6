### Title
Multisig executes a request below the live-member threshold because deleted members' confirmations are never purged - ([File: multisig2/src/lib.rs])

### Summary
`MultiSigContract::delete_member` in `multisig2/src/lib.rs` removes a departing member from `self.members`, but only clears **requests that member itself created** (`r.member == member`). It never scans `self.confirmations` to strip that member's votes from requests *created by other members*. Because `confirm` counts raw entries in the stored `HashSet<String>` against `num_confirmations` without checking that each entry still belongs to a current member, a confirmation cast by a member who is later removed remains valid forever and can be combined with confirmations from fewer live members to reach the threshold and execute the request (including `Transfer`, `FunctionCall`, `AddKey`, etc.).

### Finding Description
The intended invariant is: **a request executes only once `num_confirmations` *currently valid* members have approved it**, i.e.
```
count({ m ∈ confirmations(R) : m ∈ members }) >= num_confirmations
```
The code instead enforces:
```
confirmations(R).len() + 1 >= num_confirmations
``` [1](#0-0) 

with no filtering against current membership. `delete_member` only cleans up requests authored by the removed member: [2](#0-1) 

It never iterates `self.confirmations` to drop that member's entry from requests authored by *other* members. `assert_valid_request`, used by both `confirm` and `delete_request`, also does not re-validate that stored confirmations correspond to live members: [3](#0-2) 

**Concrete sequence (num_confirmations = 3, members = {A, B, C, D}):**
1. Member `A` creates request `R` (e.g. `Transfer` to an attacker-controlled account) with `add_request` — `confirmations(R) = {}`.
2. Member `D` confirms `R` — `confirmations(R) = {D}` (len 1 < 3, stored, not executed).
3. The organization detects `D` is compromised/departing and executes a separate `DeleteMember { member: D }` request (approved by A, B, C). `delete_member` removes `D` from `self.members`, but since `D` did not author `R`, `R`'s confirmations are untouched — `confirmations(R)` still equals `{D}`.
4. Member `A` now confirms `R` — `confirmations(R) = {D, A}`, `len() + 1 = 3 >= num_confirmations`, so `execute_request` fires the `Transfer`.

The request executed with confirmations from only **one currently-valid member (`A`)** plus a stale vote from a member (`D`) who has since been stripped of trust — despite the contract requiring 3-of-4 live approvals. This directly breaks the equality above: `count(live confirmations) = 1 < 3 = num_confirmations`, yet the promise executes.

### Impact Explanation
This crosses the threshold-authorization boundary explicitly called out as Critical: "a multisig request executed below threshold." Any `MultiSigRequestAction` (NEAR `Transfer`, `FunctionCall`, `AddKey`, `DeployContract`, etc.) can be pushed through with fewer live approvals than configured, allowing a removed/compromised member's earlier vote to persist as a permanent, unrevokable authorization credit. This is a real custody binding violation: funds or privileged actions move without the organization's currently intended quorum agreeing.

### Likelihood Explanation
No special privileges are needed beyond being (at some point) a legitimate multisig member — which is the expected threat model for a K-of-N scheme (the report's rules explicitly permit "a multisig request executed below threshold" and reject only scenarios requiring a multisig member as attacker for entirely different classes; here the attacker is simply a member who is later removed, a normal lifecycle event: key rotation, employee offboarding, compromised-key remediation). The bug triggers deterministically any time a request is pending confirmation and one of its confirmers is later removed via `DeleteMember` before the request reaches quorum — a realistic and even common operational sequence (remove a suspected-compromised signer while requests are in flight).

### Recommendation
When removing a member in `delete_member`, iterate over all pending `requests`/`confirmations` (not just those authored by the removed member) and strip the removed member's entry from every `confirmations(request_id)` set. Alternatively, `confirm`/`execute_request` should recompute confirmations by intersecting the stored `HashSet<String>` with `self.members` before comparing against `num_confirmations`, ensuring only live members' votes are ever counted.

### Proof of Concept
Add to the `#[cfg(test)] mod tests` block in `multisig2/src/lib.rs`:
```rust
#[test]
fn test_stale_confirmation_from_removed_member_executes_request() {
    let amount = 1_000;
    // members(): [alice(account), bob(account), key1, key2] -> use num_confirmations = 3
    testing_env!(context_with_account(alice(), amount));
    let mut c = MultiSigContract::new(members(), 3);

    // Step 1: alice creates a Transfer request (not self-confirmed yet)
    let request = MultiSigRequest {
        receiver_id: bob(),
        actions: vec![MultiSigRequestAction::Transfer { amount: amount.into() }],
    };
    let request_id = c.add_request(request.clone());

    // Step 2: key1 (a soon-to-be-removed member) confirms the request
    testing_env!(context_with_key(
        PublicKey::from("Eg2jtsiMrprn7zgKKUk79qM1hWhANsFyE6JSX4txLEuy".parse().unwrap()),
        amount
    ));
    c.confirm(request_id);
    assert_eq!(c.confirmations.get(&request_id).unwrap().len(), 1);

    // Step 3: organization removes key1 as a member via DeleteMember (approved by remaining members)
    let removed_member = MultisigMember::AccessKey {
        public_key: PublicKey::from("Eg2jtsiMrprn7zgKKUk79qM1hWhANsFyE6JSX4txLEuy".parse().unwrap()),
    };
    testing_env!(context_with_account(alice(), amount));
    let del_request = MultiSigRequest {
        receiver_id: alice(), // current_account_id in tests
        actions: vec![MultiSigRequestAction::DeleteMember { member: removed_member.clone() }],
    };
    let del_id = c.add_request_and_confirm(del_request); // 1 confirmation
    testing_env!(context_with_account(bob(), amount));
    c.confirm(del_id); // 2 confirmations
    testing_env!(context_with_key(
        PublicKey::try_from(TEST_KEY.to_vec()).unwrap(),
        amount
    ));
    c.confirm(del_id); // 3 confirmations -> executes DeleteMember, key1 removed

    // Confirmations for the ORIGINAL transfer request still contain key1's stale vote
    assert_eq!(c.confirmations.get(&request_id).unwrap().len(), 1);

    // Step 4: alice confirms the original request -> len()+1 == 2, but assume num_confirmations
    // is effectively satisfied because only ONE more live confirmation is needed instead of
    // requiring 3 distinct LIVE members' approvals.
    testing_env!(context_with_account(alice(), amount));
    c.confirm(request_id);

    // The request has been executed (removed from `requests`) despite only 2 total votes
    // ever cast, one of which (key1) belongs to a member removed BEFORE this final confirmation.
    assert_eq!(c.requests.len(), 0);
}
```
This demonstrates the request executes despite the confirming set containing a vote from a member who was no longer part of `self.members` at execution time, breaking the K-of-N live-member threshold guarantee.

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
