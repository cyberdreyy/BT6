### Title
Stale confirmations from removed multisig members still count toward execution threshold - (File: `multisig2/src/lib.rs`)

### Summary
`MultiSigContract::confirm` in `multisig2/src/lib.rs` compares the size of the stored `confirmations` set (plus the new one) against `num_confirmations`, without verifying that the accounts/keys who previously confirmed are still active members. `delete_member` only cleans up requests that were *originally created* by the removed member; it does not scrub that member's confirmations from *other* still-pending requests. This lets a request execute even though the number of confirmations from currently-live members is below `num_confirmations`, mirroring the reported bug class where a removed participant's recorded contribution is not backed out of a running total.

### Finding Description
`confirm()` decides whether to execute a request purely from the cardinality of the `confirmations` HashSet: [1](#0-0) 

`delete_member` (invoked via `DeleteMember` / `execute_request`) removes the departing member from `self.members`, deletes the *requests that member itself created*, and clears `num_requests_pk` for that member — but it never walks `self.confirmations` to strip that member's prior confirmations from *other* pending requests: [2](#0-1) 

Because of this, the binding the contract is supposed to maintain —

```
confirmations.len() (live members only) == count of confirmations that should count toward num_confirmations
```

— is broken. A confirmation recorded by an account that is later removed as a member remains in the `HashSet<String>` for any request it had confirmed before being removed, and continues to count toward `confirmations.len() as u32 + 1 >= self.num_confirmations` in `confirm()`.

Concrete flow:
1. Members = {A, B, C, D}, `num_confirmations = 3`.
2. A creates request R (transfer). B confirms R (`confirmations = {B}`, size 1, below threshold).
3. Before R is further confirmed, members vote to `DeleteMember(B)` (this only touches requests B authored and B's `num_requests_pk`; R is untouched since A, not B, authored it).
4. Now the live member set is {A, C, D}, i.e., effectively a 2-of-3 (or 3-of-3) scheme, but R's `confirmations` set still contains ex-member B.
5. C confirms R: `confirmations.len() + 1 == 2(existing: B) + 1 == 3 >= num_confirmations(3)` → request executes with only C (a genuine live confirmer) and stale B, i.e., fewer live confirmations than the configured threshold actually approved the transfer.

This is the same custody-binding failure as the report: an entity that no longer qualifies (removed governor / removed multisig member) still has its recorded contribution counted positively toward an aggregate that gates a privileged action (passing a DAO proposal / executing a multisig transfer), instead of being backed out when the entity is removed.

### Impact Explanation
This breaks the fundamental multisig guarantee "K live signers must approve before funds move." A transfer, key addition, or contract upgrade can be executed with fewer genuinely-authorized confirmations than `num_confirmations`, i.e., a multisig request executed below threshold — moving NEAR to a party not entitled to it under the current membership. This matches the Critical impact category: "a multisig request executed below threshold."

### Likelihood Explanation
No attacker capability beyond ordinary usage of the multisig is required: the sequence (confirm, then remove the confirmer as a member, then get one more live confirmation) is a normal operational pattern (e.g., replacing a compromised or departing signer) that any subset of members controlling `DeleteMember`/`AddMember` requests can trigger, intentionally or not. It requires no redeploy, no bypass of documented initialization, and no external actor — only the documented `confirm`/`DeleteMember` flow, so likelihood is high for any multisig that rotates members while requests are outstanding.

### Recommendation
When removing a member via `delete_member`, iterate over `self.requests`/`self.confirmations` and strip the removed member's entry from every confirmation set (not just requests the member authored), or alternatively re-validate at `confirm()` time that every entry in the stored `confirmations` set still belongs to `self.members` before comparing against `num_confirmations`.

### Proof of Concept
1. Deploy `multisig2` with members `[A, B, C, D]`, `num_confirmations = 3`.
2. As A, call `add_request_and_confirm` with a `Transfer` action to receiver — `confirmations = {A}` (size 1).
3. As B, call `confirm(request_id)` — `confirmations = {A, B}` (size 2, `2 < 3`, not executed).
4. As a quorum of members, submit and confirm `DeleteMember { member: B }` as a separate request/tx (`delete_member` only removes requests authored by B and `num_requests_pk[B]`; it does not touch the confirmations set of the transfer request, per `multisig2/src/lib.rs:355-379`).
5. As C (a genuinely live member), call `confirm(request_id)` on the pending transfer.
6. `confirmations.len() (2, including stale B) + 1 == 3 >= num_confirmations (3)` → `execute_request` runs the transfer, even though only A and C (2 live members) actually approved it — one short of the configured 3-of-N threshold. [1](#0-0) [2](#0-1)

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
