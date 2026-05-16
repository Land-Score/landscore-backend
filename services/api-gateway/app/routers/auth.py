from fastapi import APIRouter, Request
from pydantic import BaseModel, EmailStr

router = APIRouter()


class RegisterBody(BaseModel):
    email: EmailStr
    password: str
    name: str


class LoginBody(BaseModel):
    email: EmailStr
    password: str


@router.post("/register")
async def register(body: RegisterBody):
    # TODO: call auth-service via gRPC
    return {"access_token": "stub", "token_type": "bearer"}


@router.post("/login")
async def login(body: LoginBody):
    return {"access_token": "stub", "token_type": "bearer"}


@router.post("/refresh")
async def refresh(request: Request):
    return {"access_token": "stub", "token_type": "bearer"}


@router.get("/me")
async def me(request: Request):
    return {"user_id": request.state.user_id}
