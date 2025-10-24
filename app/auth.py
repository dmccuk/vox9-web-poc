from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from app.settings import settings

security = HTTPBasic()

def single_user_guard(credentials: HTTPBasicCredentials = Depends(security)):
    correct_username = credentials.username == settings.BASIC_USER
    correct_password = credentials.password == settings.BASIC_PASS
    if not (correct_username and correct_password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
