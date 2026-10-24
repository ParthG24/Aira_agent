"""User profile and identity schemas for authorization and IDOR protection."""
from typing import Optional, List
from pydantic import BaseModel


class UserProfile(BaseModel):
    name: str
    email: Optional[str] = None
    authorized_orders: List[str] = []


PRESET_USERS = {
    "Priya Sharma": UserProfile(name="Priya Sharma", email="priya@example.com", authorized_orders=["ORD-101"]),
    "Rahul Verma": UserProfile(name="Rahul Verma", email="rahul@example.com", authorized_orders=["ORD-102"]),
    "Ananya Patel": UserProfile(name="Ananya Patel", email="ananya@example.com", authorized_orders=["ORD-103"]),
}
