from datetime import datetime, timedelta, timezone
import jwt

private_key = open("jwt_private.pem").read()
public_key = open("jwt_public.pem").read()

now = datetime.now(timezone.utc)
token = jwt.encode(
    {"sub": "123", "role": "user", "type": "access",
     "iss": "legal-assistant-user-service", "iat": now, "exp": now + timedelta(minutes=15)},
    private_key, algorithm="RS256",
)
print("TOKEN:", token)

payload = jwt.decode(token, public_key, algorithms=["RS256"], issuer="legal-assistant-user-service")
print("DECODE OK:", payload)