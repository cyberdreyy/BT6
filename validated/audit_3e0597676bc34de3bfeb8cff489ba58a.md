## Analysis Result

### Title
Removed multisig member's stale confirmation still counts toward execution threshold - (File: multisig2/src/lib.rs)

### Summary
`delete_member` in the multisig2 contract only purges *requests originated by* the removed member; it never scrubs that member's entries out of the `confirmations` set of *other* still-pending requests that the removed member had previously confirmed. Because `confirm()` only checks the raw count of entries in `confirmations`, a stale confirmation from a member who has since been deleted still counts toward `num_confirmations`, letting a request execute with fewer currently-valid members than the configured threshold.

### Finding Description
When a member is deleted via a `DeleteMember` request, `delete_member` does the following cleanup: [1](#0-0) 
It removes only the requests where `r.member == member` (i.e., requests the deleted member itself created), and clears `num_requests_pk` for that member. It does **not** iterate over `self.confirmations` to strip the removed member's identifier from confirmation sets belonging to requests created by *other* members.

`confirm()` determines whether a request should execute purely by counting entries already present in `confirmations`: [2](#0-1) 
It never re-validates that every already-recorded confirmer is still in `self.members`; it only checks that the *new* confirming caller is a current member (via `current_member()`) and hasn't confirmed already.

Concretely:
1. Members A, B, C, D exist with `num_confirmations = 3`.
2. Member B creates request R (e.g. a `Transfer` or `AddKey` action).
3. Member A confirms R → `confirmations[R] = {A}`.
4. Members later vote to remove A via a `DeleteMember` request, which reaches threshold and executes `delete_member`. R still exists (it wasn't created by A), so `confirmations[R]` still contains A's stale entry.
5. Member C confirms R → `confirmations[R] = {A, C}`, count is 2, one short of threshold.
6. Member D confirms R → count becomes 3 → `confirm()` executes R.

At the moment of execution only C and D are live members who genuinely approved R (2 live confirmations), yet the threshold of 3 was satisfied only because of A's stale, now-invalid confirmation. This is the "confirmations counted versus live members" binding being broken: `confirmations.len() == num_confirmations` should imply `num_confirmations` *live* members approved, but that equality is never actually enforced.

### Impact Explanation
This allows a `MultiSigRequest` (fund transfer, `AddKey`, `FunctionCall`, `DeployContract`, etc.) to execute despite not having genuine approval from the required number of currently-authorized members — i.e., a multisig request executed below the real, live-member threshold. This directly matches the "Critical" impact category: a multisig request executed below threshold. It undermines the entire security assumption of the K-of-N scheme (removing a compromised or departing member is supposed to revoke their influence immediately), and any funds/actions the multisig protects can be moved with effectively fewer real authorizers than configured.

### Likelihood Explanation
This requires no attacker to have special god-mode privilege beyond being one of the legitimate multisig members (which is the expected threat model for any of these accounting checks per the rules — the exploit is not "requiring the foundation/owner/victim key", it happens purely through the ordinary member-removal workflow that every multisig using this contract is expected to use). The scenario is entirely realistic: removing a member (e.g., because their key was compromised or they left the organization) is a normal multisig operation, and any request pending confirmation from before the removal is silently left with a phantom vote. No special timing or race condition is even needed — it is a straightforward missing-cleanup bug that will manifest any time a member is deleted while other members' outstanding requests exist.

### Recommendation
When `delete_member` removes a member, iterate over all active requests' confirmation sets (not just requests created by that member) and remove the deleted member's identifier from each. Alternatively, revalidate confirmations against the current `members` set at the top of `confirm()` (e.g., filter `confirmations` to only currently valid members before comparing against `num_confirmations`), so a stale confirmation from a removed member can never contribute to reaching the threshold.

### Proof of Concept
1. Deploy `multisig2` with members `{A, B, C, D}` and `num_confirmations = 3`.
2. As B, call `add_request` with a `Transfer` request R (receiver arbitrary).
3. As A, call `confirm(R)` → `confirmations[R] = {A}`.
4. As a quorum of members, submit and confirm a `DeleteMember { member: A }` request; it executes via `execute_request` → `delete_member`, per [3](#0-2) . `A` is removed from `self.members`, but `confirmations[R]` still contains `A`.
5. As C, call `confirm(R)` → `confirmations[R] = {A, C}`, length 2.
6. As D, call `confirm(R)` → `confirmations.len() + 1 = 3 >= num_confirmations`, so `execute_request(R)` runs per [4](#0-3) , transferring funds — even though only C and D (2 live members) actually approved R after A's removal.

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
