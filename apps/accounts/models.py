"""User model wired to the existing `users` table.

Schema notes (matches the Go service's CREATE TABLE in internal/database/schema.go):
  - id              VARCHAR(36)  PRIMARY KEY (UUID v4 string with dashes)
  - username        VARCHAR(50)  UNIQUE
  - email           VARCHAR(255) NULL
  - password_hash   VARCHAR(255)        — bcrypt $2a$10$...
  - display_name    VARCHAR(100) NULL
  - avatar_url      VARCHAR(500) NULL
  - google_sub      VARCHAR(255) NULL UNIQUE — added by migration 0004
  - created_at      DATETIME     DEFAULT CURRENT_TIMESTAMP
  - updated_at      DATETIME     DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP

Django admin-required columns (is_staff, is_superuser, last_login) are added by
migration 0002 — they don't exist in the production table yet.
"""

from __future__ import annotations

from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.contrib.auth.models import PermissionsMixin
from django.db import models

from apps.core.ids import new_id


class UserManager(BaseUserManager):
    use_in_migrations = True

    def _create_user(self, username: str, password: str | None, **extra_fields):
        if not username:
            raise ValueError("Username is required")
        user = self.model(username=username, **extra_fields)
        if not user.id:
            user.id = new_id()
        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()
        user.save(using=self._db)
        return user

    def create_user(self, username: str, password: str | None = None, **extra_fields):
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        return self._create_user(username, password, **extra_fields)

    def create_superuser(self, username: str, password: str | None = None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        if extra_fields.get("is_staff") is not True:
            raise ValueError("Superuser must have is_staff=True.")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("Superuser must have is_superuser=True.")
        return self._create_user(username, password, **extra_fields)


class User(AbstractBaseUser, PermissionsMixin):
    id = models.CharField(primary_key=True, max_length=36, default=new_id, editable=False)
    username = models.CharField(max_length=50, unique=True)
    email = models.CharField(max_length=255, null=True, blank=True)
    # Hash column is named `password_hash` in MySQL; AbstractBaseUser's `password`
    # field name is what every check_password()/set_password() call expects.
    password = models.CharField(max_length=255, db_column="password_hash")
    display_name = models.CharField(max_length=100, null=True, blank=True)
    avatar_url = models.CharField(max_length=500, null=True, blank=True)
    # Google's `sub` claim once the account has signed in with Google — the identity key
    # that survives the user changing their Google address. NULL for password-only rows.
    google_sub = models.CharField(max_length=255, null=True, blank=True, unique=True)
    is_staff = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = UserManager()

    USERNAME_FIELD = "username"
    REQUIRED_FIELDS: list[str] = []

    class Meta:
        db_table = "users"
        managed = True

    def __str__(self) -> str:
        return self.username
