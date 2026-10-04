"""Validated inputs for the local workspace API."""
import re
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Contact(Input):
    name: str = Field(min_length=1, max_length=100)
    email: str = Field(min_length=3, max_length=254)

    @field_validator("email")
    @classmethod
    def valid_email(cls, value):
        if not re.fullmatch(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\.[A-Za-z]{2,63}", value):
            raise ValueError("Indirizzo email non valido")
        return value.lower()


class CompanyInput(Input):
    name: str = Field(min_length=1, max_length=120)
    sector: str = Field(default="", max_length=160)
    description: str = Field(default="", max_length=2000)
    signature: str = Field(default="", max_length=600)


class Preferences(Input):
    priority_contacts: list[Contact] = Field(min_length=1, max_length=4)
    hour: int = Field(default=9, ge=0, le=23, strict=True)
    minute: int = Field(default=0, ge=0, le=59, strict=True)
    timezone: Literal["Europe/Rome", "UTC"] = "Europe/Rome"

    @model_validator(mode="after")
    def unique_contacts(self):
        emails = [contact.email for contact in self.priority_contacts]
        if len(set(emails)) != len(emails):
            raise ValueError("I contatti prioritari devono avere email diverse")
        return self


class Authorization(Input):
    read: bool = Field(default=False, strict=True)
    draft: bool = Field(default=False, strict=True)
    send: bool = Field(default=False, strict=True)


class Activation(Preferences):
    authorization: Authorization


class Action(Input):
    action: Literal["pause", "resume", "deactivate", "run"]


class DemoFailure(Input):
    kind: Literal["temporary", "expired", "clear"]


class ChatInput(Input):
    message: str = Field(min_length=1, max_length=2000)
