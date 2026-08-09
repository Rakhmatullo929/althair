# Secret rotation required

The imported archive previously contained a populated environment file and static token fixtures.
Treat every credential that may have appeared there as compromised, even if it is not present in
this workspace now.

The owner must rotate these categories in their provider consoles and deployment platform:

- OpenAI and other API-provider keys;
- PostgreSQL credentials;
- Twilio, SendGrid, and Stripe credentials;
- webhook shared secrets and static API tokens;
- Django/JWT/signing secrets;
- cloud-storage access credentials;
- any static fixture tokens or passwords.

## Field-encryption key

Do not simply replace `FIELD_ENCRYPTION_KEY`: existing encrypted columns would become unreadable.
Use a controlled rotation instead:

1. back up and test restore of the database;
2. keep the old key available only to a one-off audited migration process;
3. generate a new Fernet key in the secret manager;
4. decrypt each encrypted value with the old key and immediately re-encrypt with the new key in
   bounded transactions;
5. verify record counts and sampled reads without logging plaintext;
6. deploy every application process with the new key, then revoke the old key;
7. retain only audit metadata, never plaintext or the key values.

`scripts/check-secrets.sh` uses gitleaks when installed and otherwise a conservative redacted
pattern scanner. Pattern scanning cannot detect every novel, split, or encoded secret, so provider
rotation and repository-host secret scanning remain required.
