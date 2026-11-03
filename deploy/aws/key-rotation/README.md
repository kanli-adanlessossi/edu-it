# Bedrock key rotation

Lambda, schedule, role and alarm for the production Bedrock credential (ADR 0007 addendum 1,
ai-content task 4.14). Deploying is an owner action and is not part of CI.

1. Create the secret named by `LLM_PROVIDER_API_KEY_SECRET_ID` (any initial string).
2. Package: `pip install -r requirements.txt -t build/ && cp handler.py build/`, zip `build/`, upload to S3.
3. Deploy `template.yaml` with `SecretArn`, `ModelResourceArns`, `CodeBucket`, `CodeKey` (and optionally `AlarmTopicArn`).
4. Invoke the function once and check that the secret has a new version. Only the key fingerprint is logged.

The key is valid for the shorter of 12 hours and the role credentials' expiry. The 6-hour schedule leaves one missed run of margin.
