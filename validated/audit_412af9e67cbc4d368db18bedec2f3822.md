### Title
Weak password policy allows trivially guessable passwords for node API users - ([File: core/utils/password.go])

### Summary
The chainlink node's user password validator, `utils.VerifyPasswordComplexity`, only enforces a minimum length (16 characters) and a check against leading/trailing whitespace and disallowed substrings (e.g., the user's email). [1](#0-0)  It does not reject low-entropy patterns such as repeated characters, sequential digits/letters, or common keyboard patterns — the exact class of weak passwords called out in the external report (`0123456789`, `000000`, `aaaaaa`, `abcdef`).

### Finding Description
`VerifyPasswordComplexity` is the single password-strength gate used across the node's authentication surface: user creation, password updates, and keystore/database password validation. [2](#0-1)  Because it only checks length and a small blocklist, a 16+ character password consisting entirely of a repeated character (e.g. `aaaaaaaaaaaaaaaa`) or a simple sequential pattern (e.g. `0123456789012345`, `abcdefghijklmnop`) passes validation without any complexity, character-class diversity, or dictionary/pattern check.

This validator is invoked directly in the internet-facing node API:
- `UserController.Create`, which creates new API users and validates the supplied password with `utils.VerifyPasswordComplexity(request.Password, request.Email)`. [3](#0-2) 
- `UserController.UpdatePassword`, which lets an authenticated session user change their own password, again gated only by `utils.VerifyPasswordComplexity(request.NewPassword, user.Email)`. [4](#0-3) 
- `sessions.ValidateAndHashPassword`, the single point of logic wrapping this check before hashing and persisting the password. [5](#0-4) 

The existing test suite explicitly confirms only length/whitespace/blocklist are checked — there is no test (or code path) rejecting sequential or repeated-character passwords. [6](#0-5) 

### Impact Explanation
Any node API user (of any role: `view`, `run`, `edit`, or `admin`) can set their own account password, via `/v2/user/password`, to a low-entropy, easily guessed value that still satisfies the length requirement (e.g., a repeated or sequential 16+ character string). This weakens the effective security margin of the node's authentication and increases susceptibility to online/offline brute-force or credential-stuffing attacks against the node's Operator UI/API, potentially leading to account compromise and, depending on the compromised user's role, unauthorized job management or fund-related actions exposed through the node API.

### Likelihood Explanation
Likelihood is limited by the fact that account creation (`UserController.Create`) is typically restricted to admin-level operators, but the self-service password-change endpoint (`UserController.UpdatePassword`) is reachable by any already-authenticated user regardless of role, and the same weak-password acceptance applies there. An attacker who obtains any valid session (e.g., via phishing, session leakage, or a lower-privileged compromised account) could reset the password to a trivially memorable weak value to retain long-term, easily-repeatable access.

### Recommendation
Strengthen `VerifyPasswordComplexity` in `core/utils/password.go` to reject low-entropy patterns in addition to the existing length/whitespace/blocklist checks — e.g., disallow passwords composed of a single repeated character, simple sequential runs (`0123456789`, `abcdefgh`), and common weak substrings, or integrate an entropy/dictionary-based strength estimator (such as zxcvbn) for both the `Create` and `UpdatePassword` code paths.

### Proof of Concept
1. Authenticate as any existing node API user (any role).
2. Send `PATCH /v2/user/password` with body:
```json
{"oldPassword": "<current password>", "newPassword": "aaaaaaaaaaaaaaaa"}
```
3. The request succeeds (HTTP 200) because `utils.VerifyPasswordComplexity` only checks length (≥16) and disallowed substrings — a repeated-character password passes. [7](#0-6)  The same weak value also passes during `POST /v2/users` account creation via `utils.VerifyPasswordComplexity(request.Password, request.Email)`. [8](#0-7)

### Citations

**File:** core/utils/password.go (L16-26)
```go
// PasswordComplexityRequirements defines the complexity requirements message
// Note that adding an entropy requirement wouldn't add much, since a 16
// character password already has an entropy score of 75 even if it's all
// lowercase characters
const PasswordComplexityRequirements = `
Must have a length of 16-50 characters
Must not comprise:
	Leading or trailing whitespace (note that a trailing newline in the password file, if present, will be ignored)
`

const MinRequiredLen = 16
```

**File:** core/utils/password.go (L44-70)
```go
func VerifyPasswordComplexity(password string, disallowedStrings ...string) (merr error) {
	errMsg := ErrMsgHeader
	var stringErrs []string

	if LeadingWhitespace.MatchString(password) || TrailingWhitespace.MatchString(password) {
		stringErrs = append(stringErrs, ErrWhitespace.Error())
	}

	if len(password) < MinRequiredLen {
		stringErrs = append(stringErrs, fmt.Sprintf("password is less than %d characters long", MinRequiredLen))
	}

	for _, s := range disallowedStrings {
		if strings.Contains(strings.ToLower(password), strings.ToLower(s)) {
			stringErrs = append(stringErrs, fmt.Sprintf("password may not contain: %q", s))
		}
	}

	if len(stringErrs) > 0 {
		for _, stringErr := range stringErrs {
			errMsg = fmt.Sprintf("%s	%s\n", errMsg, stringErr)
		}
		merr = errors.New(errMsg)
	}

	return
}
```

**File:** core/web/user_controller.go (L52-80)
```go
func (u *UserController) Create(c *gin.Context) {
	ctx := c.Request.Context()
	type newUserRequest struct {
		Email    string `json:"email"`
		Password string `json:"password"`
		Role     string `json:"role"`
	}

	var request newUserRequest
	if err := c.ShouldBindJSON(&request); err != nil {
		jsonAPIError(c, http.StatusUnprocessableEntity, err)
		return
	}

	userRole, err := clsession.GetUserRole(request.Role)
	if err != nil {
		jsonAPIError(c, http.StatusBadRequest, err)
		return
	}

	if verr := clsession.ValidateEmail(request.Email); verr != nil {
		jsonAPIError(c, http.StatusBadRequest, verr)
		return
	}

	if verr := utils.VerifyPasswordComplexity(request.Password, request.Email); verr != nil {
		jsonAPIError(c, http.StatusBadRequest, verr)
		return
	}
```

**File:** core/web/user_controller.go (L201-233)
```go
// UpdatePassword changes the password for the current User.
func (u *UserController) UpdatePassword(c *gin.Context) {
	ctx := c.Request.Context()
	var request UpdatePasswordRequest
	if err := c.ShouldBindJSON(&request); err != nil {
		jsonAPIError(c, http.StatusUnprocessableEntity, err)
		return
	}

	sessionUser, ok := webauth.GetAuthenticatedUser(c)
	if !ok {
		jsonAPIError(c, http.StatusInternalServerError, errors.New("failed to obtain current user from context"))
		return
	}
	user, err := u.App.AuthenticationProvider().FindUser(ctx, sessionUser.Email)
	if err != nil {
		if errors.Is(err, clsession.ErrNotSupported) {
			jsonAPIError(c, http.StatusBadRequest, errUnsupportedForAuth)
			return
		}
		u.App.GetLogger().Errorf("failed to obtain current user record: %s", err)
		jsonAPIError(c, http.StatusInternalServerError, errors.New("unable to update password"))
		return
	}
	if !utils.CheckPasswordHash(request.OldPassword, string(user.HashedPassword)) {
		u.App.GetAuditLogger().Audit(audit.PasswordResetAttemptFailedMismatch, map[string]any{"user": user.Email})
		jsonAPIError(c, http.StatusConflict, errors.New("old password does not match"))
		return
	}
	if err := utils.VerifyPasswordComplexity(request.NewPassword, user.Email); err != nil {
		jsonAPIError(c, http.StatusUnprocessableEntity, err)
		return
	}
```

**File:** core/sessions/user.go (L68-83)
```go
// ValidateAndHashPassword is the single point of logic for user password validations
func ValidateAndHashPassword(plainPwd string) (string, error) {
	if err := utils.VerifyPasswordComplexity(plainPwd); err != nil {
		return "", pkgerrors.Wrapf(err, "password insufficiently complex:\n%s", utils.PasswordComplexityRequirements)
	}
	if len(plainPwd) > MaxBcryptPasswordLength {
		return "", pkgerrors.Errorf("must enter a password less than %v characters", MaxBcryptPasswordLength)
	}

	pwd, err := utils.HashPassword(plainPwd)
	if err != nil {
		return "", err
	}

	return pwd, nil
}
```

**File:** core/utils/password_test.go (L13-51)
```go
func TestVerifyPasswordComplexity(t *testing.T) {
	t.Parallel()

	tests := []struct {
		password       string
		mustNotcontain string
		errors         []error
	}{
		{"thispasswordislongenough", "", []error{}},
		{"exactlyrightlen1", "", []error{}},
		{"notlongenough", "", []error{errors.New("password is less than 16 characters long")}},
		{"whitespace in password is ok", "", []error{}},
		{"\t leading whitespace not ok", "", []error{utils.ErrWhitespace}},
		{"trailing whitespace not ok\n", "", []error{utils.ErrWhitespace}},
		{"contains bad string", "bad", []error{errors.New("password may not contain: \"bad\"")}},
		{"contains bAd string 2", "bad", []error{errors.New("password may not contain: \"bad\"")}},
	}

	for _, test := range tests {
		t.Run(test.password, func(t *testing.T) {
			t.Parallel()

			var disallowedStrings []string
			if test.mustNotcontain != "" {
				disallowedStrings = []string{test.mustNotcontain}
			}
			err := utils.VerifyPasswordComplexity(test.password, disallowedStrings...)
			if len(test.errors) == 0 {
				assert.NoError(t, err)
			} else {
				assert.Error(t, err)
				assert.ErrorContains(t, err, utils.ErrMsgHeader)
				for _, subErr := range test.errors {
					assert.ErrorContains(t, err, subErr.Error())
				}
			}
		})
	}
}
```
