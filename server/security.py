from passlib.context import CryptContext

# Usa pbkdf2_sha256 (nativo do passlib, sem dependência externa do bcrypt)
pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")

def get_password_hash(password: str) -> str:
    # pbkdf2_sha256 aceita passwords de qualquer tamanho
    return pwd_context.hash(password)

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)
