## Analysis

The Sherlock bug class here is a **stale-authorization / trust-object mismatch**: a system trusts a boolean/counter (`auction exists?`) instead of re-validating the actual current state (`is this borrower still delinquent?`). The strongest analog in this repository is in the `multisig2` contract, matching the explicitly allowed category "confirmations counted versus live members."

`MultiSigContract::delete_member` (multisig2/src/lib.rs) removes a member and cleans up only the **requests originated by that member**, but never scans the `confirmations` map to strip that member's already-recorded confirmations from *other* members' pending requests: [1](#0-0) 

Meanwhile `confirm()` only checks that the *current* caller hasn't already confirmed, and executes the request once the confirmation-set size reaches `num_confirmations` — it never re-validates that the previously stored confirmations still belong to current members: [2](#0-1) 

`current_member()` is only used to authenticate the *new* confirming party, not to re-check historical entries in `confirmations`: [3](#0-2) 

### Title
Stale confirmations from removed multisig members still count toward `num_confirmations`, allowing execution below the intended K-of-N threshold - (File: `multisig2/src/lib.rs`)

### Summary
`MultiSigContract::confirm` treats the size of the `confirmations` set for a request as proof of `num_confirmations` distinct **current** members having approved it. `delete_member` only purges requests *authored* by the removed member; it never removes that member's already-cast confirmations on requests authored by *other* members. Once a member is removed from the multisig, their stale confirmation entries remain valid votes, so a request can execute with fewer live, currently-authorized members confirming than `num_confirmations` requires.

### Finding Description
The binding that should hold is:
`confirmations.len() at execution == number of distinct *current* members who confirmed`

In practice:
1. Member `B` calls `add_request(R1)` (not yet confirmed).
2. Member `A` calls `confirm(R1)` → `confirmations[R1] = {A}` (count 1, below threshold, not executed) — [4](#0-3) 
3. Separately, the multisig (via a `DeleteMember{member: A}` request confirmed by `num_confirmations` other members) removes `A`. `delete_member` deletes `A` from `self.members` and purges only requests where `r.member == A` (i.e. requests A *authored*). `R1` was authored by `B`, so it is untouched and its `confirmations` set still contains `A`: [5](#0-4) 
4. `A` is no longer in `self.members`, so `A` can no longer call `confirm` themselves, but their old confirmation entry for `R1` is never invalidated.
5. Member `C` (a genuinely live, current member) calls `confirm(R1)`. `confirmations[R1]` becomes `{A, B... }`/`{A, C}` with length `2 == num_confirmations`, so `execute_request` runs — even though only `C` (and possibly the original author `B` if also counted) is a currently authorized member; `A`'s vote is a ghost vote from a party the multisig no longer trusts.

This directly breaks the "K out of N" invariant the contract's own README documents (`Set K out of N multi sig scheme`) — [6](#0-5) .

### Impact Explanation
This matches the "Critical" impact category explicitly: **a multisig request executed below threshold**. An attacker (or a member later revoked for cause, e.g. compromised key or removed employee) can leave a "pre-loaded" confirmation on a pending request. After removal, any single remaining live member can push that request past `num_confirmations` and execute arbitrary `MultiSigRequestAction`s (including `Transfer`, `FunctionCall`, `AddKey`, `AddMember`) with fewer live approvals than the deployer configured — an unauthorized-move-of-funds / governance-bypass scenario.

### Likelihood Explanation
This requires no special privilege beyond being (or having been) a legitimate multisig member at some point — precisely the "unprivileged attacker breaking a custody binding" pattern in scope. Any organization that ever removes a member (a normal, expected multisig operation — e.g. offboarding an employee or rotating a compromised key) is exposed if that member had a pending, unexecuted confirmation left on some other request at the time of removal. No malicious redeploy, foundation action, or social engineering is needed — only the ordinary sequence of `confirm` then later `DeleteMember`.

### Recommendation
In `delete_member`, iterate over all entries in `self.requests`/`self.confirmations` (not just those authored by the removed member) and remove the deleted member's key from every confirmation set. Alternatively, validate at `confirm`-time (when tallying) that each entry in the stored `confirmations` set still corresponds to `self.members.contains(...)`, discarding stale entries before comparing against `num_confirmations`.

### Proof of Concept
```
members = [A, B, C], num_confirmations = 2

1. B: add_request(R1 = Transfer{amount, receiver_id})       // confirmations[R1] = {}
2. A: confirm(R1)                                           // confirmations[R1] = {A}, len=1 < 2, no execution
3. (Separately) B & C: add_request_and_confirm / confirm a
   DeleteMember{member: A} request                          // A removed from `members`
   -> delete_member() removes requests authored by A only;
      confirmations[R1] still == {A}
4. C: confirm(R1)                                            // confirmations[R1] = {A, C}, len=2 >= num_confirmations
   -> execute_request(R1) runs the Transfer
```
Only `C` is a currently live, authorized member confirming; `A`'s stale confirmation counted toward the threshold, so the request executed with 1 live confirmation instead of the required 2.

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

**File:** multisig2/src/lib.rs (L321-339)
```rust
    /// Returns current member: either predecessor as account or if it's the same as current account - signer.
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

**File:** multisig2/README.md (L5-14)
```markdown
This contract provides:
 - Set K out of N multi sig scheme
 - Request to sign transfers, function calls, adding and removing keys.
 - Any of the access keys or set of specified accounts can confirm, until the required number of confirmation achieved.

## Multisig implementation details

Multisig uses set of `FunctionCall` `AccessKey`s and account ids as a set of allowed N members. 
When contract is being setup, it should be initialized with set of members that will be initially managing this account.
All operations going forward will require `K` members to call `confirm` to be executed.
```
