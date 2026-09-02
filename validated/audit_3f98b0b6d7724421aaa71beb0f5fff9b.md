### Title
Duplicate member entries in `MultiSigContract::new` bypass the confirmations-vs-members invariant, permanently locking the multisig - (File: `multisig2/src/lib.rs`)

### Summary
`MultiSigContract::new` validates that `num_confirmations` is achievable by comparing it against the raw length of the caller-supplied `members` vector, without checking that the entries are unique. Because `self.members` is stored as an `UnorderedSet<MultisigMember>`, duplicate entries collapse to a single unique member at insertion time, so the deduplicated member count can end up strictly smaller than `num_confirmations` even though the initialization assertion passed.

### Finding Description
In `new`, the only guard against an unreachable confirmation threshold is: [1](#0-0) 

```
pub fn new(members: Vec<MultisigMember>, num_confirmations: u32) -> Self {
    assert(
        members.len() >= num_confirmations as usize,
        "Members list must be equal or larger than number of confirmations",
    );
    ...
    for member in members {
        promise = multisig.add_member(promise, member);
    }
    multisig
}
``` [2](#0-1) 

`members.len()` is the length of the raw input `Vec`, which may contain duplicate `MultisigMember` entries (same `account_id` or same `public_key`). Each element is then passed to `add_member`, which inserts into the deduplicating `UnorderedSet`: [3](#0-2) 

Because `self.members` de-duplicates, the true number of live, distinct members can be smaller than the vector length used to pass the `assert`. Confirmations are also tracked per unique member string in a `HashSet`, and re-confirmation by the same member is explicitly rejected: [4](#0-3) 

So if `members` contains duplicates such that unique member count `< num_confirmations`, the `HashSet` of confirmations for any request can never reach `num_confirmations`, and `confirm` can never trigger `execute_request`. Since adding a new member itself requires an `AddMember` action to be approved through the same broken confirmation flow (`assert_self_request` + normal `confirm` threshold), the contract cannot recover: the deployed account (and any NEAR later transferred to it) becomes permanently unusable.

This mirrors the reported bug class in Redemptions/TokenRequest: an initialization list that is trusted to represent a set of distinct entities is never checked for uniqueness, so the invariant relating a Q of a "count" to the underlying distinct set is silently broken (there: `redeemableTokens.length` implying distinct tokens; here: `members.len()` implying distinct signers, i.e. "confirmations counted versus live members").

### Impact Explanation
This is a Critical-class outcome under the funds-freezing criterion: any NEAR transferred to a multisig account initialized this way becomes permanently frozen, because no request (including one to fix membership or thresholds) can ever collect enough distinct confirmations. This can happen via the public, permissionless `multisig-factory`, whose `create` function forwards caller-supplied `members`/`num_confirmations` unchanged to `MultiSigContract::new`: [5](#0-4) 

### Likelihood Explanation
Triggering the bug only requires calling `new` (directly, or via `multisig-factory::create`) with a `members` vector that contains duplicate entries — no privileged role, victim key, or special access is needed. The `num_confirmations >= 1` and `members.len() >= num_confirmations` checks provide no protection against this since they operate on the raw, non-deduplicated vector.

### Recommendation
In `MultiSigContract::new`, verify uniqueness of the `members` input before/while inserting, e.g. check the return value of `self.members.insert(...)` (or use `HashSet`/comparison of `members.len()` to `self.members.len()` after the loop) and reject the call if any duplicate is detected, matching the fix pattern used for Redemptions (`redeemableTokenAdded[token] == false`).

### Proof of Concept
1. Call `new` with `members = [{"account_id": "alice"}, {"account_id": "alice"}, {"account_id": "alice"}]` and `num_confirmations = 3`.
2. `members.len() == 3 >= num_confirmations == 3` passes the assertion in `new`.
3. The loop calls `add_member` three times with the same `MultisigMember::Account { account_id: "alice" }`; `self.members` (an `UnorderedSet`) ends up containing exactly one entry.
4. Fund the multisig account with NEAR.
5. `alice` calls `add_request` then `confirm`: `current_member()` resolves to `alice`, `confirmations` gets `{"alice"}` (size 1), which is `< num_confirmations (3)`, so `execute_request` is never reached.
6. `alice` cannot confirm again (`"Already confirmed this request with this key"`), and no other distinct member exists to provide the remaining confirmations — the request, and any NEAR balance held by the contract, are permanently stuck.

### Citations

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

**File:** multisig2/src/lib.rs (L341-353)
```rust
    /// Add member to the list. Adds access key if member is key based.
    fn add_member(&mut self, promise: Promise, member: MultisigMember) -> Promise {
        self.members.insert(&member.clone().into());
        match member {
            MultisigMember::AccessKey { public_key } => promise.add_access_key(
                public_key.into(),
                DEFAULT_ALLOWANCE,
                env::current_account_id(),
                MULTISIG_METHOD_NAMES.to_string(),
            ),
            MultisigMember::Account { account_id: _ } => promise,
        }
    }
```

**File:** multisig-factory/src/lib.rs (L29-49)
```rust
    pub fn create(
        &mut self,
        name: AccountId,
        members: Vec<MultisigMember>,
        num_confirmations: u64,
    ) -> Promise {
        let account_id = format!("{}.{}", name, env::current_account_id());
        Promise::new(account_id)
            .create_account()
            .deploy_contract(CODE.to_vec())
            .transfer(env::attached_deposit())
            .function_call(
                b"new".to_vec(),
                json!({ "members": members, "num_confirmations": num_confirmations })
                    .to_string()
                    .as_bytes()
                    .to_vec(),
                0,
                env::prepaid_gas() - CREATE_CALL_GAS,
            )
    }
```
